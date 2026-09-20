"""Tests for `app/modules/gex/api/decisions.py` (T60). Offline: the two session factories `app.modules.gex.api.scan`
reads are monkeypatched at their import site onto one SQLite engine -- exactly the fixture
shape `test_scan_api.py`'s regime tests use, since this router runs on the same
`build_regime_rows` pipeline.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.api.decisions import router
from app.modules.gex.models.bars import DailyBar as DailyBarIn
from app.modules.gex.models.chain import Underlying
from app.modules.gex.models.db import Base, GexByStrike, GexLevel, Snapshot
from app.modules.gex.storage.bars_repository import upsert_bars

_NY = ZoneInfo("America/New_York")


@pytest.fixture
def client():
    app = FastAPI()
    # T75: `/api/gex`, matching how `app/main.py` mounts `app.modules.gex.router` -- these
    # per-router mini-apps exist to keep the tests offline, not to serve a different URL space.
    app.include_router(router, prefix="/api/gex")
    return TestClient(app)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    monkeypatch.setattr("app.modules.gex.api.scan.get_session_factory", lambda: factory)
    monkeypatch.setattr("app.modules.gex.api.scan.get_gex_session_factory", lambda: factory)
    # T61: the history/record routes read the `decisions` and bars tables through the
    # repositories' own cached factories -- point both at the same SQLite engine.
    monkeypatch.setattr("app.modules.gex.storage.decisions_repository.get_session_factory", lambda: factory)
    monkeypatch.setattr("app.modules.gex.storage.bars_repository.get_session_factory", lambda: factory)
    # A one-symbol scan universe keeps the trend ranking cheap; the regime pipeline still
    # iterates every `Underlying` member for the universe route.
    from app.core import config

    monkeypatch.setattr(config.settings, "SCAN_UNIVERSE", "SPY")
    yield factory
    engine.dispose()


def _ny(y, m, d, h, mi) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, tzinfo=_NY).astimezone(dt.UTC)


def _seed_bars(session_factory, symbol: str, n_days: int, *, start_close: float = 90.0) -> None:
    """`n_days` rising bars with a constant true range of 2.0, so ATR14 settles at 2.0."""
    bars = []
    day = dt.date(2025, 10, 1)
    close = start_close
    made = 0
    while made < n_days:
        if day.weekday() < 5:
            bars.append(
                DailyBarIn(
                    symbol=symbol,
                    date=day,
                    open=close,
                    high=close + 0.5,
                    low=close - 1.5,
                    close=close,
                    volume=1_000,
                    source="test",
                )
            )
            close += 0.1
            made += 1
        day += dt.timedelta(days=1)
    upsert_bars(bars, session_factory=session_factory)


def _seed_fade_snapshot(session_factory, underlying: str = "SPY") -> int:
    """A fresh EOD snapshot arranged like `test_scan_api.py`'s fade case: walls 0.75 ATR either
    side of spot 100, flip 3 ATR below (ATR is 2.0 from `_seed_bars`)."""
    captured_at = _ny(2026, 1, 5, 16, 20)
    with session_factory() as session:
        snap = Snapshot(
            underlying=underlying,
            captured_at=captured_at,
            source="cboe",
            spot=100.0,
            contract_count=100,
            parquet_path=f"{underlying}/unused.parquet",
            is_eod=True,
        )
        session.add(snap)
        session.commit()
        snap_id = snap.id
        session.add(
            GexLevel(
                snapshot_id=snap_id,
                filter="ALL",
                net_gex=5.0e9,
                call_wall=101.5,
                call_wall_gex=8.0e9,
                put_wall=98.5,
                put_wall_gex=-6.0e9,
                max_abs_strike=101.5,
                max_call_gex_strike=101.5,
                max_put_gex_strike=98.5,
                flip_point=94.0,
                spot=100.0,
                computed_at=captured_at,
            )
        )
        for strike, call_gex, put_gex in ((98.5, 0.0, -6.0e9), (101.5, 8.0e9, 0.0)):
            session.add(
                GexByStrike(
                    snapshot_id=snap_id,
                    filter="ALL",
                    strike=strike,
                    call_gex=call_gex,
                    put_gex=put_gex,
                    net_gex=call_gex + put_gex,
                )
            )
        session.commit()
    return snap_id


def test_unknown_symbol_is_422(client, session_factory):
    assert client.get("/api/gex/decisions/NOPE").status_code == 422


def test_unpersisted_filter_is_422(client, session_factory):
    response = client.get("/api/gex/decisions?filter=THIS_WEEK")
    assert response.status_code == 422
    assert "persisted" in response.json()["detail"]


def test_never_captured_symbol_is_a_clean_404_with_the_gex_router_detail(client, session_factory):
    response = client.get("/api/gex/decisions/SPY")
    assert response.status_code == 404
    assert response.json()["detail"] == "no snapshot captured yet for SPY"


def test_universe_route_with_nothing_captured_lists_every_symbol_under_no_chain(client, session_factory):
    response = client.get("/api/gex/decisions")
    assert response.status_code == 200
    body = response.json()
    assert body["filter"] == "ALL"
    assert body["ranked"] == []
    assert body["symbols"] == []
    assert set(body["no_chain"]) == {u.value for u in Underlying}
    assert "never" not in body["generated_from"] or "routed" in body["generated_from"]


def test_seeded_fade_symbol_yields_two_ranked_fades(client, session_factory):
    _seed_bars(session_factory, "SPY", 60)
    _seed_fade_snapshot(session_factory)

    response = client.get("/api/gex/decisions")
    assert response.status_code == 200
    body = response.json()

    assert [s["underlying"] for s in body["symbols"]] == ["SPY"]
    assert "SPY" not in body["no_chain"]
    spy = body["symbols"][0]
    assert spy["verdict"] == "fade"
    assert spy["stale"] is False
    assert spy["atr14"] == pytest.approx(2.0)
    assert spy["no_trade_reasons"] == []
    assert {o["key"] for o in spy["opportunities"]} == {"FADE_CALL_WALL", "FADE_PUT_WALL"}

    assert len(body["ranked"]) == 2
    for opp in body["ranked"]:
        assert opp["underlying"] == "SPY"
        assert opp["spot"] == 100.0
        assert opp["status"] == "active"
        assert opp["thesis"] and opp["invalidation"]
        assert sum(c["max_points"] for c in opp["score_breakdown"]) == 100
    short = next(o for o in body["ranked"] if o["key"] == "FADE_CALL_WALL")
    assert short["entry"] == 101.5
    assert short["stop"] == pytest.approx(102.5)  # +0.5 ATR of 2.0
    assert short["target"] == 98.5
    # Bars were seeded, so the breakout ledger is a real (if empty) summary and the trend
    # composite is a real percentile -- neither score component reads "unavailable".
    assert not any("unavailable" in c["note"] for c in short["score_breakdown"] if c["name"] == "trend context")


def test_min_score_filters_ranked_but_never_the_symbol_rows(client, session_factory):
    _seed_bars(session_factory, "SPY", 60)
    _seed_fade_snapshot(session_factory)

    body = client.get("/api/gex/decisions?min_score=100").json()
    assert body["ranked"] == []
    assert len(body["symbols"][0]["opportunities"]) == 2

    assert client.get("/api/gex/decisions?min_score=101").status_code == 422


def test_symbol_route_matches_the_universe_row(client, session_factory):
    _seed_bars(session_factory, "SPY", 60)
    _seed_fade_snapshot(session_factory)

    single = client.get("/api/gex/decisions/spy").json()
    universe = client.get("/api/gex/decisions").json()
    assert single == universe["symbols"][0]


def test_symbol_without_bars_reports_no_trade_for_missing_atr(client, session_factory):
    _seed_fade_snapshot(session_factory)
    body = client.get("/api/gex/decisions/SPY").json()
    assert body["atr14"] is None
    assert body["opportunities"] == []
    assert any("ATR" in r for r in body["no_trade_reasons"])


# --- T61: history and manual record ---------------------------------------------------------


def test_history_is_empty_with_a_zeroed_summary_before_any_record(client, session_factory):
    body = client.get("/api/gex/decisions/history").json()
    assert body["records"] == []
    assert body["summary"]["overall"]["n"] == 0
    assert body["summary"]["overall"]["hit_rate"] is None
    assert "in R" in body["note"]


def test_history_rejects_an_unknown_outcome(client, session_factory):
    assert client.get("/api/gex/decisions/history?outcome=won").status_code == 422


def test_record_then_history_round_trip(client, session_factory):
    _seed_bars(session_factory, "SPY", 60)
    _seed_fade_snapshot(session_factory)

    run = client.post("/api/gex/decisions/record")
    assert run.status_code == 201
    assert run.json()["recorded"] == 2
    assert run.json()["errors"] == []

    body = client.get("/api/gex/decisions/history?underlying=spy").json()
    assert [r["key"] for r in body["records"]] == ["FADE_CALL_WALL", "FADE_PUT_WALL"]
    record = body["records"][0]
    assert record["outcome"] == "pending"
    assert record["opportunity"]["thesis"]
    assert body["summary"]["overall"]["pending"] == 2
    assert body["summary"]["by_setup"]["fade"]["n"] == 2

    assert client.post("/api/gex/decisions/record").json()["recorded"] == 0
