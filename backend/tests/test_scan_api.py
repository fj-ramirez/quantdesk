"""Tests for `app/api/scan.py`. Offline: `get_session_factory` is monkeypatched at its import
site in `app.api.scan`, no real Postgres -- same pattern as `test_bars_api.py`. Timing against
the live, populated database (T43 acceptance: "returns within 2 s for 45 symbols x 500 bars")
is measured separately, outside pytest, and recorded in `app/api/scan.py`'s module docstring
and in the T43 report -- it cannot be an automated test without violating "every test must be
offline" (T43 task brief).
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.scan import router
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
    monkeypatch.setattr("app.api.scan.get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


def _universe(monkeypatch, symbols: list[str]) -> None:
    from app import config

    monkeypatch.setattr(config.settings, "SCAN_UNIVERSE", ",".join(symbols))


def _bar(symbol: str, date: dt.date, close: float, high=None, low=None, volume=1_000) -> DailyBarIn:
    return DailyBarIn(
        symbol=symbol,
        date=date,
        open=close,
        high=high if high is not None else close + 0.5,
        low=low if low is not None else close - 0.5,
        close=close,
        volume=volume,
        source="test",
    )


def _seed_monotone(
    session_factory,
    symbol: str,
    n_days: int,
    *,
    start: dt.date = dt.date(2026, 1, 5),
    start_close: float = 100.0,
) -> None:
    """A strictly increasing daily-close series -- every recorded event on it continues."""
    bars = [
        _bar(symbol, start + dt.timedelta(days=i), start_close + i, high=start_close + i + 0.5, low=start_close + i - 1.5)
        for i in range(n_days)
    ]
    upsert_bars(bars, session_factory=session_factory)


def _seed_flat_then_breakout(
    session_factory,
    symbol: str,
    *,
    n: int,
    baseline_days: int,
    trailing_days: int,
    start: dt.date = dt.date(2026, 1, 5),
) -> None:
    """`baseline_days` (>= n) flat bars establishing a clean range, one breakout bar, then
    `trailing_days` more bars -- used to control exactly how many bars have elapsed since the
    breakout (e.g. fewer than k, so it stays `pending`).
    """
    bars = []
    d = start
    for _ in range(baseline_days):
        bars.append(_bar(symbol, d, 100.0, high=100.5, low=99.5))
        d += dt.timedelta(days=1)
    bars.append(_bar(symbol, d, 110.0, high=110.5, low=109.5))  # the breakout bar
    d += dt.timedelta(days=1)
    for _ in range(trailing_days):
        bars.append(_bar(symbol, d, 111.0, high=111.5, low=110.5))
        d += dt.timedelta(days=1)
    upsert_bars(bars, session_factory=session_factory)


# --- GET /api/scan/breakouts -----------------------------------------------------------------


def test_get_breakouts_returns_a_summary_per_universe_symbol(client, session_factory, monkeypatch):
    _seed_monotone(session_factory, "SPY", 60)
    _seed_monotone(session_factory, "QQQ", 60)
    _universe(monkeypatch, ["SPY", "QQQ"])

    response = client.get("/api/scan/breakouts", params={"n": 20, "k": 5, "lookback": 40})

    assert response.status_code == 200
    body = response.json()
    assert body["n"] == 20
    assert body["k"] == 5
    assert body["lookback"] == 40
    symbols = {row["symbol"] for row in body["summaries"]}
    assert symbols == {"SPY", "QQQ"}
    for row in body["summaries"]:
        assert row["events"] >= 0
        assert row["status"] in (None, "continued", "failed", "pending")


def test_get_breakouts_default_params_match_the_plan_defaults(client, session_factory, monkeypatch):
    _seed_monotone(session_factory, "SPY", 60)
    _universe(monkeypatch, ["SPY"])

    response = client.get("/api/scan/breakouts")
    assert response.status_code == 200
    body = response.json()
    assert body["n"] == 20
    assert body["k"] == 5
    assert body["lookback"] == 126


def test_get_breakouts_rejects_invalid_n(client, session_factory, monkeypatch):
    _universe(monkeypatch, ["SPY"])
    response = client.get("/api/scan/breakouts", params={"n": 21})
    assert response.status_code == 422


def test_get_breakouts_rejects_invalid_k(client, session_factory, monkeypatch):
    _universe(monkeypatch, ["SPY"])
    response = client.get("/api/scan/breakouts", params={"k": 4})
    assert response.status_code == 422


def test_get_breakouts_open_breakouts_panel_lists_pending_events(client, session_factory, monkeypatch):
    _seed_flat_then_breakout(
        session_factory, "SPY", n=20, baseline_days=20, trailing_days=3
    )  # 3 bars elapsed, k=10 -> still pending
    _universe(monkeypatch, ["SPY"])

    response = client.get("/api/scan/breakouts", params={"n": 20, "k": 10, "lookback": 126})
    assert response.status_code == 200
    body = response.json()

    assert len(body["open_breakouts"]) == 1
    open_row = body["open_breakouts"][0]
    assert open_row["symbol"] == "SPY"
    assert open_row["direction"] == "up"
    assert open_row["bars_elapsed"] == 3

    spy_summary = next(row for row in body["summaries"] if row["symbol"] == "SPY")
    assert spy_summary["pending"] == 1
    assert spy_summary["status"] == "pending"


def test_get_breakouts_excludes_a_symbol_with_a_gappy_history(client, session_factory, monkeypatch):
    # A 2026 Monday start (a year `app.jobs.calendar` actually covers) with rows only every 3
    # calendar days -- roughly half the expected trading days in the span are missing, well
    # over the 2% exclusion threshold.
    start = dt.date(2026, 1, 5)
    bars = [_bar("GAPPY", start + dt.timedelta(days=3 * i), 100.0 + i) for i in range(10)]
    upsert_bars(bars, session_factory=session_factory)
    _universe(monkeypatch, ["GAPPY"])

    response = client.get("/api/scan/breakouts", params={"lookback": 10})
    assert response.status_code == 200
    body = response.json()

    assert body["summaries"] == []
    assert body["open_breakouts"] == []
    assert len(body["excluded"]) == 1
    assert body["excluded"][0]["symbol"] == "GAPPY"
    assert "missing" in body["excluded"][0]["reason"]


def test_get_breakouts_symbol_with_no_bars_contributes_an_empty_summary(client, session_factory, monkeypatch):
    _universe(monkeypatch, ["NEWLY_ADDED"])
    response = client.get("/api/scan/breakouts")
    assert response.status_code == 200
    body = response.json()
    assert len(body["summaries"]) == 1
    row = body["summaries"][0]
    assert row["symbol"] == "NEWLY_ADDED"
    assert row["events"] == 0
    assert row["rate"] is None
    assert row["last_event"] is None
    assert row["status"] is None


# --- GET /api/scan/breakouts/{symbol} -----------------------------------------------------


def test_get_symbol_breakouts_returns_event_list(client, session_factory, monkeypatch):
    _seed_monotone(session_factory, "SPY", 60)
    response = client.get("/api/scan/breakouts/SPY", params={"n": 20, "k": 5, "lookback": 40})
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "SPY"
    assert body["n"] == 20
    assert body["k"] == 5
    assert isinstance(body["events"], list)
    assert len(body["events"]) > 0
    for event in body["events"]:
        assert event["direction"] == "up"
        assert set(event) >= {
            "date",
            "direction",
            "level",
            "close",
            "outcome",
            "resolved_at",
            "bars_elapsed",
            "follow_through_atr",
            "excursion_atr",
            "mfe_atr",
            "mae_atr",
        }


def test_get_symbol_breakouts_no_bars_returns_clean_empty_result(client, session_factory):
    response = client.get("/api/scan/breakouts/NOPE")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "NOPE"
    assert body["events"] == []


def test_get_symbol_breakouts_is_case_insensitive_and_normalizes_symbol(client, session_factory):
    _seed_monotone(session_factory, "SPY", 60)
    response = client.get("/api/scan/breakouts/spy")
    assert response.status_code == 200
    assert response.json()["symbol"] == "SPY"


def test_get_symbol_breakouts_percent_encoded_caret_symbol(client, session_factory):
    _seed_monotone(session_factory, "^VIX", 60)
    response = client.get("/api/scan/breakouts/%5EVIX")
    assert response.status_code == 200
    assert response.json()["symbol"] == "^VIX"


def test_get_symbol_breakouts_rejects_invalid_n(client, session_factory):
    response = client.get("/api/scan/breakouts/SPY", params={"n": 7})
    assert response.status_code == 422


def test_get_symbol_breakouts_not_excluded_for_gaps_unlike_universe_route(client, session_factory):
    """The single-symbol lookup is an explicit request, not a scan aggregate -- a gappy series
    still returns whatever events it has rather than being silently dropped."""
    start = dt.date(2026, 1, 5)
    bars = [_bar("GAPPY", start + dt.timedelta(days=3 * i), 100.0 + i) for i in range(30)]
    upsert_bars(bars, session_factory=session_factory)
    response = client.get("/api/scan/breakouts/GAPPY", params={"lookback": 10})
    assert response.status_code == 200
    assert response.json()["symbol"] == "GAPPY"
