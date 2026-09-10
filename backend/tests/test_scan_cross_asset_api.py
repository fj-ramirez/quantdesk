"""Tests for `GET /api/scan/cross-asset` (T54, plans/continuation/06-cross-asset-regime.md).

Offline: `get_session_factory` is monkeypatched at its import site in `app.api.scan`, no real
Postgres -- same pattern `test_scan_api.py`'s own rotation/regime tests use. This file is kept
separate from `test_scan_api.py` rather than added to it, matching this task's "one additive
block, self-contained" instruction for the route itself.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.scan import router
from app.models.bars import DailyBar as DailyBarIn
from app.models.db import Base, get_engine, get_sessionmaker
from app.scan.groups import SECTORS
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
    monkeypatch.setattr("app.api.scan.get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


def _bar(symbol: str, date: dt.date, close: float, source: str = "test") -> DailyBarIn:
    return DailyBarIn(
        symbol=symbol,
        date=date,
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=None,
        source=source,
    )


def _seed(session_factory, symbol: str, closes: list[float], *, start: dt.date) -> None:
    bars = [
        _bar(symbol, start + dt.timedelta(days=i), close) for i, close in enumerate(closes)
    ]
    upsert_bars(bars, session_factory=session_factory)


def test_get_cross_asset_empty_when_nothing_seeded(client, session_factory):
    """Every universe member has zero bars -- the route must return a clean, fully-`None`
    (with reasons) row rather than a 404 or 500, the same "no data yet is not an error"
    contract every other T5x scan route already establishes."""
    response = client.get("/api/scan/cross-asset")
    assert response.status_code == 200
    body = response.json()

    assert body["as_of"] is None
    assert body["vix"] is None
    assert body["term_structure"] is None
    assert body["term_structure_reason"] is not None
    assert body["vrp"] is None
    assert body["vrp_reason"] is not None
    assert body["sector_correlation"] is None
    assert body["uup_return_20d"] is None
    assert body["gld_return_20d"] is None
    assert body["tlt_return_20d"] is None


def test_get_cross_asset_full_shape_when_seeded(client, session_factory, monkeypatch):
    n = 100
    today = dt.date(2026, 6, 5)  # the route's own "as of today" anchor -- see monkeypatch below
    monkeypatch.setattr("app.api.scan._today", lambda: today)
    start = today - dt.timedelta(days=n - 1)  # last seeded row lands exactly on `today`

    # VIX rising, VIX3M flat below it, VIX9D flat above it -> unambiguous backwardation on the
    # last day, and VIX's own value is trivially the max of its own history (percentile 1.0,
    # though n=100 < 252 so this only demonstrates the field is populated, not pinned at 1.0).
    vix_closes = [10.0 + i * 0.2 for i in range(n)]
    _seed(session_factory, "^VIX", vix_closes, start=start)
    _seed(session_factory, "^VIX3M", [22.0] * n, start=start)
    _seed(session_factory, "^VIX9D", [32.0] * n, start=start)
    _seed(session_factory, "^VVIX", [90.0 + i * 0.1 for i in range(n)], start=start)

    _seed(session_factory, "SPY", [400.0 + (i % 5) - (i % 3) for i in range(n)], start=start)

    for j, sector in enumerate(SECTORS):
        _seed(session_factory, sector, [50.0 + i * 0.1 + j for i in range(n)], start=start)

    _seed(session_factory, "UUP", [28.0 + i * 0.01 for i in range(n)], start=start)  # up
    _seed(session_factory, "GLD", [180.0 - i * 0.05 for i in range(n)], start=start)  # down
    _seed(session_factory, "TLT", [95.0] * n, start=start)  # flat

    response = client.get("/api/scan/cross-asset")
    assert response.status_code == 200
    body = response.json()

    assert body["as_of"] == today.isoformat()
    assert body["vix"] == pytest.approx(vix_closes[-1])
    assert body["vix3m"] == pytest.approx(22.0)
    assert body["vix9d"] == pytest.approx(32.0)
    assert body["term_structure"] == "backwardation"
    assert body["term_structure_reason"] is None

    assert body["vvix"] == pytest.approx(90.0 + (n - 1) * 0.1)

    assert body["spy_rv20"] is not None
    assert body["vrp"] is not None
    assert body["vrp_reason"] is None

    assert body["sector_correlation"] is not None
    assert body["sector_correlation_universe_n"] == len(SECTORS)

    assert body["uup_return_20d"] > 0
    assert body["gld_return_20d"] < 0
    assert body["tlt_return_20d"] == pytest.approx(0.0)

    # Every declared field is present on the wire (no silently-dropped key).
    for key in (
        "as_of",
        "vix",
        "vix3m",
        "vix9d",
        "vix_vix3m_ratio",
        "vix_vix3m_ratio_pct",
        "vix_vix3m_ratio_pct_n",
        "vix9d_vix_ratio",
        "vix9d_vix_ratio_pct",
        "vix9d_vix_ratio_pct_n",
        "term_structure",
        "term_structure_reason",
        "vvix",
        "vvix_pct",
        "vvix_pct_n",
        "vix_pct",
        "vix_pct_n",
        "spy_rv20",
        "vrp",
        "vrp_pct",
        "vrp_pct_n",
        "vrp_reason",
        "sector_correlation",
        "sector_correlation_n",
        "sector_correlation_universe_n",
        "uup_return_20d",
        "gld_return_20d",
        "tlt_return_20d",
    ):
        assert key in body, key


def test_get_cross_asset_vix_pct_none_below_60_bars(client, session_factory, monkeypatch):
    n = 40
    today = dt.date(2026, 6, 5)
    monkeypatch.setattr("app.api.scan._today", lambda: today)
    start = today - dt.timedelta(days=n - 1)
    _seed(session_factory, "^VIX", [15.0 + i * 0.1 for i in range(n)], start=start)

    response = client.get("/api/scan/cross-asset")
    assert response.status_code == 200
    body = response.json()
    assert body["vix"] is not None
    assert body["vix_pct"] is None
    assert body["vix_pct_n"] == n
