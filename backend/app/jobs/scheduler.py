"""APScheduler wiring for the EOD capture job (PLAN.md Phase 1, TASKS.md T05).

`build_scheduler()` constructs an `AsyncIOScheduler` with `capture_eod` registered on a
Mon-Fri 16:20 America/New_York cron trigger; `app/main.py`'s FastAPI lifespan starts it. Kept
separate from `app/jobs/capture.py` (the actual fetch/persist logic) so the trigger/misfire
policy below -- the part that is easy to get subtly wrong and hard to notice being wrong -- is
readable in one place, and so tests can build a scheduler and inspect its registered job
without ever starting it (no real network, no real 16:20 wait).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.jobs.bars import update_bars_job
from app.jobs.calendar import is_trading_day
from app.jobs.capture import capture_all_symbols
from app.jobs.catchup import catch_up_missed_eod

__all__ = [
    "BARS_JOB_ID",
    "EOD_JOB_ID",
    "EXTENDED_JOB_ID",
    "SAFETY_NET_JOB_ID",
    "bars_update_job",
    "build_scheduler",
    "capture_eod_job",
    "capture_eod_safety_net_job",
    "capture_extended_job",
]

logger = logging.getLogger("app.jobs.scheduler")

EOD_JOB_ID = "capture_eod"
SAFETY_NET_JOB_ID = "capture_eod_safety_net"
BARS_JOB_ID = "bars_update"
EXTENDED_JOB_ID = "capture_extended"

# `settings.TZ` (default "America/New_York") drives both the trigger's wall-clock time and
# the weekday/holiday check inside the job -- if this were ever pointed at another zone, both
# would move together rather than one silently staying NY-local.
_TZ = ZoneInfo(settings.TZ)


async def capture_eod_job() -> None:
    """Capture every symbol in `settings.symbols`, marking each `is_eod=True`.

    Guards against firing on a closed day even though the cron trigger already restricts to
    Mon-Fri: the trigger alone does not know about holidays, and this is the belt to that
    trigger's suspenders. Wrapped in a top-level try/except as a last line of defense --
    `capture_all_symbols`/`capture_snapshot` already turn every provider and storage failure
    into a logged `CaptureResult` rather than an exception, but a scheduler job that could
    still crash on some future bug (e.g. once T09's level-computation hook is wired in at the
    end of `capture_snapshot`) would silently deregister itself in APScheduler; this makes
    that structurally impossible.
    """
    today = dt.datetime.now(_TZ).date()
    if not is_trading_day(today):
        logger.info(
            json.dumps(
                {"event": "capture_eod_skipped", "date": today.isoformat(), "reason": "not a trading day"}
            )
        )
        return
    try:
        await capture_all_symbols(settings.symbols, is_eod=True)
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("capture_eod_job: unexpected top-level failure")


async def capture_eod_safety_net_job() -> None:
    """20:00 NY guard-railed retry of the 16:20 capture (T29).

    `capture_eod_job` above fires unconditionally at 16:20 -- if the process is alive to run
    it, fine. This job exists for the case that job never got the chance to run at all: a
    process that started at 16:05 (too early for the FastAPI-lifespan startup catch-up in
    `app/jobs/catchup.py` to find anything missing yet) and then crashed, rebooted, or was
    `docker compose down`-ed at 16:21 never gets another shot at 16:20 today. `_TZ`-local
    20:00 is late enough that almost any same-evening restart happens before it, early enough
    that it is still well within Cboe's "keeps serving the settled chain through the evening"
    window (see `catch_up_missed_eod`'s docstring).

    Delegates entirely to `catch_up_missed_eod`, which is what makes this safe to run even on
    a day the 16:20 job fired normally: the per-symbol "does today's is_eod row already exist"
    check makes an on-time day's 20:00 run a no-op, not a second capture.
    """
    try:
        await catch_up_missed_eod(settings.symbols)
    except Exception:  # same rationale as capture_eod_job: never take the scheduler down
        logger.exception("capture_eod_safety_net_job: unexpected top-level failure")


async def bars_update_job() -> None:
    """17:30 ET daily-bars update (T42), scheduled well after the 16:20 EOD capture and its
    20:00 safety net so the two data pipelines never compete for a slow evening connection.

    **P0 guardrail (T42 brief): a bars failure must be structurally incapable of affecting the
    option capture.** `update_bars_job` already turns every provider failure into a logged
    `BarUpdateResult` per symbol rather than raising (see its own docstring), but this wrapper
    exists for the same belt-and-suspenders reason `capture_eod_job` wraps
    `capture_all_symbols`: a future bug in this job must not be able to crash the scheduler
    thread and silently deregister *every* job on it, `capture_eod`/`capture_eod_safety_net`
    included. No trading-day guard is applied here (unlike `capture_eod_job`) -- Yahoo serves
    bars for weekends and holidays too (simply repeating the prior close), so there is no
    "closed today" case worth special-casing the way there is for a live options chain.
    """
    try:
        await update_bars_job()
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("bars_update_job: unexpected top-level failure")


async def capture_extended_job() -> None:
    """16:45 ET capture of `settings.extended_symbols` -- T47's sector/industry ETFs.

    Structurally the same job as `capture_eod_job` above (same trading-day guard, same
    `capture_all_symbols` call with `is_eod=True`, same top-level try/except), on a separate
    job id and 25 minutes later, for one reason: **this job must be structurally incapable of
    delaying or breaking the 16:20 EOD capture**, the P0 guardrail this task's brief states
    explicitly. Sharing `capture_eod_job`'s code path (even by calling it with a different
    symbol list) would put twenty-three extra sequential Cboe fetches between "the core five
    are safe" and "the job returned"; a separate job with its own trigger means the core
    capture is done and durably persisted for 25 minutes before this one even starts, and a
    bug or a slow evening here (see the plan's "likely first-contact failures": twenty-three
    captures might run long enough to approach the 17:30 bars job) cannot touch
    `capture_eod`'s result either way -- APScheduler jobs on the same scheduler share only the
    event loop, never each other's state.

    Per-symbol failures are already isolated by `capture_all_symbols`/`capture_snapshot` (one
    ETF's thin chain or a transient Cboe hiccup returns a `CaptureResult(ok=False)` for that
    symbol only); the try/except below is the same last-line-of-defense belt-and-suspenders as
    every other job in this module, not the primary isolation mechanism.
    """
    today = dt.datetime.now(_TZ).date()
    if not is_trading_day(today):
        logger.info(
            json.dumps(
                {
                    "event": "capture_extended_skipped",
                    "date": today.isoformat(),
                    "reason": "not a trading day",
                }
            )
        )
        return
    try:
        await capture_all_symbols(settings.extended_symbols, is_eod=True)
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("capture_extended_job: unexpected top-level failure")


def build_scheduler() -> AsyncIOScheduler:
    """Construct (but do not start) the scheduler with `capture_eod` registered.

    Misfire/coalesce policy -- the part that matters for "a laptop closed at 16:20 is the
    normal case for this user, not an edge case" (T05 brief):

    * `misfire_grace_time=None` -- APScheduler's default grace window (a handful of seconds)
      would treat a run that was due while the laptop was asleep as "too late" and drop it
      entirely once the process wakes up. That is exactly backwards for this app: the free
      Cboe source has no history, so a capture that runs hours late is still infinitely
      better than a capture that never runs. `None` means never drop a due run as stale.
    * `coalesce=True` -- if several fire times were missed while the process was down (e.g.
      the laptop was off for two trading days), run once on wake-up, not once per missed
      fire back-to-back. There is nothing to gain from replaying stale runs: Cboe only ever
      answers "now," so a coalesced catch-up run captures today's chain exactly like an
      on-time one would have, and the missed prior day is simply gone either way (a hole in
      the dataset the free tier can never backfill, no matter how the misfired job behaves).
    * `max_instances=1` -- a single daily job for three symbols does not need overlap
      protection in the ordinary case, but keeps a coalesced catch-up run from ever
      double-firing against an already-running one.
    """
    scheduler = AsyncIOScheduler(timezone=_TZ)
    scheduler.add_job(
        capture_eod_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=20, timezone=_TZ),
        id=EOD_JOB_ID,
        name="EOD option chain capture (SPX/SPY/QQQ/GLD/DIA)",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T29 safety net: same misfire policy as the 16:20 job, for the same reason -- a run that
    # is hours late is still better than one that never happened on this app's history-less
    # data source. See `capture_eod_safety_net_job`'s own docstring for why 20:00 and why it
    # is safe to be a no-op most days.
    scheduler.add_job(
        capture_eod_safety_net_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=20, minute=0, timezone=_TZ),
        id=SAFETY_NET_JOB_ID,
        name="EOD capture safety net (catches a missed 16:20 run)",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T42: daily bars, additive -- registered on its own job id/trigger, sharing only the
    # scheduler instance with the two option-capture jobs above, never their code path. Same
    # misfire/coalesce policy for the same reason as the EOD jobs: a bars update hours late
    # (laptop asleep, container down) is still better than one that never ran, since a missed
    # day is a permanent gap in the free Yahoo source's history this app keeps.
    scheduler.add_job(
        bars_update_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone=_TZ),
        id=BARS_JOB_ID,
        name="Daily bars update (SCAN_UNIVERSE)",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T47: extended (sector/industry ETF) option capture, additive and separate from the core
    # 16:20/20:00 jobs above -- see `capture_extended_job`'s own docstring for why 16:45 (25
    # minutes after the core capture, still an hour before the 17:30 bars job) and why it is a
    # distinct job id rather than a parameter to `capture_eod_job`. Same misfire/coalesce
    # policy as every other capture job here, for the identical reason: a run hours late still
    # beats one that never happens on this free, history-less data source.
    scheduler.add_job(
        capture_extended_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=45, timezone=_TZ),
        id=EXTENDED_JOB_ID,
        name="Extended (sector/industry ETF) option chain capture",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    return scheduler
