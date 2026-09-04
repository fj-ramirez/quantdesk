"""`GET /api/health/capture` -- "is my dataset still whole?" (TASKS.md T29).

One URL, per symbol: the last time anything was captured, the last time an `is_eod=True` row
landed, whether today's EOD row exists yet, and a `stale` flag an external monitor (or T28's
backup job) can alert on without re-deriving the trading-calendar logic itself. T28 (backups)
mentions a health endpoint too but explicitly leaves this one to T29 -- see TASKS.md's T29
scope note -- so the "EOD capture is more than one trading day old" alerting T28 describes is
implemented here, next to the calendar logic it depends on, rather than duplicated there.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.jobs.capture import get_session_factory
from app.jobs.catchup import (
    has_eod_snapshot_today,
    last_completed_trading_day,
    previous_trading_day,
)
from app.storage.repository import SnapshotRepository

__all__ = ["router"]

logger = logging.getLogger("app.api.health")

router = APIRouter(prefix="/health", tags=["health"])

# Same rationale as app/jobs/catchup.py's `_TZ`: NY calendar dates, derived from `settings.TZ`
# rather than hardcoded, so this endpoint and the catch-up guard it reads from never disagree
# about what "today" means.
_TZ = ZoneInfo(settings.TZ)


class SymbolCaptureHealth(BaseModel):
    underlying: str
    last_capture_at: dt.datetime | None
    last_eod_capture_at: dt.datetime | None
    eod_captured_today: bool
    stale: bool


class CaptureHealthResponse(BaseModel):
    generated_at: dt.datetime
    symbols: list[SymbolCaptureHealth]


def _is_stale(last_eod_date: dt.date | None, completed: dt.date) -> bool:
    """More than one trading day behind `completed` (T28's alerting rule).

    `completed` is the most recent trading day whose EOD capture is expected to exist by now
    (`last_completed_trading_day`). Being exactly one trading day behind it is normal for most
    of any given trading day -- today's own EOD row does not exist until 16:20 -- so only two
    or more missing days counts as stale: `last_eod_date` older than the trading day
    immediately before `completed`.
    """
    if last_eod_date is None:
        return True
    return last_eod_date < previous_trading_day(completed)


@router.get("/capture", response_model=CaptureHealthResponse)
def capture_health() -> CaptureHealthResponse:
    """Per-symbol capture freshness, computed fresh from the database on every call (no
    caching) -- this is a low-traffic, human-or-monitor-driven endpoint, not a hot path.
    """
    now = dt.datetime.now(dt.UTC)
    now_ny = now.astimezone(_TZ)
    today = now_ny.date()
    completed = last_completed_trading_day(now)

    session_factory = get_session_factory()
    symbols_out: list[SymbolCaptureHealth] = []
    with session_factory() as session:
        repo = SnapshotRepository(session)
        for underlying in settings.symbols:
            latest = repo.latest(underlying)
            eod_rows = repo.list(underlying, eod_only=True)
            last_eod = eod_rows[-1] if eod_rows else None
            last_eod_date = (
                last_eod.captured_at.astimezone(_TZ).date() if last_eod is not None else None
            )
            eod_today = has_eod_snapshot_today(underlying, session_factory, today)
            stale = _is_stale(last_eod_date, completed)
            if stale:
                logger.error(
                    "health.capture: %s EOD capture is stale (last=%s, expected up to=%s)",
                    underlying,
                    last_eod_date.isoformat() if last_eod_date else None,
                    completed.isoformat(),
                )
            symbols_out.append(
                SymbolCaptureHealth(
                    underlying=underlying,
                    last_capture_at=latest.captured_at if latest is not None else None,
                    last_eod_capture_at=last_eod.captured_at if last_eod is not None else None,
                    eod_captured_today=eod_today,
                    stale=stale,
                )
            )
    return CaptureHealthResponse(generated_at=now, symbols=symbols_out)
