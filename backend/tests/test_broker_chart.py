"""T159: the broker chart -- `app/modules/broker/chart.py` (pure) and `api/chart.py` (offline,
SQLite through `get_engine`'s schema map, the `broker.levels` view replaced)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_engine, get_sessionmaker
from app.modules.broker import chart as ch
from app.modules.broker.api.chart import router
from app.modules.broker.tables import Bar, Base

UTC = dt.UTC
SUMMER = 3 * 3600  # server = UTC + 3 in US summer


def _m1(start: dt.datetime, n: int, offset_s: int = SUMMER, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 2000 + np.cumsum(rng.normal(0, 1, n))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "ts": [start + dt.timedelta(minutes=i) for i in range(n)],
        "open": open_,
        "high": np.maximum(open_, close) + 0.5,
        "low": np.minimum(open_, close) - 0.5,
        "close": close,
        "tick_volume": 10,
        "spread": rng.integers(10, 20, n),
        "offset_s": offset_s,
    })


# --- resample ----------------------------------------------------------------------------------


def test_d1_buckets_are_cut_at_server_midnight_not_utc_midnight():
    # Server midnight in summer is 21:00 UTC. Three hours either side of it.
    m1 = _m1(dt.datetime(2026, 7, 14, 18, 0, tzinfo=UTC), 6 * 60)
    out = ch.resample(m1, 1440, horizon=dt.datetime(2026, 7, 20, tzinfo=UTC))
    assert list(out["time"]) == [
        pd.Timestamp("2026-07-13 21:00", tz="UTC"),
        pd.Timestamp("2026-07-14 21:00", tz="UTC"),
    ]


def test_h4_bucket_aggregates_ohlc_volume_and_min_spread():
    m1 = _m1(dt.datetime(2026, 7, 14, 13, 0, tzinfo=UTC), 240)  # 16:00-20:00 server
    out = ch.resample(m1, 240, horizon=dt.datetime(2026, 7, 20, tzinfo=UTC))
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == m1["open"].iloc[0]
    assert row["close"] == m1["close"].iloc[-1]
    assert row["high"] == m1["high"].max()
    assert row["low"] == m1["low"].min()
    assert row["tick_volume"] == 2400
    assert row["spread"] == m1["spread"].min()
    assert not row["forming"]


def test_bucket_ending_after_the_horizon_is_forming():
    start = dt.datetime(2026, 7, 14, 13, 0, tzinfo=UTC)
    m1 = _m1(start, 50)
    out = ch.resample(m1, 15, horizon=start + dt.timedelta(minutes=50))
    assert list(out["forming"]) == [False, False, False, True]


def test_recent_stop_is_ingest_lag_so_the_cut_bucket_is_forming():
    start = dt.datetime(2026, 7, 14, 13, 0, tzinfo=UTC)
    m1 = _m1(start, 50)  # data through 13:50; the 13:45 bucket ends 14:00
    out = ch.resample(m1, 15, horizon=start + dt.timedelta(minutes=58))
    assert out["forming"].iloc[-1]


def test_old_stop_is_a_closed_market_so_the_cut_bucket_is_complete():
    # Friday close mid-bucket, chart drawn on Sunday.
    start = dt.datetime(2026, 7, 17, 19, 0, tzinfo=UTC)
    m1 = _m1(start, 50)
    out = ch.resample(m1, 60, horizon=dt.datetime(2026, 7, 19, 12, tzinfo=UTC))
    assert not out["forming"].any()


# --- detectors see complete bars only --------------------------------------------------------


def test_no_event_is_confirmed_on_the_forming_bar():
    start = dt.datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    m1 = _m1(start, 15 * 200 + 7, seed=3)
    horizon = start + dt.timedelta(minutes=15 * 200 + 7)
    bars = ch.resample(m1, 15, horizon)
    assert bars["forming"].iloc[-1]
    events, _ = ch.detect(bars)
    assert events
    forming_time = bars["time"].iloc[-1].to_pydatetime()
    assert all(e.confirmed_time < forming_time for e in events)


def test_events_equal_a_run_without_the_forming_bar():
    start = dt.datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    m1 = _m1(start, 15 * 120 + 7, seed=5)
    horizon = start + dt.timedelta(minutes=15 * 120 + 7)
    bars = ch.resample(m1, 15, horizon)
    with_forming, _ = ch.detect(bars)
    without, _ = ch.detect(bars.iloc[:-1])
    assert with_forming == without


def test_recent_zones_merge_touches_of_one_level():
    start = dt.datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    m1 = _m1(start, 15 * 400, seed=7)
    bars = ch.resample(m1, 15, start + dt.timedelta(days=30))
    _, zone_events = ch.detect(bars)
    zones = ch.recent_zones(bars, zone_events)
    assert len(zones) <= ch.ZONE_LIMIT
    assert sum(z.touches for z in zones) >= len(zones) * 2
    times = [z.last_time for z in zones]
    assert times == sorted(times, reverse=True)


# --- levels as of each bar -------------------------------------------------------------------


def _levels(*rows: tuple[str, float]) -> pd.DataFrame:
    return pd.DataFrame({
        "captured_at": [pd.Timestamp(t, tz="UTC") for t, _ in rows],
        "call_wall": [w for _, w in rows],
        "put_wall": [w - 50 for _, w in rows],
        "flip_point": [w - 25 for _, w in rows],
        "spot": [w - 20 for _, w in rows],
    })


def test_a_bar_never_sees_a_snapshot_captured_after_its_close():
    start = dt.datetime(2026, 7, 14, 14, 0, tzinfo=UTC)
    bars = ch.resample(_m1(start, 60), 15, start + dt.timedelta(days=1))
    # 14:20 is inside the 14:15 bar, which closes at 14:30, so that bar is the first to see it.
    # The 14:00 bar closes at 14:15 and must not.
    lv = _levels(("2026-07-14 13:55", 100.0), ("2026-07-14 14:20", 200.0))
    out = ch.levels_per_bar(bars, lv, start + dt.timedelta(days=1))
    assert list(out["call_wall"]) == [100.0, 200.0, 200.0, 200.0]


def test_a_level_older_than_the_cap_is_not_drawn():
    start = dt.datetime(2026, 7, 14, 14, 0, tzinfo=UTC)
    bars = ch.resample(_m1(start, 30), 15, start + dt.timedelta(days=1))
    lv = _levels(("2026-07-01 14:00", 100.0))
    assert ch.levels_per_bar(bars, lv, start + dt.timedelta(days=1)).empty


def test_forming_bar_sees_levels_only_up_to_the_horizon():
    start = dt.datetime(2026, 7, 14, 14, 0, tzinfo=UTC)
    horizon = start + dt.timedelta(minutes=20)
    bars = ch.resample(_m1(start, 20), 15, horizon)
    lv = _levels(("2026-07-14 14:10", 100.0), ("2026-07-14 14:25", 999.0))
    out = ch.levels_per_bar(bars, lv, horizon)
    assert list(out["call_wall"]) == [100.0, 100.0]


# --- API -------------------------------------------------------------------------------------


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = get_engine(f"sqlite:///{tmp_path / 'broker.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    monkeypatch.setattr("app.modules.broker.api.chart.get_session_factory", lambda: factory)
    monkeypatch.setattr(
        "app.modules.broker.repository.settings.BROKER_SYMBOLS", "XAUUSD,EURUSD"
    )
    yield factory
    engine.dispose()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/broker")
    return TestClient(app)


def _store(factory, m1: pd.DataFrame, symbol: str = "XAUUSD") -> None:
    now = dt.datetime(2026, 7, 20, tzinfo=UTC)
    with factory() as session:
        session.add_all(
            Bar(symbol=symbol, timeframe="M1", ts=r.ts, open=r.open, high=r.high, low=r.low,
                close=r.close, tick_volume=int(r.tick_volume), spread=int(r.spread),
                real_volume=0, offset_s=int(r.offset_s), ingested_at=now)
            for r in m1.itertuples(index=False)
        )
        session.commit()


def test_symbols_lists_configured_cfds_with_pairs(client, factory):
    _store(factory, _m1(dt.datetime(2026, 7, 14, tzinfo=UTC), 5))
    body = client.get("/api/broker/symbols").json()
    assert [s["symbol"] for s in body] == ["XAUUSD", "EURUSD"]
    assert body[0]["pair"] == {"desk": "GLD", "method": "ratio", "proxy": False}
    assert body[0]["last_bar_at"].startswith("2026-07-14T00:04")
    assert body[1] == {"symbol": "EURUSD", "last_bar_at": None, "pair": None}


def test_chart_returns_bars_events_and_levels_as_of_end(client, factory, monkeypatch):
    start = dt.datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    _store(factory, _m1(start, 15 * 300, seed=11))
    seen = {}

    def fake_levels(cfd, desk, *, start, end, session_factory):
        seen.update(cfd=cfd, desk=desk, end=end)
        lv = _levels(("2026-07-14 20:00", 2100.0), ("2026-07-15 20:00", 2200.0))
        return lv.assign(basis_at=lv["captured_at"], basis_kind="5m", roll_after_basis=False)

    monkeypatch.setattr("app.modules.broker.repository.levels", fake_levels)
    end = "2026-07-15T12:07:00+00:00"
    r = client.get("/api/broker/chart", params={"symbol": "XAUUSD", "tf": "M15",
                                                 "bars": 100, "end": end})
    assert r.status_code == 200
    body = r.json()
    assert len(body["bars"]) == 100
    assert body["bars"][-1]["forming"] is True  # 12:00 bucket ends 12:15 > end
    assert all(not b["forming"] for b in body["bars"][:-1])
    assert pd.Timestamp(body["last_bar_at"]) <= pd.Timestamp(end) - pd.Timedelta(minutes=1)
    assert body["events"]
    assert seen == {"cfd": "XAUUSD", "desk": "GLD", "end": pd.Timestamp(end).to_pydatetime()}
    # Only the 07-14 snapshot exists at this horizon.
    assert {lv["call_wall"] for lv in body["levels"]} == {2100.0}
    assert body["level_basis"]["captured_at"].startswith("2026-07-14T20:00")


def test_chart_unknown_symbol_is_404_and_naive_end_is_422(client, factory):
    assert client.get("/api/broker/chart", params={"symbol": "NOPE"}).status_code == 404
    r = client.get("/api/broker/chart", params={"symbol": "XAUUSD", "end": "2026-07-15T12:00"})
    assert r.status_code == 422


def test_chart_with_no_bars_is_empty_not_an_error(client, factory):
    body = client.get("/api/broker/chart", params={"symbol": "EURUSD"}).json()
    assert body["bars"] == [] and body["events"] == [] and body["levels"] == []
    assert body["pair"] is None
