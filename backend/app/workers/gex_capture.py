"""The GEX capture worker: APScheduler plus the startup catch-up, lifted out of the API
process's FastAPI lifespan (T75).

    python -m app.workers.gex_capture

**Why this is its own process.** Until T75 `app/main.py`'s lifespan built the scheduler,
started it and fired `startup_catchup_job()`. That was fine while the API served one module.
It stops being fine the moment three do: a `--reload` restart in dev re-runs the catch-up,
and redeploying the API for a change that has nothing to do with GEX interrupts capture
mid-fetch. The split in this application is request path vs. background work, so everything
clock-bound lives here and `main.py` starts nothing at all.

**This is the riskiest part of T75 and it is worth being explicit about why.** Capture is the
one thing in this repository that cannot be backfilled -- the free Cboe endpoint serves only
"now", so a missed 16:20 is a permanent hole in the dataset. A test suite that passes proves
the scheduler was *built* correctly; it does not prove a cron trigger survived moving house.
That is why this module logs its registered job ids at INFO on every boot: the log line is the
cheap, unambiguous check that the ten jobs are really there, and it is what a human should
look at after the first deploy.

What is deliberately *not* here: `alembic upgrade head`. The API container runs it, and two
containers racing `upgrade head` against one Postgres is a worse failure than the one it would
prevent. This worker waits for the schema instead -- see `_wait_for_schema`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time

from sqlalchemy import inspect

from app.core.db import get_engine
from app.modules.gex.jobs.catchup import startup_catchup_job
from app.modules.gex.jobs.scheduler import build_scheduler

__all__ = ["main", "run"]

# Same reason as `app/main.py`'s: without it the structured JSON capture log lines (T05)
# propagate to an unconfigured root logger and go nowhere. There is no uvicorn in this
# process to configure anything, so it matters more here, not less.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

logger = logging.getLogger("app.workers.gex_capture")

#: How long `_wait_for_schema` will wait for the API container's `alembic upgrade head` before
#: giving up and starting anyway. Matched to `compose.prod.yaml`'s backend `start_period: 60s`,
#: which exists for exactly the same migration.
SCHEMA_WAIT_SECONDS = 60.0

#: Polling interval for the wait above. Short enough that an already-migrated database costs
#: nothing (the first probe succeeds), long enough not to hammer a starting Postgres.
SCHEMA_POLL_SECONDS = 2.0

#: The table whose existence stands in for "migrations have run". `snapshots` is the oldest
#: one in the schema and the one `has_eod_snapshot_today` queries, so it is the table the
#: startup catch-up would actually trip over.
SCHEMA_SENTINEL_TABLE = "snapshots"


async def _wait_for_schema(*, timeout: float = SCHEMA_WAIT_SECONDS) -> bool:
    """Block until the `snapshots` table exists, or `timeout` elapses. Never raises.

    **Why this exists, and why it is the only new logic in T75.** Before the split, the API
    container's command was `alembic upgrade head && exec uvicorn`, so by the time the
    lifespan fired the startup catch-up the schema was guaranteed to be there. Two containers
    have no such ordering: this worker can reach `startup_catchup_job` while the API's alembic
    is still running, and `has_eod_snapshot_today` would query a table that does not exist yet.

    That failure is quiet, which is what makes it dangerous. `startup_catchup_job` never
    raises (by design -- see its own docstring), so the symptom is not a crash but a catch-up
    that silently did nothing on a cold start. On an app whose data source has no history,
    a silently skipped catch-up is a P0.

    Returns True if the table appeared, False on timeout. **Starts the scheduler either way**:
    a worker that refused to boot because Postgres was slow would turn a recoverable delay
    into a missed 16:20, which is the exact outcome this whole module exists to prevent. The
    return value is logged so the reason for an empty catch-up is in the log rather than
    inferred.

    Runs the inspection in a thread: SQLAlchemy's `inspect` opens a real connection, and a
    blocking connect on the event loop is the T35 bug this codebase already learned once.
    """
    # Logged unconditionally so the worker is never silent at boot. Observed during T75: with
    # the API container failing its migration, this worker sat for the full timeout printing
    # nothing at all, and "hung" and "waiting, correctly" looked identical in `compose logs`.
    logger.info("waiting for the %r table (up to %.0fs)", SCHEMA_SENTINEL_TABLE, timeout)
    deadline = time.monotonic() + timeout
    engine = get_engine()
    attempt = 0
    while True:
        attempt += 1
        try:
            found = await asyncio.to_thread(
                lambda: inspect(engine).has_table(SCHEMA_SENTINEL_TABLE)
            )
        except Exception as exc:  # noqa: BLE001 -- see below; breadth is the point here.
            # Deliberately blind. Everything this can raise means the same thing to this
            # function ("the database is not ready yet"): psycopg's OperationalError while
            # Postgres is still starting, a DNS failure while the compose network settles, a
            # DBAPI error from a half-initialised catalog. Enumerating them would be a list
            # to maintain for no behavioural difference, and the loop is bounded by
            # `deadline` and reports what happened either way.
            found = False
            if attempt == 1:
                logger.info("waiting for the database: %s", exc.__class__.__name__)
        if found:
            logger.info("schema ready after %d probe(s)", attempt)
            return True
        if time.monotonic() >= deadline:
            logger.warning(
                "schema still missing table %r after %.0fs; starting anyway -- the startup "
                "catch-up will find nothing and the scheduled jobs will work once migrations "
                "land",
                SCHEMA_SENTINEL_TABLE,
                timeout,
            )
            return False
        await asyncio.sleep(SCHEMA_POLL_SECONDS)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Make SIGTERM and SIGINT set `stop`, on both Linux and Windows.

    `loop.add_signal_handler` is the correct asyncio mechanism and is what runs in the
    container (`docker compose stop` sends SIGTERM). It raises `NotImplementedError` on
    Windows, where the user develops, so that case falls back to `signal.signal` and
    `call_soon_threadsafe` -- without it the worker could not be Ctrl-C'd cleanly on the dev
    host, which would be a daily irritation and would make "does shutdown work" untestable
    where it is most often run by hand.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def run(*, stop: asyncio.Event | None = None, wait_for_schema: bool = True) -> None:
    """Start the scheduler, fire the startup catch-up, and run until `stop` is set.

    This is the old `app/main.py` lifespan, unchanged in substance: build, start, log the job
    ids, `create_task` the catch-up rather than awaiting it, and on the way out shut the
    scheduler down without waiting and cancel the catch-up if it is still in flight. The
    `create_task` is not an optimisation carried over by habit -- `startup_catchup_job` can
    sit through several sequential Cboe fetches, and nothing else in this process should be
    blocked behind them.

    `stop` and `wait_for_schema` are injectable for the same reason the rest of this codebase
    injects `session_factory` and `data_dir`: so the tests exercise the real code path without
    a database, a clock or a signal.
    """
    if wait_for_schema:
        await _wait_for_schema()

    stop = stop if stop is not None else asyncio.Event()

    scheduler = build_scheduler()
    scheduler.start()
    logger.info("scheduler started; jobs=%s", [job.id for job in scheduler.get_jobs()])

    # T29: catch up a missed 16:20 EOD capture (laptop was off, container was down, whatever).
    # `create_task` rather than `await` so a slow or hung catch-up cannot stall this process
    # before its scheduler is running. The job function itself never raises (see its own
    # docstring), so there is nothing here to catch.
    catchup_task = asyncio.create_task(startup_catchup_job())
    try:
        await stop.wait()
    finally:
        logger.info("shutting down")
        scheduler.shutdown(wait=False)
        if not catchup_task.done():
            catchup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await catchup_task


async def main() -> None:
    """Container entrypoint: install signal handlers, then `run` until one fires."""
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    await run(stop=stop)


if __name__ == "__main__":
    asyncio.run(main())
