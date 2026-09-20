"""Tests for `app/workers/gex_capture.py` -- the scheduler and startup catch-up that T75 moved
out of `app/main.py`'s FastAPI lifespan.

**Three of these tests came from `tests/test_main.py` verbatim in substance.** The behaviour
they cover left `main.py`, so they followed it rather than being deleted: the catch-up is
scheduled as a background task, a slow catch-up does not block boot, and an unreachable
database does not stall startup (the T35 regression, still exercising the real
`catch_up_missed_eod` -> `has_eod_snapshot_today` path against RFC 5737 TEST-NET-1). The rest
are new coverage the worker needs and the lifespan never had: that the job ids are logged, and
that shutdown really shuts the scheduler down.

Everything here runs offline. `run(..., wait_for_schema=False)` skips the database probe, and
`build_scheduler` is stubbed wherever the test is not about the real trigger policy --
`tests/test_scheduler.py` owns that, and deliberately inspects a *built* scheduler without
starting one, which is the property T75 had to preserve to prove the move worked.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.workers.gex_capture as worker


class _FakeScheduler:
    """Records what the worker did to it. Standing in for `AsyncIOScheduler` keeps these tests
    off the clock entirely -- a real one would start a thread and a timer for jobs that are
    hours away."""

    def __init__(self, job_ids: list[str]):
        self._job_ids = job_ids
        self.started = False
        self.shutdown_calls: list[bool] = []

    def start(self) -> None:
        self.started = True

    def get_jobs(self):
        return [type("Job", (), {"id": jid})() for jid in self._job_ids]

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


async def _run_until_started(monkeypatch, *, catchup, job_ids=("capture_eod",)):
    """Start `worker.run` on this loop, let it get as far as firing the catch-up, then stop it.

    Returns `(scheduler, task)` with the run task already awaited to completion, so a test can
    assert on both the start and the shutdown side.
    """
    scheduler = _FakeScheduler(list(job_ids))
    monkeypatch.setattr(worker, "build_scheduler", lambda: scheduler)
    monkeypatch.setattr(worker, "startup_catchup_job", catchup)

    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop=stop, wait_for_schema=False))
    # One turn of the loop is enough for `run` to build, start, log and `create_task` --
    # nothing in that path awaits anything slower.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    return scheduler, task


async def test_worker_schedules_startup_catchup_as_a_background_task(monkeypatch):
    """The T29 catch-up must actually be fired on boot. Moved from `tests/test_main.py`."""
    called = asyncio.Event()

    async def fake_startup_catchup_job():
        called.set()

    scheduler, _ = await _run_until_started(monkeypatch, catchup=fake_startup_catchup_job)

    assert scheduler.started
    assert called.is_set()


async def test_worker_does_not_block_startup_on_a_slow_catchup(monkeypatch):
    """A catch-up that takes a while (three sequential Cboe fetches, or a hung request) must
    not stall the worker coming up -- this is the whole reason the worker uses `create_task`
    instead of `await`ing `startup_catchup_job()` directly. Moved from `tests/test_main.py`,
    where it guarded the identical `create_task` in the lifespan.
    """

    async def slow_startup_catchup_job():
        await asyncio.sleep(5)

    start = time.monotonic()
    scheduler, _ = await _run_until_started(monkeypatch, catchup=slow_startup_catchup_job)
    elapsed = time.monotonic() - start

    # Generously under the 5s the fake catch-up would otherwise cost.
    assert elapsed < 2.0
    assert scheduler.started
    # The in-flight task was cancelled on the way out rather than awaited.
    assert scheduler.shutdown_calls == [False]


async def test_worker_starts_and_stops_when_the_database_is_unreachable(monkeypatch):
    """T35 regression, moved from `tests/test_main.py` and still exercising the real
    `catch_up_missed_eod` -> `has_eod_snapshot_today` path.

    That code used to run its blocking psycopg connect directly on the event loop inside the
    `create_task`d startup coroutine, so an unreachable database starved the loop. Only the
    session factory it lands on is swapped, for one pointed at 192.0.2.1 -- an RFC 5737
    TEST-NET-1 address guaranteed to exist and never answer, so the failure is deterministic
    and does not depend on this machine's network topology. `connect_timeout=1` keeps the test
    fast; the worker must not need to wait even that long.

    `is_trading_day`/`EOD_CUTOFF` are relaxed so the catch-up always reaches the DB check
    regardless of the real wall-clock time this test happens to run at.
    """
    bad_engine = create_engine(
        "postgresql+psycopg://gex:gex@192.0.2.1:5432/gex", connect_args={"connect_timeout": 1}
    )
    monkeypatch.setattr(
        "app.modules.gex.jobs.catchup.get_session_factory", lambda: sessionmaker(bind=bad_engine)
    )
    monkeypatch.setattr("app.modules.gex.jobs.catchup.is_trading_day", lambda day: True)
    monkeypatch.setattr("app.modules.gex.jobs.catchup.EOD_CUTOFF", dt.time.min)

    scheduler = _FakeScheduler(["capture_eod"])
    monkeypatch.setattr(worker, "build_scheduler", lambda: scheduler)

    start = time.monotonic()
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop=stop, wait_for_schema=False))
    await asyncio.sleep(0)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0  # startup must not itself wait on the DB connect attempt
    assert scheduler.started

    stop.set()
    await asyncio.wait_for(task, timeout=10)


async def test_worker_logs_its_registered_job_ids_at_boot(monkeypatch, caplog):
    """The acceptance check for T75's riskiest move.

    A green test suite proves the scheduler was *built*; it does not prove a cron trigger
    survived changing process. This log line is what a human reads after the first deploy to
    confirm the ten jobs are really registered, so it is worth a test of its own -- a silent
    worker and a worker with no jobs look identical from outside.
    """
    async def noop_catchup():
        return None

    with caplog.at_level(logging.INFO, logger="app.workers.gex_capture"):
        await _run_until_started(
            monkeypatch,
            catchup=noop_catchup,
            job_ids=("capture_eod", "capture_eod_safety_net", "bars_update"),
        )

    started = [r for r in caplog.records if r.message.startswith("scheduler started")]
    assert len(started) == 1
    rendered = started[0].getMessage()
    assert "capture_eod" in rendered
    assert "capture_eod_safety_net" in rendered
    assert "bars_update" in rendered


async def test_worker_shuts_the_scheduler_down_without_waiting(monkeypatch):
    """`shutdown(wait=False)` and then cancelling an in-flight catch-up: the same two lines,
    in the same order, as the lifespan's `finally` block before T75. `wait=True` would block
    SIGTERM behind whatever job happened to be mid-fetch, which on a 16:20 capture is a Cboe
    round trip the container has no reason to wait for.
    """
    async def noop_catchup():
        return None

    scheduler, task = await _run_until_started(monkeypatch, catchup=noop_catchup)

    assert scheduler.shutdown_calls == [False]
    assert task.done() and not task.cancelled()


async def test_wait_for_schema_returns_false_and_does_not_raise_when_the_database_is_absent(
    monkeypatch,
):
    """The boot wait must never be able to stop the worker starting.

    A worker that refused to come up because Postgres was slow would turn a recoverable delay
    into a missed 16:20, which is the outcome the whole module exists to prevent. So the probe
    reports what it found and the caller starts the scheduler either way.
    """
    def exploding_engine():
        raise RuntimeError("no database here")

    monkeypatch.setattr(worker, "get_engine", exploding_engine)

    with pytest.raises(RuntimeError):
        # `get_engine` is called once, outside the retry loop -- the loop guards the *probe*,
        # not engine construction, and a URL so malformed that `create_engine` fails is a
        # configuration error that should be loud rather than retried for a minute.
        await worker._wait_for_schema(timeout=0.0)


async def test_wait_for_schema_times_out_and_still_returns(monkeypatch):
    """A database that is reachable but un-migrated: probe says no, deadline passes, the
    worker is told so and carries on. `timeout=0` makes the first failed probe also the last.
    """
    monkeypatch.setattr(worker, "get_engine", lambda: object())

    def always_missing(_engine):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(worker, "inspect", always_missing)

    assert await worker._wait_for_schema(timeout=0.0) is False


async def test_wait_for_schema_returns_true_once_the_table_appears(monkeypatch):
    monkeypatch.setattr(worker, "get_engine", lambda: object())

    class _Inspector:
        def has_table(self, name: str) -> bool:
            assert name == "snapshots"
            return True

    monkeypatch.setattr(worker, "inspect", lambda _engine: _Inspector())

    assert await worker._wait_for_schema(timeout=5.0) is True
