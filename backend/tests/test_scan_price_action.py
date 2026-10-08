"""Tests for `app/modules/gex/scan/price_action.py` (T146, plans/charter-mt5/README.md).

Fully offline. Hand-built fixtures pin each detector's reading, and one property test pins the
module's central promise: **a detector run on `bars[: t + 1]` reports exactly the events the
full-series run reports with `confirmed <= t`.** If that ever fails, a detector is looking ahead.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.modules.gex.scan.price_action import (
    Event,
    bar_patterns,
    breaks,
    structure,
    sweeps,
    swings,
    to_frame,
    zones,
)


def _bars(highs, lows, closes=None, opens=None) -> pd.DataFrame:
    n = len(highs)
    closes = closes if closes is not None else [(h + lo) / 2 for h, lo in zip(highs, lows)]
    opens = opens if opens is not None else closes
    assert len(lows) == len(closes) == len(opens) == n
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


def _random_walk(seed: int, n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    open_ = np.concatenate([[100.0], close[:-1]]) + rng.normal(0, 0.2, n)
    high = np.maximum(open_, close) + rng.exponential(0.5, n)
    low = np.minimum(open_, close) - rng.exponential(0.5, n)
    # Rounded to a tick so ties actually occur and the tie rule gets exercised.
    return pd.DataFrame(
        {"open": open_.round(1), "high": high.round(1), "low": low.round(1), "close": close.round(1)}
    )


# --------------------------------------------------------------------------- swings


def test_swing_high_is_confirmed_right_bars_later():
    bars = _bars([1, 2, 5, 3, 2, 1, 1], [0, 1, 4, 2, 1, 0, 0])
    highs = [e for e in swings(bars, left=2, right=2) if e.kind == "swing_high"]
    assert highs == [Event("swing_high", "down", 2, 4, 5.0)]


def test_flat_top_yields_one_swing_at_its_first_bar():
    bars = _bars([1, 2, 5, 5, 3, 2, 1], [0, 1, 4, 4, 2, 1, 0])
    highs = [e for e in swings(bars, left=2, right=2) if e.kind == "swing_high"]
    assert [(e.occurred, e.confirmed) for e in highs] == [(2, 4)]


def test_no_swing_without_a_full_right_window():
    bars = _bars([1, 2, 3, 4, 5, 6, 9, 8], [0, 1, 2, 3, 4, 5, 8, 7])
    assert [e for e in swings(bars, left=2, right=2) if e.kind == "swing_high"] == []


def test_swing_low_mirror():
    bars = _bars([9, 8, 6, 7, 8, 9], [8, 7, 3, 6, 7, 8])
    lows = [e for e in swings(bars, left=2, right=2) if e.kind == "swing_low"]
    assert lows == [Event("swing_low", "up", 2, 4, 3.0)]


def test_swings_rejects_empty_windows():
    with pytest.raises(ValueError):
        swings(_bars([1, 2], [0, 1]), left=0)


# --------------------------------------------------------------------------- structure


def test_structure_labels_against_previous_same_side_swing():
    sw = [
        Event("swing_high", "down", 2, 4, 10.0),
        Event("swing_low", "up", 5, 7, 5.0),
        Event("swing_high", "down", 8, 10, 12.0),
        Event("swing_low", "up", 11, 13, 5.0),
        Event("swing_high", "down", 14, 16, 12.0),
    ]
    got = [(e.kind, e.occurred, e.confirmed, e.ref) for e in structure(sw)]
    # The equal low is HL and the equal high LH: not exceeding the last extreme is not a new one.
    assert got == [("HH", 8, 10, 2), ("HL", 11, 13, 5), ("LH", 14, 16, 8)]


# --------------------------------------------------------------------------- breaks


def test_first_break_is_bos_then_against_trend_is_choch():
    closes = [5, 5, 5, 5, 5, 11, 5, 3, 5]
    bars = _bars([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)
    sw = [
        Event("swing_high", "down", 1, 3, 10.0),
        Event("swing_low", "up", 2, 4, 4.0),
    ]
    got = [(e.kind, e.direction, e.occurred, e.price, e.ref) for e in breaks(bars, sw)]
    assert got == [("bos", "up", 5, 10.0, 1), ("choch", "down", 7, 4.0, 2)]


def test_break_ignores_swing_not_yet_confirmed():
    closes = [5, 5, 11, 5]
    bars = _bars([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)
    sw = [Event("swing_high", "down", 0, 3, 10.0)]  # known only from bar 3
    assert breaks(bars, sw) == []


def test_a_swing_is_broken_once():
    closes = [5, 11, 12, 13]
    bars = _bars([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)
    sw = [Event("swing_high", "down", 0, 0, 10.0)]
    assert len(breaks(bars, sw)) == 1


# --------------------------------------------------------------------------- zones


def test_two_swings_within_tolerance_form_a_zone_at_the_second_confirmation():
    n = 20
    bars = _bars([11.0] * n, [9.0] * n)  # true range 2 everywhere -> ATR 2 once warm
    sw = [
        Event("swing_high", "down", 14, 15, 100.0),
        Event("swing_low", "up", 16, 17, 100.4),  # 0.4 <= 0.25 * 2 = 0.5
        Event("swing_high", "down", 18, 19, 105.0),  # far away: its own cluster
    ]
    got = zones(bars, sw, atr_period=14)
    assert [(e.confirmed, e.price, e.direction) for e in got] == [(17, pytest.approx(100.2), "up")]


def test_zone_skips_swings_before_atr_exists():
    bars = _bars([11.0] * 20, [9.0] * 20)
    sw = [Event("swing_high", "down", 1, 2, 100.0), Event("swing_high", "down", 3, 4, 100.0)]
    assert zones(bars, sw, atr_period=14) == []


# --------------------------------------------------------------------------- bar patterns


def test_inside_and_outside():
    bars = _bars([10, 9, 11], [5, 6, 4], closes=[7, 7, 7], opens=[7.5, 7.5, 7.5])
    kinds = [(e.kind, e.occurred) for e in bar_patterns(bars) if e.kind in ("inside", "outside")]
    assert kinds == [("inside", 1), ("outside", 2)]


def test_pin_bar_with_long_lower_wick_is_up():
    bars = _bars([10.0], [0.0], closes=[9.5], opens=[9.0])  # body 0.5, lower wick 9
    assert [(e.kind, e.direction) for e in bar_patterns(bars)] == [("pin", "up")]


def test_zero_range_bar_is_never_a_pin():
    bars = _bars([5.0], [5.0], closes=[5.0], opens=[5.0])
    assert bar_patterns(bars) == []


def test_bullish_engulfing():
    bars = _bars([10, 10.5], [8, 7.5], closes=[8.5, 10], opens=[9.5, 8])
    got = [(e.kind, e.direction, e.occurred) for e in bar_patterns(bars) if e.kind == "engulfing"]
    assert got == [("engulfing", "up", 1)]


def test_doji_is_never_engulfed():
    bars = _bars([10, 11], [8, 7], closes=[9, 10.5], opens=[9, 7.5])
    assert [e for e in bar_patterns(bars) if e.kind == "engulfing"] == []


# --------------------------------------------------------------------------- sweeps


def test_wick_through_high_closing_back_inside_is_a_down_sweep():
    bars = _bars([9, 9, 10.5], [8, 8, 8.5], closes=[8.5, 8.5, 9.5])
    sw = [Event("swing_high", "down", 0, 1, 10.0)]
    got = sweeps(bars, sw)
    assert [(e.kind, e.direction, e.occurred, e.price, e.ref) for e in got] == [
        ("sweep", "down", 2, 10.0, 0)
    ]


def test_close_through_retires_the_level_unswept():
    bars = _bars([9, 11, 10.5], [8, 9, 8.5], closes=[8.5, 10.5, 9.5])
    sw = [Event("swing_high", "down", 0, 0, 10.0)]
    assert sweeps(bars, sw) == []


# --------------------------------------------------------------------------- the property


def _all_detectors(bars: pd.DataFrame) -> dict[str, list[Event]]:
    sw = swings(bars, left=3, right=3)
    return {
        "swings": sw,
        "structure": structure(sw),
        "breaks": breaks(bars, sw),
        "zones": zones(bars, sw, tolerance_atr=0.5, atr_period=10),
        "bar_patterns": bar_patterns(bars),
        "sweeps": sweeps(bars, sw),
    }


@pytest.mark.parametrize("seed", range(8))
def test_no_detector_looks_ahead(seed):
    bars = _random_walk(seed)
    full = _all_detectors(bars)
    assert sum(len(v) for v in full.values()) > 50  # the fixture actually exercises things
    for t in range(0, len(bars), 7):
        prefix = _all_detectors(bars.iloc[: t + 1].reset_index(drop=True))
        for name, events in full.items():
            known = [e for e in events if e.confirmed <= t]
            assert prefix[name] == known, f"{name} differs at t={t}"


@pytest.mark.parametrize("seed", range(4))
def test_confirmed_is_never_before_occurred(seed):
    for events in _all_detectors(_random_walk(seed)).values():
        assert all(e.confirmed >= e.occurred for e in events)


def test_to_frame_columns():
    frame = to_frame([Event("pin", "up", 1, 1, math.nan)])
    assert list(frame.columns) == ["kind", "direction", "occurred", "confirmed", "price", "ref"]
    assert frame.iloc[0]["kind"] == "pin"
