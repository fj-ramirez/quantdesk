"""Tests for `app/modules/research/signals.py` and its store (T135). Offline, synthetic bars."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.research.models.db import Base
from app.modules.research.signals import (
    continuation_events,
    zscore_events,
    zscore_variants,
)
from app.modules.research.storage.signal_events import record_events
from app.modules.research.strategies import FAMILIES

PARAMS = {"n": 20, "entry_z": 1.5, "exit_z": 0.0, "long_short": False, "regime": "any"}


def _hourly(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-09-01", periods=len(closes), freq="1h", tz="UTC")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0})


def _dip_then_recover() -> pd.DataFrame:
    base = [100.1 if i % 2 else 99.9 for i in range(60)]  # |z| stays near 1: no signal of its own
    return _hourly(base + [97.0, 96.5] + [100.5] * 3)  # a two-bar dip, then back above the mean


def test_grid_is_the_120_ninjatrader_classes():
    names = [name for name, _ in zscore_variants()]
    assert len(names) == len(set(names)) == 120
    # names the meta-trader-strategies generator writes, character for character
    assert "ZScoreDip_N160_E1p5_X0p5_HighVol" in names
    assert "ZScoreDip_N160_E3_X0p5_TrendDown" in names
    assert "ZScoreDip_N80_E1p5_X0_Any" in names


def test_zscore_events_are_exactly_the_strategys_position_changes():
    df = _dip_then_recover()
    events = zscore_events(df, "ES=F", "Test", PARAMS, window=len(df))
    pos = FAMILIES["zscore_meanrev"].positions(df, PARAMS)
    changes = [df.index[i] for i in range(1, len(df)) if pos.iloc[i] != pos.iloc[i - 1]]
    assert [pd.Timestamp(e.bar_ts) for e in events] == changes
    assert [e.action for e in events] == ["ENTER", "EXIT"]
    assert events[0].price == 97.0 and "< -1.5" in events[0].reason
    assert all(e.side == "LONG" and e.late for e in events)  # neither is on the newest bar


def test_window_limits_what_is_reported_and_marks_the_newest_bar_fresh():
    df = _dip_then_recover().iloc[:61]  # ends on the first dip bar
    events = zscore_events(df, "ES=F", "Test", PARAMS, window=3)
    assert len(events) == 1 and events[0].action == "ENTER" and events[0].late is False
    assert zscore_events(df.iloc[:60], "ES=F", "Test", PARAMS, window=3) == []


def test_a_regime_that_never_warms_up_never_trades():
    df = _dip_then_recover().iloc[:62]
    trend = dict(PARAMS, regime="trend_down")  # 200-bar SMA never warms up on 62 bars: always off
    assert zscore_events(df, "ES=F", "Test", trend, window=len(df)) == []


def _daily(closes: list[float], *, spread: float = 0.5) -> pd.DataFrame:
    dates, day = [], dt.date(2026, 1, 1)
    while len(dates) < len(closes):
        if day.weekday() < 5:
            dates.append(day)
        day += dt.timedelta(days=1)
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"date": dates, "open": c, "high": c + spread, "low": c - spread, "close": c})


def test_continuation_short_enters_on_a_falling_week_and_exits_through_the_desk_scorer():
    closes = [100.0] * 20 + [99.0, 98.0, 97.0, 96.0, 95.0]  # five down sessions
    entry_bars = _daily(closes)
    events = continuation_events(entry_bars, "HYG", "SHORT", window=5)
    enter = [e for e in events if e.action == "ENTER"]
    assert enter and enter[-1].late is False
    e = enter[-1]
    assert e.signal == "ContinuationProxy_Short" and e.side == "SHORT" and e.price == 95.0
    assert e.stop > e.price > e.target
    assert e.target - e.price == pytest.approx(-2 * (e.stop - e.price))

    # next session gaps down through that trade's target: the desk scorer exits at the open
    bars = _daily(closes + [80.0])
    exits = [
        x for x in continuation_events(bars, "HYG", "SHORT", window=2)
        if x.action == "EXIT" and x.stop == e.stop and x.target == e.target
    ]
    assert len(exits) == 1 and exits[0].reason.startswith("target") and exits[0].price == pytest.approx(80.0)
    assert exits[0].late is False


def test_continuation_never_enters_against_its_side():
    closes = [100.0] * 20 + [101.0, 102.0, 103.0, 104.0, 105.0]
    assert [e for e in continuation_events(_daily(closes), "XLU", "SHORT") if e.action == "ENTER"] == []
    assert continuation_events(_daily(closes), "XLU", "LONG")[-1].action == "ENTER"


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'signals.db'}")
    Base.metadata.create_all(engine)
    yield get_sessionmaker(engine)
    engine.dispose()


def test_store_inserts_each_event_once_and_never_rewrites_it(session_factory):
    df = _dip_then_recover().iloc[:61]
    fresh = zscore_events(df, "ES=F", "Test", PARAMS, window=3)
    assert record_events(fresh, session_factory=session_factory) == fresh

    # The next run re-reads the window: the same event is now `late`, and must stay as recorded.
    df2 = _dip_then_recover().iloc[:62]
    again = zscore_events(df2, "ES=F", "Test", PARAMS, window=3)
    assert any(e.late for e in again)
    assert record_events(again, session_factory=session_factory) == []
