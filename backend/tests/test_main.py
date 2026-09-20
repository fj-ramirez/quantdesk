"""Tests for `app/main.py`: the root `/health` contract, and the fact that the API process
starts nothing.

**T75 rewrote this file's remit.** It used to own the lifespan wiring -- that the startup
catch-up was scheduled on boot, and critically that it ran as a background task rather than
being awaited. All of that moved to `app/workers/gex_capture.py`, and the three tests that
covered it moved with it, to `tests/test_workers_gex_capture.py`. Nothing was dropped: the
assertions there are the same ones, including the 192.0.2.1 unreachable-database case, aimed
at the worker instead of the app.

What is left here is the `/health` route (a deployment contract -- see `compose.prod.yaml`)
plus one new test asserting the *absence* of background work. That absence is the whole point
of T75 and is exactly the kind of property that regresses silently: if a later task puts
`build_scheduler()` back in a lifespan while the `gex-capture` container is also running,
every capture fires twice and nothing fails until someone reads the log.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module


def test_the_api_process_starts_no_background_work():
    """T75's load-bearing property, asserted directly.

    Two schedulers is the failure this guards: the worker container runs `build_scheduler()`,
    so if the API process ever starts one too, the 16:20 EOD capture, the 20:00 safety net and
    every other job fire twice a day. T71's write idempotency would hide most of the damage,
    which is exactly why this needs a test rather than trusting the symptom to show up.

    Checked three ways, because any one alone is easy to defeat by accident: nothing left a
    scheduler on `app.state`, nothing left a catch-up task there, and no startup/shutdown
    handler is registered on the router.
    """
    with TestClient(main_module.app) as client:
        assert not hasattr(client.app.state, "scheduler")
        assert not hasattr(client.app.state, "startup_catchup_task")

    assert main_module.app.router.on_startup == []
    assert main_module.app.router.on_shutdown == []


def test_health_and_snapshots_and_capture_health_routes_are_registered():
    with TestClient(main_module.app) as client:
        assert client.get("/health").status_code == 200
        # No `underlying` query param -> 422 (route exists, request is malformed), not 404.
        assert client.get("/api/gex/snapshots").status_code == 422
        # Openapi schema is the simplest route-existence check that needs no real DB.
        schema = client.get("/openapi.json").json()
        assert "/api/gex/health/capture" in schema["paths"]


def test_health_reports_db_ok_when_the_database_answers(monkeypatch):
    """The production container healthcheck (compose.prod.yaml) exits non-zero unless
    `/health` reports `db: ok`, so this field is a deployment contract, not decoration.

    SQLite in memory rather than a real Postgres: the probe is a bare `SELECT 1`, which says
    nothing engine-specific, and the point here is that a reachable database produces "ok".
    """
    monkeypatch.setattr(
        main_module, "get_session_factory", lambda: sessionmaker(bind=create_engine("sqlite://"))
    )

    with TestClient(main_module.app) as client:
        body = client.get("/health").json()
        assert body["db"] == "ok"
        # Additive: the pre-existing keys are untouched by the probe.
        assert body["status"] == "ok"
        assert "provider" in body and "symbols" in body


def test_health_still_answers_200_with_db_error_when_the_database_is_unreachable(monkeypatch):
    """An unreachable database must be reported *in the body*, not by failing the request.

    `status` deliberately stays "ok" -- the API process is up and answering, which is a
    different fact from the database being reachable, and collapsing the two would make "the
    backend is down" and "Postgres is down" indistinguishable to whoever is debugging at
    16:20. The healthcheck keys off `db`, so the container still goes unhealthy.

    192.0.2.1 is RFC 5737 TEST-NET-1: guaranteed to exist as an address and never answer, so
    the failure does not depend on this machine's network. `connect_timeout=1` keeps it quick.
    """
    unreachable = create_engine(
        "postgresql+psycopg://gex:gex@192.0.2.1:5432/gex", connect_args={"connect_timeout": 1}
    )
    monkeypatch.setattr(main_module, "get_session_factory", lambda: sessionmaker(bind=unreachable))

    with TestClient(main_module.app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["db"] == "error"
        assert body["status"] == "ok"
        # The password lives in DATABASE_URL and SQLAlchemy puts the URL in its connection
        # errors, so the probe must never let the exception text reach the response body.
        assert "gex:gex" not in response.text


def test_health_stays_at_the_root_and_is_not_under_a_module_prefix():
    """`compose.prod.yaml`'s backend healthcheck hits `http://127.0.0.1:8001/health`. T75 moved
    every router under `/api/<module>`; this route deliberately did not move, and a later task
    that "tidied" it behind a prefix would break every production deploy's healthcheck while
    the test suite stayed green.
    """
    with TestClient(main_module.app) as client:
        schema = client.get("/openapi.json").json()
        assert "/health" in schema["paths"]
        assert "/api/health" not in schema["paths"]
        assert "/api/gex/health" not in schema["paths"]
