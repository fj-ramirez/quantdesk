"""Container entrypoint for the `terminal-ingest` worker (T79).

Runs the nightly cross-asset sequence and nothing else. On `internal` only; it never serves a
request.

**The sequence is ordered, and the order is the point.** Each step consumes what the one before
it stored:

1. `ingest`  — fetch every source into `observations`. Nothing downstream has inputs without it.
2. `derive`  — compute the derived series (breakevens, spreads, ratios) from what was just
               ingested. A derived value computed before its inputs land would be wrong or
               absent, not late.
3. `fomc`    — refresh the meeting calendar, which `policy` reads.
4. `edges`   — the transmission graph's empirical half, which needs the full panel.

**`policy` is deliberately not in that list any more (T90), and its absence is load-bearing.**
It was step 4 until 2026-09-21, and it could never have worked here: `cli.py`'s `policy`
subcommand takes a *required positional* settlement file, because CME's Data Terms of Use
prohibit fetching their settlements automatically. `cli.main(["policy"])` therefore never
reached its handler -- argparse raised `SystemExit(2)` for the missing argument, which is a
`BaseException` and slipped straight through `_run_sequence`'s `except Exception`. So the
sequence died at step 4 every night and `edges` never ran: on 2026-09-21 the scheduled 03:00
run stopped after `derive`, and `edge_stats` still carried the previous day's *manual* run.
See `plans/decision-inputs/00-nightly-abort.md`; T96 owns finding a source that would let the
implied path run unattended.

`board`, `regime`, `factors` and `brief` are **not** here: they compute on read from stored
observations, so the API serves them live at whatever `as_of` the screen asks for. Precomputing
them nightly would mean the board could only be viewed at the moments a cron job happened to
run, which is the opposite of what a point-in-time terminal is for.

Same structure as `gex_capture` and `research_search`: wait for the schema, build, start, log,
run until a signal, shut down without waiting. Two module-specific decisions:

* **No catch-up.** Unlike GEX's unbackfillable Cboe snapshot, every source here serves history:
  FRED and ALFRED serve vintages, Treasury and CFTC serve archives. A missed night refills on
  the next run, so catch-up machinery would add a failure mode and buy nothing.
* **`max_instances=1` and a generous misfire grace**, for the same reasons the research worker
  has them — a run is minutes of HTTP against five sources, and APScheduler's one-second
  default grace silently skips a job that fires late on a busy host.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import inspect

from app.core.config import settings
from app.core.db import get_engine
from app.core.schemas import SCHEMA_TERMINAL
from app.core.version import record_service_version

logger = logging.getLogger("app.workers.terminal_ingest")

__all__ = ["INGEST_JOB_ID", "build_terminal_scheduler", "main", "run", "terminal_ingest_job"]

INGEST_JOB_ID = "terminal_ingest"

SCHEMA_WAIT_SECONDS = 60.0
SCHEMA_POLL_SECONDS = 2.0
SCHEMA_SENTINEL_TABLE = "observations"

#: An hour. See the module docstring.
MISFIRE_GRACE_SECONDS = 3600

#: The ordered nightly sequence. A list, not a set, because the order is semantic.
SEQUENCE: tuple[str, ...] = ("ingest", "derive", "fomc", "edges")

#: Steps that exist as CLI commands but cannot run unattended, and why. Logged once per run at
#: WARNING so the gap is stated rather than inferred from an absence -- which is exactly how
#: T90's bug survived: the implied policy path silently stopped updating and nothing said so.
#:
#: Keeping the reason here rather than in a comment means the operator reading `compose logs`
#: gets it, not just the next person to read this file.
UNSCHEDULED_STEPS: tuple[tuple[str, str], ...] = (
    (
        "policy",
        (
            "needs an operator-supplied CME ZQ settlement file (the `settlements` positional "
            "in `app.modules.terminal.cli`), and CME's Data Terms of Use prohibit fetching it "
            "automatically -- so the implied policy path does not update on a schedule. Run "
            "it by hand with a settlement file, or see T96"
        ),
    ),
)


def _run_sequence() -> None:
    """Run the nightly steps in order, through the CLI's own entry point.

    `cli.main([step])` rather than reaching for the `cmd_*` functions: the CLI builds its parser
    inside `main`, and every handler expects the fully-populated `argparse.Namespace` that
    parser produces (defaults included). Going through the front door means the worker runs
    *exactly* what `python -m app.modules.terminal.cli ingest` runs -- which is the property the
    brief asks for, and the one that makes a hand-run debugging session trustworthy.

    Each step is attempted even if an earlier one failed, and the failure is logged rather than
    raised. The steps are only loosely coupled -- a FRED outage should not stop the CFTC data
    landing or the edges being recomputed on yesterday's panel -- and a worker that abandoned
    the night on the first bad source would turn one vendor's bad day into a total gap.

    **T90: the guard catches `SystemExit` as well as `Exception`, and that is the difference
    between the paragraph above being true and merely being intended.** `cli.main` goes through
    argparse, and argparse's answer to a bad invocation is to raise `SystemExit` -- which
    inherits from `BaseException`, not `Exception`, so the original guard let it through and
    ended the night. `cli.main` no longer raises it (it returns the code instead), so this is
    belt and braces: the next step added with a required argument, or any library that decides
    to `sys.exit` on a bad input, must not be able to silently truncate the sequence again.

    The width is deliberate and stops there. `except BaseException` would also swallow
    `KeyboardInterrupt` and `asyncio.CancelledError`, making the worker un-interruptible and
    un-shutdownable -- trading a silent data gap for a container that ignores SIGTERM.
    """
    from app.modules.terminal import cli

    for step, reason in UNSCHEDULED_STEPS:
        logger.warning("terminal %s: not scheduled -- %s", step, reason)

    for step in SEQUENCE:
        logger.info("terminal %s: starting", step)
        try:
            code = cli.main([step])
            if code == 0:
                logger.info("terminal %s: finished", step)
            else:
                logger.error("terminal %s: exited %s", step, code)
        except (Exception, SystemExit) as exc:
            logger.exception(
                "terminal %s: failed with %s; continuing with the rest of the sequence",
                step,
                type(exc).__name__,
            )


async def terminal_ingest_job() -> None:
    """One nightly sequence, off the event loop.

    `to_thread` for the same reason the research cycle uses it: this is blocking HTTP against
    five sources plus pandas work, and running it in the coroutine would stall the scheduler
    that has to fire it.
    """
    await asyncio.to_thread(_run_sequence)


def build_terminal_scheduler() -> AsyncIOScheduler:
    """The scheduler with the nightly job registered. Does not start it."""
    tz = ZoneInfo(settings.TZ)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        terminal_ingest_job,
        trigger=CronTrigger.from_crontab(settings.XA_INGEST_CRON, timezone=tz),
        id=INGEST_JOB_ID,
        name="xactx nightly ingest sequence",
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        max_instances=1,
        replace_existing=True,
    )
    logger.info("terminal ingest scheduled: %s", settings.XA_INGEST_CRON)
    return scheduler


async def _wait_for_schema(*, timeout: float = SCHEMA_WAIT_SECONDS) -> bool:
    """Block until `terminal.observations` exists, or `timeout` elapses. Never raises.

    Schema-qualified, which is T76's lesson applied for the third time: `has_table` with no
    schema looks in `public`, finds nothing, and the worker waits out its whole timeout against
    a perfectly migrated database.
    """
    logger.info("waiting for the %r table (up to %.0fs)", SCHEMA_SENTINEL_TABLE, timeout)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    engine = get_engine()

    while True:
        try:
            found = await asyncio.to_thread(
                lambda: inspect(engine).has_table(SCHEMA_SENTINEL_TABLE, schema=SCHEMA_TERMINAL)
            )
            if found:
                logger.info("%r is present", SCHEMA_SENTINEL_TABLE)
                return True
        except Exception as exc:  # noqa: BLE001 - any connect failure is "not ready yet"
            logger.info("waiting for the database: %s", exc.__class__.__name__)

        if loop.time() >= deadline:
            logger.warning(
                "schema still missing table %r after %.0fs; starting anyway",
                SCHEMA_SENTINEL_TABLE,
                timeout,
            )
            return False
        await asyncio.sleep(SCHEMA_POLL_SECONDS)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def run(*, stop: asyncio.Event | None = None, wait_for_schema: bool = True) -> None:
    if wait_for_schema:
        await _wait_for_schema()

    # Which build this container is running, written where the API can read it: the worker
    # serves no HTTP, so `GET /health` has no other way to report it. Never raises -- see
    # `app.core.version.record_service_version`; a version file is not worth a capture.
    record_service_version("terminal-ingest")


    stop = stop if stop is not None else asyncio.Event()
    scheduler = build_terminal_scheduler()
    scheduler.start()
    logger.info(
        "terminal worker started; jobs=%s", [job.id for job in scheduler.get_jobs()]
    )
    try:
        await stop.wait()
    finally:
        logger.info("shutting down")
        scheduler.shutdown(wait=False)


async def main() -> None:
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    await run(stop=stop)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())
