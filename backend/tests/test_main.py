"""Tests for `app/main.py`'s lifespan wiring (TASKS.md T29).

Confirms the startup catch-up is actually scheduled on boot and, critically, that it runs as
a background task rather than being awaited -- a slow (or hanging) catch-up must not delay the
app coming up. Does not touch a real scheduler firing or real Cboe; `build_scheduler` itself is
already covered by `test_scheduler.py`.
"""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

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
