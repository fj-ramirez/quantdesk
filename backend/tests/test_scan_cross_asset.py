"""Tests for `app.modules.gex.scan.cross_asset` (T54, plans/continuation/06-cross-asset-regime.md).

Pure module, entirely offline -- no fixtures, no network, no database. Every test builds its
own small `pd.Series`/`pd.DataFrame` by hand, hand-checkable the same way
`test_scan_indicators.py`/`test_scan_rotation.py` validate their own pure modules.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.modules.gex.scan.cross_asset import (
    CrossAssetRow,
    SectorCorrelationResult,
    compute_cross_asset_row,
    percentile_252,
    sector_correlation,
    term_structure,
    vrp,
)
from app.modules.gex.scan.indicators import realized_vol

# ---------------------------------------------------------------------------------------
# term_structure
# ---------------------------------------------------------------------------------------


def test_term_structure_contango():
    # VIX/VIX3M < 1 and VIX9D/VIX < 1: curve slopes up from the front.
    assert term_structure(vix=15.0, vix3m=19.0, vix9d=13.0) == "contango"


def test_term_structure_backwardation():
    # VIX/VIX3M > 1 and VIX9D/VIX > 1: curve slopes down from the front (stress).
    assert term_structure(vix=30.0, vix3m=22.0, vix9d=34.0) == "backwardation"


def test_term_structure_mixed_when_ratios_disagree():
    # VIX/VIX3M < 1 but VIX9D/VIX > 1: disagreement -> mixed.
    assert term_structure(vix=18.0, vix3m=20.0, vix9d=19.0) == "mixed"


def test_term_structure_mixed_at_exact_parity():
    # Both ratios exactly 1.0 satisfy neither "< 1 and < 1" nor "> 1 and > 1".
    assert term_structure(vix=20.0, vix3m=20.0, vix9d=20.0) == "mixed"


def test_term_structure_rejects_non_positive_denominators():
    with pytest.raises(ValueError):
        term_structure(vix=0.0, vix3m=20.0, vix9d=15.0)
    with pytest.raises(ValueError):
        term_structure(vix=15.0, vix3m=0.0, vix9d=15.0)


# ---------------------------------------------------------------------------------------
# vrp
# ---------------------------------------------------------------------------------------


def test_vrp_hand_computed():
    # VIX 18.2 vol points, SPY RV20 = 0.15 fraction (15%) -> 18.2 - 15.0 = 3.2.
    assert vrp(18.2, 0.15) == pytest.approx(3.2)


def test_vrp_negative_when_realized_outruns_implied():
    assert vrp(12.0, 0.20) == pytest.approx(12.0 - 20.0)


# ---------------------------------------------------------------------------------------
# percentile_252
# ---------------------------------------------------------------------------------------


def test_percentile_252_none_under_60_bars():
    series = pd.Series(np.arange(59, dtype=float))
    pct, n = percentile_252(series)
    assert pct is None
    assert n == 59


def test_percentile_252_at_exactly_60_bars_is_not_none():
    series = pd.Series(np.arange(60, dtype=float))
    pct, n = percentile_252(series)
    assert n == 60
    assert pct == pytest.approx(1.0)  # last value (59) is the max of the window


def test_percentile_252_last_value_is_the_minimum():
    series = pd.Series(np.arange(100, 0, -1, dtype=float))  # descending: last value is smallest
    pct, n = percentile_252(series)
    assert n == 100
    assert pct == pytest.approx(0.0)


def test_percentile_252_middle_value_hand_checked():
    # 0..99 ascending, last value 99 is the max -> percentile 1.0. Use a window trimmed so the
    # current value sits in the middle: last 101 values 0..100 with the series re-ordered so
    # the *current* (last) observation is the median of the window.
    values = list(range(100))
    values[-1] = 50  # last observation now ties the middle of 0..99 (with one duplicate)
    series = pd.Series(values, dtype=float)
    pct, n = percentile_252(series)
    assert n == 100
    # rank(method="average") of the tied value 50 among [0..49, 50, 50, 51..99] (100 values,
    # two 50s: the original index-50 element and the replaced last element) -- average rank of
    # the two ties is (51 + 52) / 2 = 51.5 (1-indexed), scaled: (51.5 - 1) / 99.
    assert pct == pytest.approx((51.5 - 1.0) / 99.0)


def test_percentile_252_only_uses_trailing_window():
    # 300 ascending values; window=252 means the oldest 48 are dropped, but the *last* value is
    # still the maximum of whichever window is used, so this alone wouldn't catch a window bug
    # -- use a series where an early huge outlier would corrupt the percentile if not trimmed.
    values = [10_000.0] + list(range(299))  # first observation is an enormous outlier
    series = pd.Series(values)
    pct, n = percentile_252(series)
    assert n == 252  # the outlier (index 0) fell outside the trailing 252-window
    assert pct == pytest.approx(1.0)  # last value (298) is still the max of its own window


def test_percentile_252_drops_non_finite_values_before_windowing():
    series = pd.Series([np.nan] * 10 + list(range(60)), dtype=float)
    _pct, n = percentile_252(series)
    assert n == 60  # the 10 leading NaNs are not counted


def test_percentile_252_rejects_bad_args():
    with pytest.raises(ValueError):
        percentile_252(pd.Series([1.0]), window=0)
    with pytest.raises(ValueError):
        percentile_252(pd.Series([1.0]), min_bars=0)


# ---------------------------------------------------------------------------------------
# sector_correlation
# ---------------------------------------------------------------------------------------


def _hadamard(n: int) -> np.ndarray:
    """An `n`x`n` Hadamard matrix (`n` a power of 2), built by the standard recursive
    doubling construction. Rows *and* columns are pairwise orthogonal (`H @ H.T == n * I`
    implies `H.T @ H == n * I` too, since `H` is square and invertible) -- used to build
    genuinely, exactly (not just approximately) uncorrelated return series below.
    """
    h = np.array([[1.0]])
    while h.shape[0] < n:
        h = np.block([[h, h], [h, -h]])
    return h


def test_sector_correlation_is_exactly_one_when_every_sector_shares_one_return_series():
    dates = pd.bdate_range("2026-01-01", periods=25)
    base = 100.0 * np.cumprod(1.0 + np.concatenate([[0.0], np.full(24, 0.01)]))
    closes = pd.DataFrame({sym: base for sym in ["XLK", "XLF", "XLE", "XLV"]}, index=dates)

    result = sector_correlation(closes, window=20)
    assert result.mean_correlation == pytest.approx(1.0)
    assert result.n == 20
    assert result.universe_n == 4


def test_sector_correlation_is_near_zero_on_orthogonal_series():
    n = 32
    h = _hadamard(n)
    # Columns 1..11 (excluding column 0, the all-ones column -- an all-ones column has zero
    # variance, which would make its correlation with anything undefined) are mean-zero,
    # mutually orthogonal, unit-magnitude-entry sequences.
    returns = h[:, 1:12] * 0.001  # scaled to a realistic daily-return magnitude
    log_prices = np.concatenate([np.zeros((1, 11)), np.cumsum(returns, axis=0)], axis=0)
    prices = np.exp(log_prices)  # 33 rows: row 0 is the baseline, rows 1..32 realize `returns`

    dates = pd.bdate_range("2026-01-01", periods=33)
    columns = [f"S{i}" for i in range(11)]
    closes = pd.DataFrame(prices, columns=columns, index=dates)

    result = sector_correlation(closes, window=32)
    assert result.n == 32
    assert result.mean_correlation == pytest.approx(0.0, abs=1e-9)


def test_sector_correlation_reports_effective_sample_size_when_a_bar_is_missing():
    """The plan's own named hazard: one ETF missing a bar inside the window must shrink the
    reported sample, not silently misalign the rest."""
    dates = pd.bdate_range("2026-01-01", periods=25)
    rng = np.random.default_rng(0)
    log_returns = rng.normal(scale=0.01, size=(24, 3))
    prices = 100.0 * np.exp(np.concatenate([np.zeros((1, 3)), np.cumsum(log_returns, axis=0)]))
    closes = pd.DataFrame(prices, columns=["A", "B", "C"], index=dates)

    # Punch a hole in one column's *close*, in the middle of the trailing 20-bar window. A
    # missing close removes two rows from the log-return series (the return *into* that date
    # and the return *out of* it), not one -- both must be dropped from every column's sample,
    # not just column B's.
    closes.loc[dates[15], "B"] = np.nan

    result = sector_correlation(closes, window=20)
    assert result.n == 18  # 20-bar window minus the two return rows the missing close corrupts
    assert result.mean_correlation is not None


def test_sector_correlation_none_with_fewer_than_two_aligned_rows():
    dates = pd.bdate_range("2026-01-01", periods=2)
    closes = pd.DataFrame({"A": [100.0, 101.0], "B": [50.0, 50.5]}, index=dates)
    result = sector_correlation(closes, window=20)
    assert result.mean_correlation is None
    assert result.n == 1  # only 1 log return exists at all (2 closes)


def test_sector_correlation_none_with_fewer_than_two_columns():
    dates = pd.bdate_range("2026-01-01", periods=25)
    closes = pd.DataFrame({"A": np.arange(25, dtype=float) + 100.0}, index=dates)
    result = sector_correlation(closes, window=20)
    assert result == SectorCorrelationResult(mean_correlation=None, n=0, universe_n=1)


def test_sector_correlation_rejects_bad_window():
    with pytest.raises(ValueError):
        sector_correlation(pd.DataFrame({"A": [1.0], "B": [1.0]}), window=1)


# ---------------------------------------------------------------------------------------
# compute_cross_asset_row
# ---------------------------------------------------------------------------------------


def _flat_series(index: pd.DatetimeIndex, value: float) -> pd.Series:
    return pd.Series(value, index=index)


def test_compute_cross_asset_row_happy_path():
    n = 100
    dates = pd.bdate_range("2026-01-01", periods=n)

    # VIX rises steadily so its own last value is the max of its trailing window -> pct 1.0.
    vix = pd.Series(np.linspace(10.0, 30.0, n), index=dates)
    vix3m = _flat_series(dates, 22.0)  # VIX(30) > VIX3M(22) -> VIX/VIX3M > 1
    vix9d = _flat_series(dates, 32.0)  # VIX9D(32) > VIX(30) -> VIX9D/VIX > 1 -> backwardation
    vvix = pd.Series(np.linspace(80.0, 120.0, n), index=dates)

    rng = np.random.default_rng(1)
    spy_log_returns = rng.normal(scale=0.01, size=n - 1)
    spy_close = pd.Series(
        100.0 * np.exp(np.concatenate([[0.0], np.cumsum(spy_log_returns)])), index=dates
    )

    sector_log_returns = rng.normal(scale=0.01, size=(n - 1, 11))
    sector_prices = 50.0 * np.exp(
        np.concatenate([np.zeros((1, 11)), np.cumsum(sector_log_returns, axis=0)])
    )
    sector_closes = pd.DataFrame(
        sector_prices, columns=[f"XL{i}" for i in range(11)], index=dates
    )

    uup_close = pd.Series(np.linspace(28.0, 29.0, n), index=dates)  # steady uptrend
    gld_close = pd.Series(np.linspace(180.0, 170.0, n), index=dates)  # steady downtrend
    tlt_close = _flat_series(dates, 95.0)  # flat -> 0 return

    row = compute_cross_asset_row(
        vix=vix,
        vix3m=vix3m,
        vix9d=vix9d,
        vvix=vvix,
        spy_close=spy_close,
        sector_closes=sector_closes,
        uup_close=uup_close,
        gld_close=gld_close,
        tlt_close=tlt_close,
    )

    assert isinstance(row, CrossAssetRow)
    assert row.as_of == dates[-1]
    assert row.vix == pytest.approx(30.0)
    assert row.vix_pct == pytest.approx(1.0)
    assert row.vix_pct_n == n
    assert row.term_structure == "backwardation"
    assert row.term_structure_reason is None
    assert row.vrp is not None  # both VIX and SPY RV20 available
    assert row.vrp_reason is None
    assert row.sector_correlation is not None
    assert row.sector_correlation_universe_n == 11
    assert row.uup_return_20d > 0
    assert row.gld_return_20d < 0
    assert tlt_close.iloc[-1] == tlt_close.iloc[-21]
    assert row.tlt_return_20d == pytest.approx(0.0)


def test_compute_cross_asset_row_missing_inputs_degrade_to_none_with_reasons():
    n = 30  # short history: below percentile_252's 60-bar floor and below RV20's own warm-up
    dates = pd.bdate_range("2026-01-01", periods=n)

    all_nan = pd.Series(np.nan, index=dates)
    vix = _flat_series(dates, 18.0)

    row = compute_cross_asset_row(
        vix=vix,
        vix3m=all_nan,  # entirely missing -> term structure and its ratio can't be computed
        vix9d=all_nan,
        vvix=all_nan,
        spy_close=all_nan,  # entirely missing -> RV20, and therefore VRP, can't be computed
        sector_closes=pd.DataFrame({"A": all_nan, "B": all_nan}, index=dates),
        uup_close=all_nan,
        gld_close=all_nan,
        tlt_close=all_nan,
    )

    assert row.vix == pytest.approx(18.0)
    assert row.vix_pct is None  # only 30 finite VIX observations, below the 60-bar floor
    assert row.vix_pct_n == n

    assert row.term_structure is None
    assert row.term_structure_reason is not None

    assert row.spy_rv20 is None
    assert row.vrp is None
    assert row.vrp_reason is not None

    assert row.sector_correlation is None

    assert row.uup_return_20d is None
    assert row.gld_return_20d is None
    assert row.tlt_return_20d is None


def test_compute_cross_asset_row_as_of_none_when_vix_entirely_missing():
    dates = pd.bdate_range("2026-01-01", periods=5)
    all_nan = pd.Series(np.nan, index=dates)
    row = compute_cross_asset_row(
        vix=all_nan,
        vix3m=all_nan,
        vix9d=all_nan,
        vvix=all_nan,
        spy_close=all_nan,
        sector_closes=pd.DataFrame({"A": all_nan, "B": all_nan}, index=dates),
        uup_close=all_nan,
        gld_close=all_nan,
        tlt_close=all_nan,
    )
    assert row.as_of is None


def test_sector_correlation_window_counts_sessions_not_foreign_calendar_rows():
    """Dates on which *no* column trades are a calendar artifact, not a missing bar, and must
    not eat into the window.

    Measured against live data before this was fixed: `/api/gex/scan/cross-asset` reads one shared
    wide frame holding the sector ETFs *and* the Cboe index symbols, and Cboe's index calendar
    carries ~33 dates over five years that the ETF histories do not (1287 `^VIX` rows against
    1254 for every sector). Those index-only dates are all-`NaN` in the sector slice, so a
    `tail(window)` taken before dropping them landed on them and the "20-day" correlation was
    computed from 18 observations every single day.
    """
    sessions = pd.bdate_range("2026-01-01", periods=25)
    rng = np.random.default_rng(3)
    log_returns = rng.normal(scale=0.01, size=(24, 3))
    prices = 100.0 * np.exp(np.concatenate([np.zeros((1, 3)), np.cumsum(log_returns, axis=0)]))
    closes = pd.DataFrame(prices, columns=["A", "B", "C"], index=sessions)

    # Two dates the wider frame carries but this universe does not trade on -- all-NaN rows,
    # landing inside what would otherwise be the trailing 20-row slice.
    foreign = pd.DataFrame(
        np.nan,
        columns=["A", "B", "C"],
        index=pd.DatetimeIndex([sessions[20] + pd.Timedelta(days=1), sessions[22] + pd.Timedelta(days=1)]),
    )
    with_foreign = pd.concat([closes, foreign]).sort_index()

    baseline = sector_correlation(closes, window=20)
    widened = sector_correlation(with_foreign, window=20)

    assert baseline.n == 20
    assert widened.n == 20, "all-NaN calendar rows must not shrink the window"
    assert widened.mean_correlation == pytest.approx(baseline.mean_correlation)


def test_vrp_percentile_is_not_shortened_by_foreign_calendar_rows():
    """A rolling window costs `RV_PERIOD` observations per all-NaN row, not one.

    Measured on live data before the fix: the `/api/gex/scan/cross-asset` frame is the union
    calendar of the sector ETFs and the Cboe index symbols, and over its 400-day lookback it
    carried 8 dates `^VIX` has and `SPY` does not. Those 8 all-NaN SPY rows dropped the valid
    RV20 count from 256 to 129 and took `vrp_pct` down with it -- a tile labelled a one-year
    percentile computed from 129 observations.
    """
    sessions = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(11)
    spy = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(scale=0.01, size=300))), index=sessions)
    vix = pd.Series(rng.uniform(12.0, 30.0, size=300), index=sessions)

    # Eight dates the index calendar carries and SPY does not, spread through the window.
    foreign_dates = pd.DatetimeIndex([sessions[i] + pd.Timedelta(days=1) for i in range(40, 280, 30)])
    assert len(foreign_dates) == 8
    spy_widened = pd.concat([spy, pd.Series(np.nan, index=foreign_dates)]).sort_index()
    vix_widened = pd.concat([vix, pd.Series(rng.uniform(12.0, 30.0, size=8), index=foreign_dates)]).sort_index()

    clean_n = percentile_252(vix - realized_vol(pd.DataFrame({"close": spy})) * 100.0)[1]
    widened_n = percentile_252(
        vix_widened - realized_vol(pd.DataFrame({"close": spy_widened.dropna()})) * 100.0
    )[1]

    assert clean_n == 252
    assert widened_n == 252, "foreign calendar rows must not shorten the VRP percentile sample"
