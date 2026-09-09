"""Tests for `GET /api/health/capture` (TASKS.md T29). Offline: `get_session_factory` is
monkeypatched at its import site in `app.api.health`/`app.jobs.capture`, no real Postgres.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import router
from app.models.bars import DailyBar as DailyBarIn
from app.models.db import Base, Snapshot, get_engine, get_sessionmaker
from app.storage.bars_repository import upsert_bars


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
    # T42: the same DB backs daily_bars too, so the bars health block (below) reads/writes
    # through the identical temp-SQLite factory rather than the real Postgres default.
    monkeypatch.setattr("app.api.health.get_bars_session_factory", lambda: factory)
    monkeypatch.setattr("app.storage.bars_repository.get_session_factory", lambda: factory)
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


# --- T42: additive `bars` block ----------------------------------------------------------------


def test_capture_health_response_carries_a_bars_block(client, session_factory):
    """Additive per the T42 brief: the existing `symbols` shape is untouched (covered by every
    test above), and a new `bars` key appears alongside it."""
    response = client.get("/api/health/capture")
    assert response.status_code == 200
    body = response.json()
    assert "bars" in body
    assert "symbols" in body["bars"]
    assert "stale_count" in body["bars"]


def test_bars_health_reports_every_symbol_stale_on_an_empty_db(client, session_factory):
    from app import config

    response = client.get("/api/health/capture")
    body = response.json()
    bars_symbols = {s["symbol"]: s for s in body["bars"]["symbols"]}
    assert set(bars_symbols) == set(config.settings.scan_universe)
    for entry in bars_symbols.values():
        assert entry["last_bar_date"] is None
        assert entry["stale"] is True
    assert body["bars"]["stale_count"] == len(config.settings.scan_universe)


def test_bars_health_reports_fresh_when_todays_bar_exists(client, session_factory, monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "SCAN_UNIVERSE", "SPY,QQQ")
    _freeze_now(monkeypatch, dt.datetime(2026, 9, 4, 21, 0, tzinfo=dt.UTC))

    upsert_bars(
        [
            DailyBarIn(
                symbol="SPY",
                date=dt.date(2026, 9, 4),
                open=650.0,
                high=655.0,
                low=648.0,
                close=652.0,
                volume=44_000_000,
                source="yahoo-splitadj",
            )
        ],
        session_factory=session_factory,
    )

    response = client.get("/api/health/capture")
    body = response.json()
    bars_symbols = {s["symbol"]: s for s in body["bars"]["symbols"]}
    assert bars_symbols["SPY"]["last_bar_date"] == "2026-09-04"
    assert bars_symbols["SPY"]["stale"] is False
    assert bars_symbols["QQQ"]["last_bar_date"] is None
    assert bars_symbols["QQQ"]["stale"] is True
    assert body["bars"]["stale_count"] == 1


# --- T47: additive `extended` block --------------------------------------------------------


def test_capture_health_response_carries_an_extended_block(client, session_factory):
    """Additive per the T47 brief: the existing `symbols` shape (the core five, the P0
    capture) is untouched -- covered by every test above -- and a new `extended` key appears
    alongside it, one row per `settings.extended_symbols`."""
    from app import config

    response = client.get("/api/health/capture")
    assert response.status_code == 200
    body = response.json()
    assert "extended" in body
    assert {s["underlying"] for s in body["extended"]} == set(config.settings.extended_symbols)
    # The core block must be exactly the five it always was -- an extended symbol appearing
    # there (or a core one leaking into `extended`) would defeat the whole point of the split.
    assert {s["underlying"] for s in body["symbols"]} == {"SPX", "SPY", "QQQ", "GLD", "DIA"}


def test_capture_health_extended_symbols_report_stale_on_an_empty_db(client, session_factory):
    response = client.get("/api/health/capture")
    body = response.json()
    for entry in body["extended"]:
        assert entry["last_capture_at"] is None
        assert entry["last_eod_capture_at"] is None
        assert entry["eod_captured_today"] is False
        assert entry["stale"] is True


def test_capture_health_extended_symbol_reports_healthy_when_todays_eod_row_exists(
    client, session_factory, monkeypatch
):
    _freeze_now(monkeypatch, dt.datetime(2026, 9, 4, 21, 45, tzinfo=dt.UTC))

    captured_at = dt.datetime(2026, 9, 4, 20, 45, tzinfo=dt.UTC)
    _add_snapshot(session_factory, "XLK", captured_at, is_eod=True)

    response = client.get("/api/health/capture")

    assert response.status_code == 200
    body = response.json()
    xlk = next(s for s in body["extended"] if s["underlying"] == "XLK")
    assert xlk["eod_captured_today"] is True
    assert xlk["last_eod_capture_at"] is not None
    assert xlk["stale"] is False
    # A fresh extended capture must not be conflated with the core block's staleness -- SPX
    # (etc.) never got a snapshot in this test, so it stays stale.
    spx = next(s for s in body["symbols"] if s["underlying"] == "SPX")
    assert spx["stale"] is True
