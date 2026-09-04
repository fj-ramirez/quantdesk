"""Tests for `app/api/snapshots.py`. Offline: `capture_snapshot` and `get_session_factory`
are monkeypatched at their import site in `app.api.snapshots`, so no network or real Postgres
is ever touched -- the live end-to-end path is covered separately by the T05 manual
acceptance run (Docker Postgres + the real Cboe endpoint), not by this unit suite.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.snapshots import router
from app.jobs.capture import CaptureResult
from app.models.db import Base, Snapshot, get_engine, get_sessionmaker


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def test_capture_rejects_unsupported_underlying_without_calling_capture(client, monkeypatch):
    called = False

    async def fake_capture_snapshot(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("app.api.snapshots.capture_snapshot", fake_capture_snapshot)

    response = client.post("/api/snapshots/capture", params={"underlying": "DOGE"})

    assert response.status_code == 422
    assert called is False


def test_capture_success_returns_201_with_result_body(client, monkeypatch):
    async def fake_capture_snapshot(underlying, *, is_eod, **kwargs):
        return CaptureResult(
            underlying=underlying,
            ok=True,
            contract_count=246,
            spot=6500.5,
            duration_seconds=0.42,
            snapshot_id=7,
            parquet_path="chains/SPX/2026/09/x.parquet",
            skipped_duplicate=False,
        )

    monkeypatch.setattr("app.api.snapshots.capture_snapshot", fake_capture_snapshot)

    response = client.post("/api/snapshots/capture", params={"underlying": "SPX", "eod": "false"})

    assert response.status_code == 201
    body = response.json()
    assert body["underlying"] == "SPX"
    assert body["ok"] is True
    assert body["snapshot_id"] == 7
    assert body["contract_count"] == 246


def test_capture_failure_returns_502(client, monkeypatch):
    async def fake_capture_snapshot(underlying, *, is_eod, **kwargs):
        return CaptureResult(underlying=underlying, ok=False, error="cboe is down")

    monkeypatch.setattr("app.api.snapshots.capture_snapshot", fake_capture_snapshot)

    response = client.post("/api/snapshots/capture", params={"underlying": "SPX"})

    assert response.status_code == 502
    assert response.json()["detail"] == "cboe is down"


def test_capture_passes_eod_flag_through(client, monkeypatch):
    seen = {}

    async def fake_capture_snapshot(underlying, *, is_eod, **kwargs):
        seen["underlying"] = underlying
        seen["is_eod"] = is_eod
        return CaptureResult(underlying=underlying, ok=True, contract_count=0, spot=1.0)

    monkeypatch.setattr("app.api.snapshots.capture_snapshot", fake_capture_snapshot)

    client.post("/api/snapshots/capture", params={"underlying": "spy", "eod": "true"})

    assert seen == {"underlying": "spy", "is_eod": True}


def test_list_snapshots_returns_rows_newest_first(client, monkeypatch, session_factory):
    monkeypatch.setattr("app.api.snapshots.get_session_factory", lambda: session_factory)
    t0 = dt.datetime(2026, 9, 4, 16, 0, 0, tzinfo=dt.UTC)
    with session_factory() as session:
        for i, minutes in enumerate([0, 30, 15]):
            session.add(
                Snapshot(
                    underlying="SPX",
                    captured_at=t0 + dt.timedelta(minutes=minutes),
                    source="cboe",
                    spot=6500.0 + i,
                    contract_count=100 + i,
                    parquet_path=f"spx-{i}.parquet",
                    is_eod=False,
                )
            )
        session.commit()

    response = client.get("/api/snapshots", params={"underlying": "SPX", "limit": 30})

    assert response.status_code == 200
    body = response.json()
    # Newest first, by `captured_at` (t0+30, t0+15, t0+0) -- `spot` was set to `6500.0 + i`
    # in insertion order above, so it doubles as an identity check without depending on the
    # filesystem path leaking into the response (see `SnapshotOut`'s docstring, T30).
    assert [row["spot"] for row in body] == pytest.approx([6501.0, 6502.0, 6500.0])
    assert all("parquet_path" not in row for row in body)


def test_list_snapshots_respects_limit(client, monkeypatch, session_factory):
    monkeypatch.setattr("app.api.snapshots.get_session_factory", lambda: session_factory)
    t0 = dt.datetime(2026, 9, 4, 16, 0, 0, tzinfo=dt.UTC)
    with session_factory() as session:
        for i in range(5):
            session.add(
                Snapshot(
                    underlying="SPX",
                    captured_at=t0 + dt.timedelta(minutes=i),
                    source="cboe",
                    spot=6500.0,
                    contract_count=100,
                    parquet_path=f"spx-{i}.parquet",
                    is_eod=False,
                )
            )
        session.commit()

    response = client.get("/api/snapshots", params={"underlying": "SPX", "limit": 2})

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_snapshots_empty_for_unknown_underlying(client, monkeypatch, session_factory):
    monkeypatch.setattr("app.api.snapshots.get_session_factory", lambda: session_factory)
    response = client.get("/api/snapshots", params={"underlying": "QQQ"})
    assert response.status_code == 200
    assert response.json() == []
