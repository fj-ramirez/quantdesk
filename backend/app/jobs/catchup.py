"""Startup / safety-net catch-up for a missed EOD capture (TASKS.md T29).

`app/jobs/scheduler.py`'s `misfire_grace_time=None` only rescues a run whose *process was
alive* when 16:20 NY passed (a sleeping laptop). If the backend simply was not running at
16:20 -- shut down, rebooted, `docker compose down`, mid-deploy, crashed -- APScheduler's
in-memory job store never even knew the run was due, so nothing fires, nothing logs, and
nothing errors. The free Cboe source has no history, so that day's close is gone forever.

This module is the fix, in one place, used by two callers:

* `app/main.py`'s FastAPI lifespan calls `catch_up_missed_eod` once, immediately, at process
  startup (fire-and-forget -- see that module for why it must not block boot).
* `app/jobs/scheduler.py` registers a 20:00 NY "safety net" cron that calls the exact same
  function, so a process that started at 16:05 (too early for the startup check to find
  anything missing) and then crashed at 16:21 still gets the day once the evening rolls
  around.

Both callers share one guard -- "does a NY-calendar-day `is_eod=True` row already exist for
this symbol" -- checked against the database, not against APScheduler's job history. That is
what makes starting the app twice in an evening produce one EOD row per symbol rather than
two: the second call finds the row the first one wrote and does nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.jobs.calendar import is_trading_day
from app.jobs.capture import CaptureResult, capture_snapshot, get_session_factory
from app.models.db import Snapshot
from app.providers import get_provider

__all__ = [
    "EOD_CUTOFF",
    "catch_up_missed_eod",
    "has_eod_snapshot_today",
    "last_completed_trading_day",
    "startup_catchup_job",
]

logger = logging.getLogger("app.jobs.catchup")

# `settings.TZ` (default "America/New_York"), same rationale as `app/jobs/scheduler.py`: the
# 16:20 cutoff and the trading-day check must move together with wherever this app considers
# "NY local", not silently stay pinned to America/New_York if that setting is ever changed.
_TZ = ZoneInfo(settings.TZ)

# The scheduled EOD job fires at 16:20 NY (`app/jobs/scheduler.py`). Cboe keeps serving the
# settled chain through the evening (informally verified: the close print does not move once
# the session ends), so a catch-up run any time after this, up to roughly 23:59 ET, still
# captures the correct close for the day -- it is not racing a vendor cutover the way an
# earlier catch-up would be.
EOD_CUTOFF = dt.time(16, 20)


def _ny_day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """`[start, end)` of NY calendar date `day`, as tz-aware UTC instants.

    `Snapshot.captured_at` is always UTC (`UTCDateTime`); this converts a NY *calendar* date
    into the UTC instant range that actually corresponds to it, so "today" always means NY
    today even for the hours where NY and UTC disagree on the date.
    """
    start_ny = dt.datetime.combine(day, dt.time.min, tzinfo=_TZ)
    end_ny = start_ny + dt.timedelta(days=1)
    return start_ny.astimezone(dt.UTC), end_ny.astimezone(dt.UTC)


def has_eod_snapshot_today(
    underlying: str, session_factory: sessionmaker[Session], today: dt.date
) -> bool:
    """Whether an `is_eod=True` row already exists for `underlying` on NY calendar date
    `today`. This -- a direct database check -- is the actual T29 guard; APScheduler's job
    store has no memory of a day it was never running to schedule.
    """
    start, end = _ny_day_bounds(today)
    stmt = (
        select(Snapshot.id)
        .where(
            Snapshot.underlying == underlying,
            Snapshot.is_eod.is_(True),
            Snapshot.captured_at >= start,
            Snapshot.captured_at < end,
        )
        .limit(1)
    )
    with session_factory() as session:
        return session.execute(stmt).first() is not None


def last_completed_trading_day(now: dt.datetime) -> dt.date:
    """The most recent NY trading day whose EOD capture is expected to exist by `now`.

    If today is a trading day and `now` is already past `EOD_CUTOFF`, today counts (its close
    has printed). Otherwise -- today is a weekend/holiday, or today just hasn't reached 16:20
    yet -- the answer is the most recent trading day strictly before today, since today's own
    close (if any) has not happened yet. Shared by the catch-up guard's cutoff check and by
    the health endpoint's staleness rule (`app/api/health.py`), so both use one definition of
    "what day should we have by now."
    """
    now_ny = now.astimezone(_TZ)
    today = now_ny.date()
    if is_trading_day(today) and now_ny.time() >= EOD_CUTOFF:
        return today
    return previous_trading_day(today)


def previous_trading_day(day: dt.date) -> dt.date:
    """The most recent trading day strictly before `day`."""
    candidate = day - dt.timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= dt.timedelta(days=1)
    return candidate


async def catch_up_missed_eod(
    symbols: Sequence[str],
    *,
    session_factory: sessionmaker[Session] | None = None,
    data_dir: str | Path | None = None,
    now: dt.datetime | None = None,
) -> list[CaptureResult]:
    """Capture `is_eod=True` for every symbol in `symbols` missing today's EOD row -- but only
    if today (NY) is a trading day and `now` is at or past `EOD_CUTOFF`. Idempotent: calling
    this twice in the same evening captures once, because the second call's per-symbol
    `has_eod_snapshot_today` check finds the row the first call wrote.

    Args:
        now: Injected clock for tests (`dt.datetime.now(_TZ)` when omitted). Must be
            tz-aware; naive input would silently misattribute NY/UTC dates.

    Returns:
        One `CaptureResult` per symbol actually attempted -- empty if it is not yet time, not
        a trading day, or every symbol already has today's row. Never raises: every capture
        already degrades to `CaptureResult(ok=False, ...)` (see `app.jobs.capture`), and this
        function adds no new way to fail before that point other than the DB read in
        `has_eod_snapshot_today`, which a caller wrapping this in try/except (both callers do)
        will simply log and move past.
    """
    effective_now = now if now is not None else dt.datetime.now(_TZ)
    now_ny = effective_now.astimezone(_TZ)
    today = now_ny.date()

    if not is_trading_day(today):
        logger.info(
            json.dumps(
                {"event": "catchup_skipped", "date": today.isoformat(), "reason": "not a trading day"}
            )
        )
        return []
    if now_ny.time() < EOD_CUTOFF:
        logger.info(
            json.dumps(
                {
                    "event": "catchup_skipped",
                    "date": today.isoformat(),
                    "reason": "before EOD cutoff",
                    "now": now_ny.isoformat(),
                }
            )
        )
        return []

    effective_session_factory = session_factory or get_session_factory()
    missing = [
        underlying
        for underlying in symbols
        if not has_eod_snapshot_today(underlying, effective_session_factory, today)
    ]
    if not missing:
        logger.info(
            json.dumps({"event": "catchup_noop", "date": today.isoformat(), "symbols": list(symbols)})
        )
        return []

    logger.warning(
        json.dumps({"event": "catchup_firing", "date": today.isoformat(), "symbols": missing})
    )
    provider = get_provider()
    results: list[CaptureResult] = []
    try:
        for underlying in missing:
            results.append(
                await capture_snapshot(
                    underlying,
                    is_eod=True,
                    provider=provider,
                    session_factory=effective_session_factory,
                    data_dir=data_dir,
                )
            )
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()
    return results


async def startup_catchup_job() -> None:
    """Entry point for `app/main.py`'s lifespan.

    Run as a bare `asyncio.create_task(...)` there, never awaited by anything -- so, like
    `capture_eod_job` in `app/jobs/scheduler.py`, this wraps its own body in try/except: an
    uncaught exception in a task nobody awaits does not crash the app, but it also does not
    reach the structured capture log, it just becomes an "exception was never retrieved"
    warning at garbage-collection time. Catching it here keeps the failure visible in the
    same place every other capture failure shows up.
    """
    try:
        await catch_up_missed_eod(settings.symbols)
    except Exception:  # must never take the app down, and must not vanish silently either
        logger.exception("startup_catchup_job: unexpected top-level failure")
