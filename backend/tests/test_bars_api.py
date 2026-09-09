"""Tests for `app/api/bars.py`. Offline: `get_session_factory` is monkeypatched at its import
site in `app.api.bars`, no real Postgres -- same pattern as `test_snapshots_api.py`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.bars import router
from app.models.bars import DailyBar as DailyBarIn
from app.models.db import Base, get_engine, get_sessionmaker
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
    monkeypatch.setattr("app.api.bars.get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


def _bar(symbol, date, close, volume=1000) -> DailyBarIn:
    return DailyBarIn(
        symbol=symbol,
        date=date,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=volume,
        source="yahoo-splitadj",
    )


def test_get_bars_for_spy_returns_stored_rows(client, session_factory):
    upsert_bars(
        [
            _bar("SPY", dt.date(2026, 9, 3), 649.12),
            _bar("SPY", dt.date(2026, 9, 4), 648.83),
        ],
        session_factory=session_factory,
    )

    response = client.get("/api/bars/SPY", params={"start": "2026-01-01"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["date"] == "2026-09-03"
    assert body[0]["close"] == pytest.approx(649.12)
    assert body[0]["source"] == "yahoo-splitadj"


def test_get_bars_for_unknown_symbol_returns_empty_list_not_404(client, session_factory):
    response = client.get("/api/bars/NOPE")
    assert response.status_code == 200
    assert response.json() == []


def test_get_bars_respects_start_and_end(client, session_factory):
    upsert_bars(
        [_bar("SPY", dt.date(2026, 9, d), 650.0 + d) for d in range(1, 6)],
        session_factory=session_factory,
    )
    response = client.get(
        "/api/bars/SPY", params={"start": "2026-09-02", "end": "2026-09-03"}
    )
    assert response.status_code == 200
    dates = [row["date"] for row in response.json()]
    assert dates == ["2026-09-02", "2026-09-03"]


def test_get_bars_symbol_is_case_insensitive(client, session_factory):
    upsert_bars([_bar("SPY", dt.date(2026, 9, 4), 650.0)], session_factory=session_factory)
    response = client.get("/api/bars/spy")
    assert response.status_code == 200
    assert len(response.json()) == 1


# --- ^VIX: the "^" URL-encoding/route-matching hazard ------------------------------------------


def test_get_bars_for_percent_encoded_vix(client, session_factory):
    upsert_bars(
        [_bar("^VIX", dt.date(2026, 9, 4), 16.15, volume=0)], session_factory=session_factory
    )
    response = client.get("/api/bars/%5EVIX")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["volume"] == 0  # a genuine zero, not dropped or turned into null


def test_get_bars_for_literal_caret_vix(client, session_factory):
    """httpx (TestClient's transport) percent-encodes a raw '^' in a URL path itself, so this
    exercises the same route with the client doing the encoding instead of the test."""
    upsert_bars(
        [_bar("^VIX", dt.date(2026, 9, 4), 16.15, volume=0)], session_factory=session_factory
    )
    response = client.get("/api/bars/^VIX")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_bars_spy_and_vix_are_distinct(client, session_factory):
    upsert_bars(
        [
            _bar("SPY", dt.date(2026, 9, 4), 650.0),
            _bar("^VIX", dt.date(2026, 9, 4), 16.15, volume=0),
        ],
        session_factory=session_factory,
    )
    spy = client.get("/api/bars/SPY").json()
    vix = client.get("/api/bars/%5EVIX").json()
    assert len(spy) == 1
    assert len(vix) == 1
    assert spy[0]["close"] == pytest.approx(650.0)
    assert vix[0]["close"] == pytest.approx(16.15)


# --- /api/universe -----------------------------------------------------------------------------


def test_get_universe_returns_scan_universe(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "SCAN_UNIVERSE", "SPY,QQQ,^VIX")
    response = client.get("/api/universe")
    assert response.status_code == 200
    assert response.json() == {"symbols": ["SPY", "QQQ", "^VIX"]}
