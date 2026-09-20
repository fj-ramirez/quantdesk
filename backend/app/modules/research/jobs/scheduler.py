"""APScheduler wiring for the research search cycle (T77).

Same shape as `app/modules/gex/jobs/scheduler.py`, and deliberately so: one scheduling
mechanism in the repo rather than two. `build_research_scheduler()` constructs the scheduler
and registers the job; it never starts one, so a test can inspect the registered trigger
without waiting on a clock or touching the network.

**What this replaces.** The standalone repo had two schedules -- a Windows Task Scheduler entry
at 02:00 and a systemd unit running `nightly.py --loop 60`. Inside Docker neither exists, and
`--loop` is a `while True` with a `sleep` in it: no misfire policy, no overrun protection, and
no way to ask what it is about to do. Here the trigger shape is configuration:

| `RESEARCH_SCHEDULE` | trigger                                              |
|---------------------|------------------------------------------------------|
| `cron` (default)    | `RESEARCH_CRON`, default `0 2 * * *` in `settings.TZ` |
| `interval`          | `RESEARCH_INTERVAL_MINUTES`, default 60              |
| `off`               | nothing registered                                   |

Three job policies, each of which earns its place:

- **`max_instances=1`.** A cycle that overruns its next fire must never start a second
  alongside itself. Two concurrent searches would sample the same combinations and race each
  other's writes for no gain.
- **`coalesce=True`.** Four fires missed while the host was down are one run when it returns,
  not four.
- **`misfire_grace_time` set explicitly and generously.** APScheduler's default drops a job
  fired more than one second late, which on a busy host means a nightly cycle that silently
  never runs. An hour is the right order for a job that takes tens of minutes anyway.

**There is deliberately no catch-up job.** GEX has one because a missed 16:20 capture is gone
forever -- the free Cboe feed serves only "now". A missed research cycle costs nothing: the
registry already remembers every combination tried, so the next cycle simply continues from
there. Adding catch-up here would import a failure mode and buy nothing.
"""

from __future__ import annotations

import asyncio
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.modules.research.config import load_config
from app.modules.research.nightly import run_cycle

__all__ = [
    "SEARCH_JOB_ID",
    "build_research_scheduler",
    "research_cycle_job",
]

logger = logging.getLogger("app.modules.research.jobs.scheduler")

SEARCH_JOB_ID = "research_search"

#: An hour. See the module docstring: the default of one second turns a slightly-late fire into
#: a silently skipped night.
MISFIRE_GRACE_SECONDS = 3600


async def research_cycle_job() -> None:
    """One search cycle, off the event loop.

    `run_cycle` is heavily CPU-bound (thousands of backtests) and does blocking I/O (parquet
    reads, HTTP data updates, Postgres writes). Running it directly in the coroutine would peg
    the loop and stall the scheduler itself -- APScheduler could not even fire its own next
    job. `to_thread` keeps the loop responsive; the GIL is not a concern because the numeric
    work is in pandas/numpy, which releases it.
    """
    cfg = load_config()
    logger.info("research cycle starting")
    summary = await asyncio.to_thread(run_cycle, cfg)
    logger.info(
        "research cycle finished: trials=%s noise_ceiling=%s above_ceiling=%s",
        summary.get("total_trials"),
        summary.get("noise_ceiling"),
        summary.get("candidates_above_ceiling"),
    )


def _trigger() -> CronTrigger | IntervalTrigger | None:
    """Build the configured trigger, or `None` when the schedule is switched off."""
    tz = ZoneInfo(settings.TZ)
    schedule = settings.RESEARCH_SCHEDULE.strip().lower()

    if schedule == "off":
        return None
    if schedule == "interval":
        return IntervalTrigger(minutes=settings.RESEARCH_INTERVAL_MINUTES, timezone=tz)
    if schedule == "cron":
        # `from_crontab` rather than five parsed fields: the value is written as a crontab line
        # in `.env`, and re-implementing the parsing would be a second dialect to get wrong.
        return CronTrigger.from_crontab(settings.RESEARCH_CRON, timezone=tz)
    raise ValueError(
        f"RESEARCH_SCHEDULE must be 'cron', 'interval' or 'off', not {settings.RESEARCH_SCHEDULE!r}"
    )


def build_research_scheduler() -> AsyncIOScheduler:
    """The scheduler, with the search job registered. Does not start it."""
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.TZ))
    trigger = _trigger()

    if trigger is None:
        logger.info("RESEARCH_SCHEDULE=off; no search job registered")
        return scheduler

    scheduler.add_job(
        research_cycle_job,
        trigger=trigger,
        id=SEARCH_JOB_ID,
        name="EdgeLab research search cycle",
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        max_instances=1,
        replace_existing=True,
    )
    logger.info("research search scheduled: %s", trigger)
    return scheduler
