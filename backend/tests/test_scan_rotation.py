"""Tests for `app/scan/rotation.py` (T50, plans/continuation/04-sector-rotation.md). Fully
offline: every test builds a small synthetic `pd.DataFrame`/`pd.Series` -- no database, no
filesystem, no network, in keeping with the module's purity contract.

Every hand-checked fixture value in this file was independently re-derived a second way before
being pasted into an assertion -- either a closed-form (the constant-multiple case) or a plain,
unoptimized Python loop written straight from the formula with no calls into `app.scan
.rotation` (the piecewise-linear RRG sequence, and the 3-symbol relative-return fixture). Both
derivations, and their output, are also recorded in `docs/validation-scan.md`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.scan.rotation import (
    SectorBreadth,
    relative_returns,
    rrg_approx,
    sector_breadth,
    weekly_closes,
)


def _weekly_index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-05", periods=n, freq="W-FRI")


# --------------------------------------------------------------------------------------------
# weekly_closes
# --------------------------------------------------------------------------------------------


def test_weekly_closes_takes_last_close_of_each_week_wfri():
    daily = pd.DataFrame(
        {"A": [1.0, 2.0, 3.0, 4.0, 5.0]},
        index=pd.to_datetime(
            ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-08", "2024-01-09"]
        ),  # Mon/Tue/Wed of week 1, Mon/Tue of week 2
    )
    weekly = weekly_closes(daily)
    # Week ending Fri 2024-01-05: last available close inside that bin is 2024-01-03 -> 3.0.
    # Week ending Fri 2024-01-12: last available close inside that bin is 2024-01-09 -> 5.0.
    assert weekly.loc["2024-01-05", "A"] == 3.0
    assert weekly.loc["2024-01-12", "A"] == 5.0


def test_weekly_closes_a_week_with_no_bar_at_all_is_nan_not_forward_filled():
    # Symbol "A" has no bar at all in the second week -- must read as NaN for that week, not
    # silently inherit the first week's last value (the task brief's named hazard).
    daily = pd.DataFrame(
        {"A": [1.0, 2.0, np.nan, np.nan]},
        index=pd.to_datetime(["2024-01-01", "2024-01-03", "2024-01-08", "2024-01-09"]),
    )
    weekly = weekly_closes(daily)
    assert weekly.loc["2024-01-05", "A"] == 2.0
    assert pd.isna(weekly.loc["2024-01-12", "A"])


def test_weekly_closes_empty_input_returns_empty():
    result = weekly_closes(pd.DataFrame(columns=["A", "B"]))
    assert result.empty
    assert list(result.columns) == ["A", "B"]


# --------------------------------------------------------------------------------------------
# rrg_approx
# --------------------------------------------------------------------------------------------


def test_rrg_approx_raises_on_w_below_2():
    prices = pd.DataFrame({"X": [1.0, 2.0]}, index=_weekly_index(2))
    with pytest.raises(ValueError, match="w must be >= 2"):
        rrg_approx(prices, prices["X"], w=1)


def test_rrg_approx_constant_multiple_of_benchmark_sits_at_exactly_100_100_after_warmup():
    """Acceptance item 1: a symbol whose price is a constant multiple of the benchmark sits at
    exactly (100, 100) for every week after warm-up -- made exact by using integer benchmark
    levels (`100 + t`) and a power-of-two multiplier (`2.0`), so `100 * (2*B) / B` round-trips
    to `200.0` bit-for-bit in IEEE double precision (independently checked: with a
    non-integer/non-power-of-two benchmark this ratio picks up ~1e-15 floating noise and is
    merely *approximately* 100, not exactly -- see `docs/validation-scan.md` for that check).
    With `rs` then bit-identical across every week, `_rolling_zscore`'s window variance is
    exactly `0.0`, not merely small, so the `std == 0` guard (not floating-point luck) is what
    makes this exact.
    """
    n = 40
    w = 14
    benchmark = pd.Series([100.0 + t for t in range(n)], index=_weekly_index(n))
    prices = pd.DataFrame({"SYM": 2.0 * benchmark}, index=benchmark.index)

    result = rrg_approx(prices, benchmark, w=w)
    sym = result[result["symbol"] == "SYM"].reset_index(drop=True)

    # Warm-up: NaN before week w-1 (rs_ratio) / before week 2w-1 (rs_momentum) -- never a
    # fabricated number (Non-negotiables: "insufficient history returns None/NaN").
    assert sym["rs_ratio_approx"].iloc[: w - 1].isna().all()
    assert sym["rs_momentum_approx"].iloc[: 2 * w - 1].isna().all()

    # Every week from warm-up onward: exactly 100.0, both coordinates, no tolerance.
    after_ratio_warmup = sym["rs_ratio_approx"].iloc[w - 1 :]
    after_momentum_warmup = sym["rs_momentum_approx"].iloc[2 * w - 1 :]
    assert (after_ratio_warmup == 100.0).all()
    assert (after_momentum_warmup == 100.0).all()


def _piecewise_accelerating_then_decelerating_rs(n: int = 80) -> list[float]:
    """A relative-strength series whose *rate of increase* accelerates (weeks 1-29: +0.5/week)
    then strongly accelerates again (weeks 30-49: +3.0/week) then decelerates (weeks 50-79:
    +0.3/week) -- never flat, never decreasing, always "rising" in the plan's colloquial sense,
    but deliberately not a single unbroken straight line. See `_rolling_zscore`'s own docstring
    for the closed-form proof that a *pure* straight line cannot produce the leading-then-
    weakening sequence this fixture is built to exercise: under this exact formula, an
    unbroken linear ramp's `rs_ratio_approx`/`rs_momentum_approx` become a genuine mathematical
    *constant* (not merely close to one) the instant warm-up completes, with momentum landing
    on exactly `100.0` -- never above it -- so this fixture's slope *changes* are the point, not
    an accident of an otherwise-simpler construction.
    """
    rs = [100.0]
    for t in range(1, n):
        if t <= 29:
            slope = 0.5
        elif t <= 49:
            slope = 3.0
        else:
            slope = 0.3
        rs.append(rs[-1] + slope)
    return rs


def test_rrg_approx_piecewise_rising_rs_shows_leading_then_weakening_sequence():
    """Acceptance item 2: a symbol with rising `rs` lands in the leading quadrant with positive
    momentum, then drifts toward weakening as the z-score saturates -- asserting the *sequence*
    (leading at week 30, weakening at week 33), not only an endpoint.

    Hand-checked (independent plain-Python loop, `w=14`, population mean/std, `std==0 -> 0.0`
    guard, no pandas/numpy calls) against this exact fixture, values reproduced to 6 decimals in
    `docs/validation-scan.md`:
        week 13 (first valid rs_ratio_approx):  101.612452
        week 27 (first valid rs_momentum_approx): 100.000000  (flat segment -- diffs are a
            constant +0.5/week the entire time this window is warming up, so its own z-score
            guard resolves to the honest "no deviation" 0.0, landing exactly at the border)
        week 30: rs_ratio_approx=102.346462, rs_momentum_approx=103.605551  -> LEADING
            (both > 100 -- the acceleration to +3.0/week is still mostly inside the momentum
            window's own trailing 14 weeks, most of which were the slower +0.5/week segment)
        week 33: rs_ratio_approx=102.335859, rs_momentum_approx=99.091090   -> WEAKENING
            (rs_ratio_approx is still > 100 -- the symbol is still "ahead" of its own recent
            history on the x-axis -- but rs_momentum_approx has now dropped below 100: the
            momentum window has slid far enough past the acceleration that its own trailing
            average of diffs is higher than the *current* diff, even though the current diff
            (+3.0/week) is unchanged -- exactly "the z-score saturates" in the module
            docstring's sense)
    """
    w = 14
    rs = pd.Series(_piecewise_accelerating_then_decelerating_rs(80), index=_weekly_index(80))
    benchmark = pd.Series(1.0, index=rs.index)  # rs = 100 * price / benchmark -> price = rs/100
    prices = pd.DataFrame({"SYM": rs / 100.0}, index=rs.index)

    result = rrg_approx(prices, benchmark, w=w)
    sym = result[result["symbol"] == "SYM"].reset_index(drop=True)

    ratio = sym["rs_ratio_approx"]
    momentum = sym["rs_momentum_approx"]

    assert ratio.iloc[:13].isna().all()
    assert ratio.iloc[13] == pytest.approx(101.612452, abs=1e-5)
    assert momentum.iloc[:27].isna().all()
    assert momentum.iloc[27] == pytest.approx(100.0, abs=1e-9)

    # Week 30: LEADING -- both coordinates above 100.
    assert ratio.iloc[30] == pytest.approx(102.346462, abs=1e-5)
    assert momentum.iloc[30] == pytest.approx(103.605551, abs=1e-5)
    assert ratio.iloc[30] > 100.0
    assert momentum.iloc[30] > 100.0

    # Week 33: WEAKENING -- ratio still above 100, momentum has dropped below 100.
    assert ratio.iloc[33] == pytest.approx(102.335859, abs=1e-5)
    assert momentum.iloc[33] == pytest.approx(99.091090, abs=1e-5)
    assert ratio.iloc[33] > 100.0
    assert momentum.iloc[33] < 100.0


def test_rrg_approx_benchmark_reindexed_onto_prices_index():
    """A benchmark series with extra rows outside `prices`' own index (the cross-symbol
    alignment hazard's general shape -- one series simply has more rows than the other) is
    reindexed onto `prices`' index before dividing, not zipped positionally; extra rows are
    dropped, never shifting which benchmark value lines up with which price date.
    """
    idx = _weekly_index(20)
    extra_idx = idx.append(pd.DatetimeIndex(["2030-01-01"]))
    benchmark = pd.Series([100.0 + t for t in range(20)] + [99999.0], index=extra_idx)
    prices = pd.DataFrame({"SYM": [200.0 + 2 * t for t in range(20)]}, index=idx)

    result = rrg_approx(prices, benchmark, w=14)
    # If the extra benchmark row had shifted the alignment, rs would not be the constant 100.0
    # ratio this fixture is built to produce -- exact equality (integer prices/benchmark, as
    # in the constant-multiple test above), not an approximate comparison.
    valid = result.dropna(subset=["rs_ratio_approx"])
    assert not valid.empty
    assert (valid["rs_ratio_approx"].to_numpy() == 100.0).all()


# --------------------------------------------------------------------------------------------
# relative_returns
# --------------------------------------------------------------------------------------------


def test_relative_returns_hand_built_3_symbol_fixture_matches_1e9():
    """Acceptance item 3: relative returns on a hand-built 3-symbol fixture match to 1e-9.

    `A`/`BENCH` have 10 full rows; `C` has only its last 4 rows populated (rows 0-5 `NaN`,
    simulating a symbol added to the universe partway through the window). Independently
    re-derived with a plain Python loop reading straight off the formula
    `(a_t/a_{t-n})/(b_t/b_{t-n}) - 1` (not calling `app.scan.rotation` at all):

        return_2:  A=-0.014136904761904656, BENCH(self)=0.0, C=-0.016089108910890992
        return_5:  A=0.05519480519480524,  BENCH(self)=0.0, C=NaN (only 4 rows -- `C[t-5]`
                   does not exist)

    `return_5` for `C` is `NaN`, not `None` -- `relative_returns` returns a `DataFrame`, and
    this module's convention (matching `app.scan.indicators`) is `NaN` for "insufficient
    history" at the `DataFrame`/`Series` level; only `SectorBreadth`, a frozen dataclass,
    translates to `None` at its own boundary.
    """
    idx = pd.RangeIndex(10)
    a = [100, 102, 101, 105, 110, 108, 115, 120, 118, 125]
    bench = [50, 50, 51, 50, 52, 53, 54, 53, 55, 56]
    c = [np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, 200, 202, 205, 210]
    prices = pd.DataFrame({"A": a, "BENCH": bench, "C": c}, index=idx, dtype=float)

    result = relative_returns(prices, "BENCH", windows=(2, 5))

    assert result.loc["A", "return_2"] == pytest.approx(-0.014136904761904656, abs=1e-9)
    assert result.loc["A", "return_5"] == pytest.approx(0.05519480519480524, abs=1e-9)
    assert result.loc["BENCH", "return_2"] == pytest.approx(0.0, abs=1e-9)
    assert result.loc["BENCH", "return_5"] == pytest.approx(0.0, abs=1e-9)
    assert result.loc["C", "return_2"] == pytest.approx(-0.016089108910890992, abs=1e-9)
    assert pd.isna(result.loc["C", "return_5"])


def test_relative_returns_unknown_benchmark_raises():
    prices = pd.DataFrame({"A": [1.0, 2.0]})
    with pytest.raises(ValueError, match="not a column"):
        relative_returns(prices, "NOPE")


def test_relative_returns_symbol_with_zero_bars_is_all_nan():
    prices = pd.DataFrame({"A": [100.0] * 10, "BENCH": [50.0] * 10, "EMPTY": [np.nan] * 10})
    result = relative_returns(prices, "BENCH", windows=(2, 5))
    assert pd.isna(result.loc["EMPTY", "return_2"])
    assert pd.isna(result.loc["EMPTY", "return_5"])
    # A flat symbol against a flat benchmark is a perfectly well-defined 0.0 (1.0/1.0 - 1),
    # not an undefined 0/0 -- both ratios have a nonzero denominator (the price itself), so
    # this is a genuine reading, not a case this function should suppress to None.
    assert result.loc["A", "return_2"] == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------------------------
# sector_breadth
# --------------------------------------------------------------------------------------------


def _flat_then_bar(last: float, n: int = 20) -> list[float]:
    return [100.0] * (n - 1) + [last]


def test_sector_breadth_exactly_4_of_11_above_20d_average():
    """Acceptance item 4: breadth on a fixture where exactly 4 of 11 sectors are above their
    20-day average returns 4.

    Each sector is 19 flat bars at 100.0 followed by one final bar: `101.0` for the four
    "above" sectors (`SMA20 = (19*100 + 101)/20 = 100.05 < 101`) and `99.0` for the other seven
    ("below": `SMA20 = (19*100 + 99)/20 = 99.95 > 99`) -- hand-computable exactly, no floating
    tolerance needed.
    """
    sectors = tuple(f"SEC{i}" for i in range(11))
    above = {"SEC0", "SEC1", "SEC2", "SEC3"}
    columns = {
        sym: _flat_then_bar(101.0 if sym in above else 99.0) for sym in sectors
    }
    columns["SPY"] = [100.0] * 20
    columns["RSP"] = [100.0] * 20
    prices = pd.DataFrame(columns)

    result = sector_breadth(prices, sectors=sectors)

    assert isinstance(result, SectorBreadth)
    assert result.above_20d == 4
    assert result.evaluated_20d == 11
    assert result.above_50d == 0  # only 20 bars of history -- SMA50 never warms up
    assert result.evaluated_50d == 0


def test_sector_breadth_missing_symbol_excluded_not_counted_against():
    sectors = ("SEC0", "SEC1", "SEC2")
    prices = pd.DataFrame(
        {
            "SEC0": _flat_then_bar(101.0),
            "SEC1": _flat_then_bar(99.0),
            # SEC2 entirely absent from `prices` -- must be excluded, not counted as "below".
            "SPY": [100.0] * 20,
            "RSP": [100.0] * 20,
        }
    )
    result = sector_breadth(prices, sectors=sectors)
    assert result.above_20d == 1
    assert result.evaluated_20d == 2


def test_sector_breadth_equal_weight_ratio_and_20d_change():
    n = 21
    rsp = [100.0 + i for i in range(n)]  # 100 .. 120
    spy = [100.0] * n
    prices = pd.DataFrame({"RSP": rsp, "SPY": spy, "SEC0": [100.0] * n})
    result = sector_breadth(prices, sectors=("SEC0",))
    # ratio_t = 120/100 = 1.2; ratio_{t-20} = ratio at row 0 = 100/100 = 1.0.
    assert result.equal_weight_ratio == pytest.approx(1.2, abs=1e-9)
    assert result.equal_weight_ratio_change_20d == pytest.approx(0.2, abs=1e-9)


def test_sector_breadth_insufficient_history_for_ratio_is_none_not_zero():
    prices = pd.DataFrame({"RSP": [100.0, 101.0], "SPY": [100.0, 100.0], "SEC0": [100.0, 100.0]})
    result = sector_breadth(prices, sectors=("SEC0",))
    # Only 2 rows -- ratio itself is computable (last row), but the 20-day change is not.
    assert result.equal_weight_ratio is not None
    assert result.equal_weight_ratio_change_20d is None


def test_sector_breadth_missing_benchmark_columns_leaves_ratio_none():
    prices = pd.DataFrame({"SEC0": _flat_then_bar(101.0)})
    result = sector_breadth(prices, sectors=("SEC0",))
    assert result.equal_weight_ratio is None
    assert result.equal_weight_ratio_change_20d is None
    assert result.above_20d == 1
