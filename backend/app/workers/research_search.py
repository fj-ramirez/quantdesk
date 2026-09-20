"""Container entrypoint for the `research-search` worker (T77).

Runs the EdgeLab search cycle on a schedule and nothing else. It never serves a request, and it
is on the `internal` network only -- there is no reason for anything outside the stack to reach
it.

**It registers a job; it does not run `--loop`.** `app/modules/research/nightly.py` still has a
`--loop N` mode for hand use on a machine with no Docker, and starting that *inside* this
container alongside the scheduler would run two independent cycle clocks and double everything.
The two are alternatives, never a pair.

Structurally this is `app/workers/gex_capture.py` with a different scheduler and no catch-up.
The shared shape -- wait for the schema, build, start, log the job ids, run until a signal, shut
down without waiting -- is deliberate: it is the second worker, and the point at which the
pattern either becomes the house style or starts to fork. Two differences are real, and both
follow from what the data is:

* **No startup catch-up.** GEX runs one because a missed 16:20 capture is unrecoverable. A
  missed research cycle costs nothing -- the registry remembers every combination ever tried,
  so the next cycle continues from there rather than redoing anything.
* **The sentinel table is `research.trials`**, not `gex.snapshots`, and the probe is
  schema-qualified. T76's lesson, learned the expensive way: `has_table` with no schema looks
  in `public`, finds nothing, and the worker waits out its whole timeout against a database
  that is perfectly migrated.

A cycle killed mid-flight (SIGTERM on redeploy) loses only its in-flight trials. `search.py`
writes per trial and commits every 500, so the registry is consistent at every instant and the
next cycle picks up from what is stored.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy import inspect

from app.core.config import settings
from app.core.db import get_engine
from app.core.schemas import SCHEMA_RESEARCH
from app.modules.research.jobs.scheduler import build_research_scheduler

logger = logging.getLogger("app.workers.research_search")

__all__ = ["main", "run"]

#: How long to wait for the API container's `alembic upgrade head` before starting anyway.
SCHEMA_WAIT_SECONDS = 60.0
SCHEMA_POLL_SECONDS = 2.0

#: `research.trials` stands in for "migrations have run". See the module docstring for why the
#: schema is passed explicitly rather than left to the connection's default.
SCHEMA_SENTINEL_TABLE = "trials"
SCHEMA_SENTINEL_SCHEMA = SCHEMA_RESEARCH


async def _wait_for_schema(*, timeout: float = SCHEMA_WAIT_SECONDS) -> bool:
    """Block until `research.trials` exists, or `timeout` elapses. Never raises.

    Two containers have no startup ordering: this worker can reach its first cycle while the
    API's `alembic upgrade head` is still running. Starting anyway after the timeout is the
    right failure mode -- a cycle that finds no table fails, logs, and the next fire succeeds
    once migrations land, which is far better than a worker that exits and leaves compose
    restarting it forever.

    The inspection runs in a thread because SQLAlchemy's `inspect` opens a real connection, and
    a blocking connect on the event loop is the T35 hazard all over again.
    """
    logger.info("waiting for the %r table (up to %.0fs)", SCHEMA_SENTINEL_TABLE, timeout)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    engine = get_engine()

    while True:
        try:
            found = await asyncio.to_thread(
                lambda: inspect(engine).has_table(
                    SCHEMA_SENTINEL_TABLE, schema=SCHEMA_SENTINEL_SCHEMA
                )
            )
            if found:
                logger.info("%r is present", SCHEMA_SENTINEL_TABLE)
                return True
        except Exception as exc:  # noqa: BLE001 - any connect failure is "not ready yet"
            logger.info("waiting for the database: %s", exc.__class__.__name__)

        if loop.time() >= deadline:
            logger.warning(
                "schema still missing table %r after %.0fs; starting anyway -- the scheduled "
                "cycle will work once migrations land",
                SCHEMA_SENTINEL_TABLE,
                timeout,
            )
            return False
        await asyncio.sleep(SCHEMA_POLL_SECONDS)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """SIGTERM/SIGINT set `stop`, on Linux and on the Windows dev host alike.

    Same fallback as `gex_capture`: `loop.add_signal_handler` is the correct mechanism and is
    what the container uses, but it raises `NotImplementedError` on Windows, where this is most
    often run by hand.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def run(*, stop: asyncio.Event | None = None, wait_for_schema: bool = True) -> None:
    """Start the scheduler and run until `stop` is set.

    `stop` and `wait_for_schema` are injectable so tests exercise the real path without a
    database, a clock or a signal.
    """
    if wait_for_schema:
        await _wait_for_schema()

    stop = stop if stop is not None else asyncio.Event()

    scheduler = build_research_scheduler()
    scheduler.start()
    jobs = [job.id for job in scheduler.get_jobs()]
    logger.info(
        "research worker started; schedule=%s jobs=%s", settings.RESEARCH_SCHEDULE, jobs
    )
    if not jobs:
        # Legitimate (`RESEARCH_SCHEDULE=off`) but worth saying out loud: a silent worker and a
        # misconfigured one look identical from outside.
        logger.warning("no jobs registered -- this worker will do nothing until reconfigured")

    try:
        await stop.wait()
    finally:
        logger.info("shutting down")
        # `wait=False`: a cycle can be tens of minutes of backtesting, and SIGTERM must not
        # queue behind it. The in-flight cycle loses only its uncommitted trials.
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
