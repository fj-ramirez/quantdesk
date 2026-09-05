"""Tests for `app/main.py`'s lifespan wiring (TASKS.md T29).

Confirms the startup catch-up is actually scheduled on boot and, critically, that it runs as
a background task rather than being awaited -- a slow (or hanging) catch-up must not delay the
app coming up. Does not touch a real scheduler firing or real Cboe; `build_scheduler` itself is
already covered by `test_scheduler.py`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module


def test_lifespan_schedules_startup_catchup_as_a_background_task(monkeypatch):
    called = asyncio.Event()

    async def fake_startup_catchup_job():
        called.set()

    monkeypatch.setattr(main_module, "startup_catchup_job", fake_startup_catchup_job)

    with TestClient(main_module.app) as client:
        task = client.app.state.startup_catchup_task
        assert isinstance(task, asyncio.Task)
        response = client.get("/health")
        assert response.status_code == 200


def test_lifespan_does_not_block_startup_on_a_slow_catchup(monkeypatch):
    """A catch-up that takes a while (three sequential Cboe fetches, or a hung request) must
    not stall the app coming up -- this is the whole reason `main.py` uses `create_task`
    instead of `await`ing `startup_catchup_job()` directly.
    """

    async def slow_startup_catchup_job():
        await asyncio.sleep(5)

    monkeypatch.setattr(main_module, "startup_catchup_job", slow_startup_catchup_job)

    start = time.monotonic()
    with TestClient(main_module.app) as client:
        elapsed = time.monotonic() - start
        assert elapsed < 2.0  # generously under the 5s the fake catch-up would otherwise cost
        response = client.get("/health")
        assert response.status_code == 200
        # Cancel it ourselves so this test doesn't wait out the 5s sleep on teardown -- the
        # real lifespan's shutdown does the same cancellation for a task still in flight.
        client.app.state.startup_catchup_task.cancel()


def test_startup_completes_and_health_answers_when_database_is_unreachable(monkeypatch):
    """T35 regression, at the real `app.main.app` boundary (not the fake `startup_catchup_job`
    the other tests here use) -- `catch_up_missed_eod`'s `has_eod_snapshot_today` used to run
    its blocking psycopg connect directly on the event loop inside the `create_task`d startup
    coroutine, so an unreachable database starved the loop uvicorn itself needs to log
    "Application startup complete" and start accepting connections (empirically confirmed
    against a real unroutable Postgres host before this fix; see TASKS.md T35). This test
    still exercises the real `catch_up_missed_eod` -> `has_eod_snapshot_today` path -- only the
    session factory it lands on is swapped for one pointed at 192.0.2.1, an RFC 5737
    TEST-NET-1 address guaranteed to exist and never answer, so the failure is deterministic
    and doesn't depend on this machine's actual network topology. `connect_timeout=1` keeps
    the test itself fast; the app must not need to wait even that long.

    `is_trading_day`/`EOD_CUTOFF` are relaxed so the catch-up always reaches the DB check
    regardless of the real wall-clock time this test happens to run at.
    """
    bad_engine = create_engine(
        "postgresql+psycopg://gex:gex@192.0.2.1:5432/gex", connect_args={"connect_timeout": 1}
    )
    monkeypatch.setattr("app.jobs.catchup.get_session_factory", lambda: sessionmaker(bind=bad_engine))
    monkeypatch.setattr("app.jobs.catchup.is_trading_day", lambda day: True)
    monkeypatch.setattr("app.jobs.catchup.EOD_CUTOFF", dt.time.min)

    start = time.monotonic()
    with TestClient(main_module.app) as client:
        elapsed = time.monotonic() - start
        assert elapsed < 5.0  # startup must not itself wait on the DB connect attempt
        response = client.get("/health")
        assert response.status_code == 200
        client.app.state.startup_catchup_task.cancel()


def test_health_and_snapshots_and_capture_health_routes_are_registered(monkeypatch):
    async def fake_startup_catchup_job():
        return None

    monkeypatch.setattr(main_module, "startup_catchup_job", fake_startup_catchup_job)

    with TestClient(main_module.app) as client:
        assert client.get("/health").status_code == 200
        # No `underlying` query param -> 422 (route exists, request is malformed), not 404.
        assert client.get("/api/snapshots").status_code == 422
        # Openapi schema is the simplest route-existence check that needs no real DB.
        schema = client.get("/openapi.json").json()
        assert "/api/health/capture" in schema["paths"]
        client.app.state.startup_catchup_task.cancel()
