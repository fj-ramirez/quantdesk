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
from app.jobs.calendar import is_trading_day
from app.jobs.capture import capture_all_symbols

__all__ = ["EOD_JOB_ID", "build_scheduler", "capture_eod_job"]

logger = logging.getLogger("app.jobs.scheduler")

EOD_JOB_ID = "capture_eod"

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
        name="EOD option chain capture (SPX/SPY/QQQ)",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    return scheduler
