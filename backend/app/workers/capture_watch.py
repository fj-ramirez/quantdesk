"""Container entrypoint for the `capture-watch` worker (T104).

The one job this stack was missing. September quarterly opex week is absent from
`gex.snapshots` — five open sessions, the whole 28-symbol universe — and nothing said so for
twelve days. `GET /api/gex/health/capture` had computed exactly the right thing since T29 and
was never polled.

**Its own container, not a job inside `gex-capture`.** That is the whole point and it is worth
stating plainly: a watchdog living inside the process it watches dies with it, and reproduces
the silence it exists to break. Separating them costs one small container and removes the
failure mode entirely for everything short of the host going down.

**It does not close the dead-man's-switch problem, and says so.** If this container dies, the
alert dies with it. The honest mitigation on a single-user homeserver is the heartbeat below:
with `CAPTURE_WATCH_HEARTBEAT_DAYS` set and a channel configured, it posts a short "capture
healthy" note on a slow cadence, so silence itself becomes a signal a human notices. An
external check from off the box is strictly better and is out of scope here — noted rather
than pretended away.

Structurally this is `app/workers/research_search.py` with a different scheduler: wait for the
schema, build, start, log the job ids, run until a signal, shut down without waiting.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import inspect

from app.core import notify
from app.core.config import settings
from app.core.db import get_engine, get_session_factory
from app.core.schemas import SCHEMA_GEX
from app.core.version import record_service_version
from app.modules.gex.jobs.outage import check_capture_outage

logger = logging.getLogger("app.workers.capture_watch")

#: Post a "still healthy" note every N days when a channel is configured. 0 disables it.
#: Off by default: an unsolicited periodic message is a choice, and on a channel the user did
#: not have to set up at all it would be presumptuous.
HEARTBEAT_DAYS = int(getattr(settings, "CAPTURE_WATCH_HEARTBEAT_DAYS", 0) or 0)

_last_alert: dt.date | None = None
_last_heartbeat: dt.date | None = None


def run_check(*, now: dt.datetime | None = None) -> None:
    """One pass. Never raises — a watchdog that dies on a bad query stops watching."""
    global _last_alert, _last_heartbeat
    moment = now or dt.datetime.now(dt.UTC)
    today = moment.astimezone(dt.UTC).date()

    try:
        with get_session_factory()() as session:
            report = check_capture_outage(session, today=today)
    except Exception:
        logger.exception("capture_watch: check failed; will retry on the next interval")
        return

    if report.is_outage:
        # Once per day, not once per interval. An alert repeated hourly through a multi-day
        # outage is an alert the user learns to swipe away, which is how the next one is missed.
        if _last_alert == today:
            logger.info("capture_watch: outage still open, already alerted today")
            return
        result = notify.send(report.summary(), level=logging.ERROR)
        _last_alert = today
        if not result.delivered:
            logger.warning(
                "capture_watch: outage detected but not delivered (%s). It is in the log above.",
                result.reason,
            )
        return

    _last_alert = None
    if HEARTBEAT_DAYS > 0 and (
        _last_heartbeat is None or (today - _last_heartbeat).days >= HEARTBEAT_DAYS
    ):
        notify.send(report.summary(), level=logging.INFO)
        _last_heartbeat = today


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.TZ)
    scheduler.add_job(
        run_check,
        IntervalTrigger(minutes=settings.CAPTURE_WATCH_INTERVAL_MINUTES),
        id="capture_watch",
        name=f"Capture outage check (every {settings.CAPTURE_WATCH_INTERVAL_MINUTES} min)",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def _wait_for_schema(*, timeout_seconds: float = 120.0) -> bool:
    """Wait until `gex.snapshots` exists. Schema-qualified, per T76's expensive lesson: an
    unqualified `has_table` looks in `public`, finds nothing, and waits out the whole timeout
    against a database that is perfectly migrated."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            if inspect(get_engine()).has_table("snapshots", schema=SCHEMA_GEX):
                return True
        except Exception as exc:  # noqa: BLE001 - postgres may simply not be up yet
            logger.debug("capture_watch: schema not ready yet (%s)", type(exc).__name__)
        await asyncio.sleep(2.0)
    return False


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    record_service_version("capture-watch")

    # Said at boot, every boot: the configured mode must never be something anyone has to infer
    # from the absence of messages.
    logger.info("capture_watch: %s", notify.notifier_status())
    if HEARTBEAT_DAYS:
        logger.info("capture_watch: heartbeat every %d day(s)", HEARTBEAT_DAYS)

    if not await _wait_for_schema():
        logger.error("capture_watch: %s.snapshots did not appear; exiting", SCHEMA_GEX)
        return 1

    scheduler = build_scheduler()
    scheduler.start()
    logger.info(
        "capture_watch: scheduler started; jobs=%s", [job.id for job in scheduler.get_jobs()]
    )
    # One immediate pass, so a restart answers "are we whole?" now rather than in an hour.
    run_check()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    await stop.wait()

    scheduler.shutdown(wait=False)
    logger.info("capture_watch: stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
