"""Tests for `GET /api/health/capture` (TASKS.md T29). Offline: `get_session_factory` is
monkeypatched at its import site in `app.api.health`/`app.jobs.capture`, no real Postgres.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import router
from app.models.db import Base, Snapshot, get_engine, get_sessionmaker


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    monkeypatch.setattr("app.api.health.get_session_factory", lambda: factory)
    monkeypatch.setattr("app.jobs.catchup.get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


def _add_snapshot(session_factory, underlying, captured_at, *, is_eod):
    with session_factory() as session:
        session.add(
            Snapshot(
                underlying=underlying,
                captured_at=captured_at,
                source="cboe",
                spot=100.0,
                contract_count=1,
                parquet_path=f"{underlying}.parquet",
                is_eod=is_eod,
            )
        )
        session.commit()


def test_capture_health_empty_db_reports_none_and_stale_for_every_symbol(client, session_factory):
    response = client.get("/api/health/capture")
    assert response.status_code == 200
    body = response.json()
    assert {s["underlying"] for s in body["symbols"]} == {"SPX", "SPY", "QQQ", "GLD", "DIA"}
    for entry in body["symbols"]:
        assert entry["last_capture_at"] is None
        assert entry["last_eod_capture_at"] is None
        assert entry["eod_captured_today"] is False
        assert entry["stale"] is True  # never captured -- unambiguously stale


def _freeze_now(monkeypatch, frozen_now: dt.datetime) -> None:
    """Freeze `dt.datetime.now(...)` as seen through `app.api.health`'s `import datetime as
    dt`. `monkeypatch.setattr` targets the shared stdlib `datetime` module object itself
    (there is only one), so this affects every module's `dt.datetime.now(...)` call for the
    duration of the test and is auto-restored by pytest afterwards -- safer than a manual
    try/finally, which would leak the patch on an assertion failure.
    """

    class _FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now if tz is not None else frozen_now.replace(tzinfo=None)

    monkeypatch.setattr(dt, "datetime", _FrozenDatetime)


def test_capture_health_reports_healthy_when_todays_eod_row_exists(
    client, session_factory, monkeypatch
):
    _freeze_now(monkeypatch, dt.datetime(2026, 9, 4, 21, 0, tzinfo=dt.UTC))

    captured_at = dt.datetime(2026, 9, 4, 20, 20, tzinfo=dt.UTC)
    for symbol in ["SPX", "SPY", "QQQ", "GLD", "DIA"]:
        _add_snapshot(session_factory, symbol, captured_at, is_eod=True)

    response = client.get("/api/health/capture")

    assert response.status_code == 200
    body = response.json()
    for entry in body["symbols"]:
        assert entry["eod_captured_today"] is True
        assert entry["last_eod_capture_at"] is not None
        assert entry["stale"] is False


def test_capture_health_reports_stale_when_last_eod_is_more_than_one_trading_day_old(
    client, session_factory, monkeypatch
):
    # "Now" is 2026-09-08 (Tuesday) evening; the last EOD row is from 2026-09-02 (Wednesday).
    # The most recent trading days by then are 09-08, 09-04 (Fri; 09-05/06 weekend, 09-07
    # Labor Day), 09-03, 09-02 -- 09-02 is more than one trading day behind 09-08, so this
    # must report stale.
    _freeze_now(monkeypatch, dt.datetime(2026, 9, 8, 21, 0, tzinfo=dt.UTC))

    stale_captured_at = dt.datetime(2026, 9, 2, 20, 20, tzinfo=dt.UTC)
    _add_snapshot(session_factory, "SPX", stale_captured_at, is_eod=True)

    response = client.get("/api/health/capture")

    assert response.status_code == 200
    body = response.json()
    spx = next(s for s in body["symbols"] if s["underlying"] == "SPX")
    assert spx["eod_captured_today"] is False
    assert spx["stale"] is True
