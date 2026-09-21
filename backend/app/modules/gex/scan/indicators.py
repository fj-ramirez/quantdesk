"""Shared technical indicators for `app/modules/gex/scan/*` (T43,
plans/continuation/01-breakout-ledger.md; extended T45, plans/continuation/02-trend-chop-
scorer.md).

**Pure, exactly like `app.modules.gex.gex.engine`**: no HTTP, DB, filesystem or logging anywhere in this
module. Every function takes a `pd.DataFrame` of daily bars -- the shape `app.modules.gex.storage
.bars_repository.read_bars` returns: columns `date, open, high, low, close, volume, source`,
ascending by date, one row per trading day, `date` a `datetime.date` (never a `datetime`) --
and returns a `pd.Series` aligned 1:1 to `bars.index` by position. Nothing here touches the
network, a database, or a clock; a caller builds the frame (from the repository, or by hand in
a test) and everything downstream is deterministic.

This module started with exactly one indicator, `atr` (T43), written as a **shared module from
the start** for T45 (trend scorer) to extend with `adx`, `efficiency_ratio`, `choppiness`,
`realized_vol` and `variance_ratio`. Every addition here keeps `atr`'s two conventions:

* **Signature**: `indicator(bars: pd.DataFrame, period: int = N, ...) -> pd.Series`, one row
  per input row, no resampling and no reindexing by date. `variance_ratio` is the one
  deliberate exception -- see "Deviations from a rolling-Series convention" below.
* **Insufficient history is `NaN`, never a fabricated `0.0` or a silently shortened window.**
  This is the same discipline `app.modules.gex.gex.engine.GexLevel` applies to a missing scalar level
  (CLAUDE.md invariant 3's "`None` means unknown, never collapse it into a real zero"), carried
  over to a `Series`: pandas has no per-element nullable-float sentinel that survives ordinary
  arithmetic as cleanly as `NaN` does, so `NaN` is this module's `None`. A caller who wants a
  scalar out of a `Series` gets `NaN` back the same way and must decide there, explicitly, what
  an unmeasurable window means for that caller -- this module never decides it for them by
  dropping rows or padding with a plausible-looking number. `app.modules.gex.scan.trend.score_symbol`
  converts a trailing `NaN` to a plain `None` at the dataclass boundary the same way
  `app.modules.gex.scan.breakouts.BreakoutEvent` already does for `follow_through_atr`.

`true_range` is exposed publicly, not folded into `atr`, because `adx` is built from the same
true-range series plus directional movement -- computing it twice from two near-identical
private helpers would be exactly the kind of duplication this module exists to avoid.


How each T45 indicator was validated
-------------------------------------

Every function below was checked two ways, per the plan's explicit requirement (not a
formality): against hand-derivable values on a small, deliberately simple fixture, and against
the stated mathematical properties from `plans/continuation/02-trend-chop-scorer.md` ("Design
decisions"). Both are recorded in full, with the arithmetic, in `docs/validation-scan.md`
(the T43 validation doc extended, not a new file) -- this docstring summarizes what was checked
and why the fixtures were built the way they were; that file has the actual numbers.

* **`adx`** -- validated on a 30-bar fixture built specifically to make Wilder's smoothing
  hand-tractable: a flat 16-bar baseline (zero true range, zero directional movement) followed
  by 14 bars of a *constant* daily up-move. A constant input into Wilder's recurrence is a
  fixed point (`avg = avg*(n-1)/n + avg/n`), so `+DM`/`TR` smooth to their own constant values
  the instant the warm-up window fills, `-DI = 0` throughout the trending stretch, and `DX`
  locks at exactly `100.0` -- which a second, independent Wilder recursion (plain Python, no
  pandas, written directly from the recurrence formula) reproduces to `1e-6`. This also pins
  the "ADX warm-up needs about 2n bars" failure mode named in the plan: the fixture's `ADX_14`
  is `NaN` for every bar before the 2·14-2 = 26th (0-indexed), never a partially-smoothed
  number.
* **`efficiency_ratio`** -- Kaufman's own worked definition is already a hand-checkable ratio
  of two absolute sums; validated on a straight-line 25-bar fixture (`ER = 1.0` exactly, since
  the numerator and denominator are then the same telescoping sum) and on a sawtooth fixture
  where the denominator is visibly larger than the numerator (`ER` computed by hand as a single
  fraction, matching to `1e-9`).
* **`choppiness`** -- validated on the same straight-line fixture (`CHOP` at its theoretical
  floor -- see the property check below) and hand-computed on a small 16-bar mixed fixture
  using the formula literally: `100 * log10(sum(TR_14) / (max(H_14) - min(L_14))) / log10(14)`.
* **`realized_vol`** -- validated against `numpy.std(returns, ddof=1) * sqrt(252)` computed by
  hand on a 21-bar fixture's log returns (an unweighted sample stdev is not a discretionary
  choice here, `plans/continuation/02-trend-chop-scorer.md` names the exact formula).
* **`variance_ratio`** -- validated two ways: (1) a 40-bar fixture small enough that the
  Lo-MacKinlay (1988) overlapping-return sums were reproduced by an independent, unoptimized
  Python loop (not calling this module) accumulating each term of `Var_b`, `Var_a`, and every
  `delta_j` in the heteroskedasticity-robust `theta(q)`, matching this module's vectorized
  version to `1e-9`; (2) the stated properties -- i.i.d. Gaussian noise (seeded) lands `VR ≈ 1`
  with `|z| < 2` in at least 95 of 100 seeds (the plan's own acceptance bar), and a persistent
  trending series produces a large positive `z` -- both re-run and recorded in
  `docs/validation-scan.md` with the actual pass counts, not just the formula check.

See `docs/validation-scan.md` for every number above spelled out.


Deviations from a textbook definition, and from the rolling-`Series` convention
----------------------------------------------------------------------------------

* **`atr` (T43) uses a plain rolling mean, not Wilder's exponential smoothing** -- unchanged by
  this task, see that function's own docstring and `docs/validation-scan.md` §3 for the
  reasoning. `adx` below *does* use Wilder smoothing (the conventional definition, and the one
  the plan names explicitly: "ADX Wilder 14") -- the two indicators disagree on smoothing
  method on purpose, each matching its own textbook convention, and that disagreement is why
  `adx` cannot simply reuse `atr`'s rolling-mean true-range average as its own smoothed TR.
* **`adx`'s internal Wilder recurrence is written in "running average" form, not Wilder's
  original "running sum" form.** The two are related by a constant factor of `period` at every
  step and agree exactly once both are used consistently -- `+DI`/`-DI` are *ratios* of two
  such smoothed series, so the scale factor cancels there regardless of form, and the running-
  average form is what makes `adx`'s own second-stage smoothing (DX -> ADX, which is *not* a
  ratio, so the form does matter there) match the standard published definition: Wilder's ADX
  is conventionally seeded by the plain arithmetic mean of the first `period` `DX` values, which
  is the running-average form's natural seed, not the running-sum form's.
* **`variance_ratio` returns a `(float | None, float | None)` tuple computed once over the
  entire `bars` frame it is given, not a `pd.Series` aligned to `bars.index`.** This is a
  deliberate exception to this module's own `indicator(bars, period=N) -> pd.Series`
  convention, for the same reason `app.modules.gex.scan.breakouts.summarize`'s `lookback` is caller-supplied
  metadata rather than something the pure function trims to itself: the Lo-MacKinlay estimator
  is a single-sample statistic over a *fixed* number of observations (the plan: "VR(q=5) on log
  returns over 126 bars" -- one number, not a value re-estimated fresh at every trailing
  position), so there is no daily "today's VR" reading to report as a `Series` element the way
  there is for `adx`/`efficiency_ratio`/`choppiness`/`realized_vol`. `app.modules.gex.scan.trend
  .score_symbol` is the caller that decides how much of `bars` to hand it (`bars.tail(126)`),
  the same "the pure module accepts any window, the caller picks the window" split
  `detect_events`'s `n`/`k` already established.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

__all__ = [
    "ADX_PERIOD",
    "ATR_PERIOD",
    "CHOP_PERIOD",
    "ER_PERIOD",
    "REL_VOLUME_PERIOD",
    "RV_PERIOD",
    "TRADING_DAYS_PER_YEAR",
    "VR_MIN_RETURNS_FACTOR",
    "VR_Q",
    "adx",
    "atr",
    "choppiness",
    "efficiency_ratio",
    "realized_vol",
    "relative_volume",
    "true_range",
    "variance_ratio",
]

#: The window `app.modules.gex.scan.breakouts` uses for follow-through and excursion sizing (T43 plan:
#: "Follow-through ... / ATR14[t]"). Exported so callers name the same constant rather than
#: repeating the literal `14`.
ATR_PERIOD = 14

#: T45 plan defaults ("Design decisions", implement exactly): ADX Wilder 14, Kaufman efficiency
#: ratio n=20, Choppiness n=14, RV20 (`sqrt(252)`-annualized), VR(q=5).
ADX_PERIOD = 14
ER_PERIOD = 20
CHOP_PERIOD = 14
RV_PERIOD = 20
VR_Q = 5

#: `realized_vol`'s annualization factor -- 252 trading days/year, the plan's own "`sqrt(252)`".
TRADING_DAYS_PER_YEAR = 252

#: `variance_ratio` needs enough 1-period returns for its overlapping `q`-period sums to mean
#: anything (the Lo-MacKinlay estimator is asymptotic in `n`); below `VR_MIN_RETURNS_FACTOR *
#: q` returns this module reports "insufficient history" (`None, None`) rather than a numerator
#: built from too few overlapping windows to be anything but noise. Not a value the plan names
#: explicitly -- the plan's own default usage (126 bars, q=5 -> 125 returns) clears this floor
#: by more than an order of magnitude, so the floor only ever bites a symbol with a genuinely
#: short history, which is exactly the case this module's "insufficient history is `None`"
#: discipline exists for.
VR_MIN_RETURNS_FACTOR = 2

#: `relative_volume`'s trailing window (T92). Sixty sessions -- a quarter -- is long enough that
#: a single heavy day does not define the baseline and short enough to track a name whose
#: liquidity is genuinely changing.
REL_VOLUME_PERIOD = 60


def true_range(bars: pd.DataFrame) -> pd.Series:
    """Per-bar true range: `max(high-low, |high-prev_close|, |low-prev_close|)`.

    The first row has no previous close, so its true range is plain `high - low` --
    `prev_close` is `NaN` there, `pandas.DataFrame.max(axis=1, skipna=True)` (the default)
    ignores the two `NaN` candidates and keeps `high - low`, which is the standard convention
    for an indicator's first bar rather than a gap to fill.

    Args:
        bars: Ascending-by-date daily bars with at least `high`, `low`, `close` columns.

    Returns:
        `pd.Series` named `"true_range"`, aligned to `bars.index`, one value per row.
    """
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    prev_close = bars["close"].astype(float).shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    result = ranges.max(axis=1)
    result.name = "true_range"
    return result


def atr(bars: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Average True Range: a rolling, unweighted mean of :func:`true_range` over `period` bars.

    A plain rolling mean rather than Wilder's exponential smoothing -- the two agree closely in
    steady state and diverge mainly in how fast they respond right after a volatility regime
    change, which does not matter for this module's use (sizing a follow-through/excursion in
    "how many recent typical days did the market move" units, not a live trading signal). See
    `docs/validation-scan.md` for the measurement backing this choice, and the module docstring
    above for why `adx` below does *not* make the same choice.

    Args:
        bars: Ascending-by-date daily bars with at least `high`, `low`, `close` columns.
        period: Window length in bars. Must be `>= 1`.

    Returns:
        `pd.Series` named `f"atr_{period}"`, aligned to `bars.index`. The first `period - 1`
        values are `NaN` -- there is no `period`-bar window yet, so there is no ATR yet, and
        a caller must not read that as a true zero-volatility measurement.

    Raises:
        ValueError: `period < 1`.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    result = true_range(bars).rolling(window=period, min_periods=period).mean()
    result.name = f"atr_{period}"
    return result


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing recurrence, running-average form, over `series`'s **finite tail**.

    `series` is allowed a leading run of `NaN` (a prior warm-up, e.g. `true_range` is fully
    finite from bar 0 but a directional-movement series derived from it might not be) but not
    an interior one -- every caller in this module only ever produces a contiguous `NaN` prefix
    followed by all-finite data, so this does not attempt to handle gaps inside the finite
    region. First output = arithmetic mean of the first `period` finite values (Wilder's
    conventional seed); each value after that is `prev*(period-1)/period + current/period`.
    See the module docstring's "Deviations" section for why this is the average form, not
    Wilder's original running-sum form.

    Returns a `pd.Series` aligned to `series.index`, `NaN` until `period` finite values have
    accumulated.
    """
    values = series.to_numpy(dtype=float)
    n = values.size
    out = np.full(n, np.nan, dtype=float)

    finite_mask = ~np.isnan(values)
    if not finite_mask.any():
        return pd.Series(out, index=series.index)
    first_valid = int(np.argmax(finite_mask))
    tail = values[first_valid:]
    m = tail.size
    if m < period:
        return pd.Series(out, index=series.index)

    seed = float(np.mean(tail[:period]))
    out[first_valid + period - 1] = seed
    prev = seed
    for i in range(period, m):
        prev = (prev * (period - 1) + tail[i]) / period
        out[first_valid + i] = prev
    return pd.Series(out, index=series.index)


def adx(bars: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    """Average Directional Index (Wilder 14): trend *strength*, direction-agnostic, `0-100`.

    Two stages of Wilder smoothing, exactly the textbook construction: smoothed true range and
    smoothed directional movement give `+DI`/`-DI`, whose normalized spread is `DX`; `ADX` is
    `DX` smoothed a second time. See `docs/validation-scan.md` for the hand-checked 30-bar
    fixture and the module docstring's "Deviations" section for the one place this departs from
    a literal reading of Wilder's original (running-sum) formulation.

    Args:
        bars: Ascending-by-date daily bars with at least `high`, `low`, `close` columns.
        period: Wilder smoothing window. Must be `>= 1`.

    Returns:
        `pd.Series` named `f"adx_{period}"`, aligned to `bars.index`. `NaN` for roughly the
        first `2*period - 1` bars (needs `period` bars to seed the `+DI`/`-DI` smoothing, then
        `period` more `DX` values to seed the `ADX` smoothing itself) -- "about 2n bars" of
        warm-up, per the plan's own "Likely first-contact failures" note. Never a partially
        smoothed number.

    Raises:
        ValueError: `period < 1`.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    tr = true_range(bars)

    # +DM/-DM: the larger of the two single-bar directional moves, only when it is itself
    # positive -- a flat or inside bar contributes 0 to both. `up_move`/`down_move` are `NaN`
    # at bar 0 (`.diff()`); every comparison against `NaN` is `False`, so bar 0 gets `+DM =
    # -DM = 0` (the standard convention: there is no prior bar to move away from yet).
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0), index=bars.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0), index=bars.index
    )

    smoothed_tr = _wilder_smooth(tr, period)
    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)

    # A run with literally zero true range across the whole smoothing window (illiquid symbol,
    # `high == low == prev_close` for `period` bars straight -- also this module's own 30-bar
    # validation fixture's flat baseline, see `docs/validation-scan.md`) makes `smoothed_tr`
    # a *valid, non-warm-up* zero, which would otherwise divide `+DI`/`-DI` as `0/0 = NaN`.
    # `.where(smoothed_tr != 0.0, 0.0)` forces both DIs to the honest "no directional movement"
    # `0.0` on exactly those rows, while a genuine warm-up `NaN` in `smoothed_tr` survives
    # unchanged (`NaN != 0.0` is `True`, so `.where` keeps the already-`NaN` ratio there).
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = (100.0 * smoothed_plus_dm / smoothed_tr).where(smoothed_tr != 0.0, 0.0)
        minus_di = (100.0 * smoothed_minus_dm / smoothed_tr).where(smoothed_tr != 0.0, 0.0)
        di_sum = plus_di + minus_di
        # Same 0/0 guard, one level up: both DIs land on 0.0 together (the flat case above,
        # or a bar where +DM and -DM smoothed to exactly the same nonzero value -- possible in
        # principle, not exercised by this module's fixture) makes `di_sum == 0` with a
        # perfectly well-defined "no net directional bias" DX of `0.0`, not a fabricated
        # `0/0` NaN; a warm-up `NaN` di_sum survives the same way as above.
        dx = (100.0 * (plus_di - minus_di).abs() / di_sum).where(di_sum != 0.0, 0.0)

    result = _wilder_smooth(dx, period)
    result.name = f"adx_{period}"
    return result


def efficiency_ratio(bars: pd.DataFrame, period: int = ER_PERIOD) -> pd.Series:
    """Kaufman's Efficiency Ratio: `|C_t - C_{t-n}| / sum_{i=t-n+1..t} |C_i - C_{i-1}|`.

    `1.0` means every bar in the window moved in the same direction (maximally "efficient"
    trend); `0.0` means the net move was zero regardless of how much the price churned inside
    the window (maximally inefficient / choppy). See `docs/validation-scan.md` for the
    straight-line (`ER = 1.0` exactly) and sawtooth hand-checks.

    Args:
        bars: Ascending-by-date daily bars with at least a `close` column.
        period: Window length `n`. Must be `>= 1`.

    Returns:
        `pd.Series` named `f"er_{period}"`, aligned to `bars.index`. `NaN` for the first
        `period` bars (needs `period` prior closes to form both the numerator and the
        denominator) and also wherever the denominator itself is exactly zero -- a perfectly
        flat window has an undefined efficiency ratio, not a fabricated `0.0` or `1.0`.

    Raises:
        ValueError: `period < 1`.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    close = bars["close"].astype(float)
    net_change = (close - close.shift(period)).abs()
    volatility = close.diff().abs().rolling(window=period, min_periods=period).sum()

    with np.errstate(invalid="ignore", divide="ignore"):
        result = net_change / volatility
    result = result.where(volatility != 0.0)
    result.name = f"er_{period}"
    return result


def choppiness(bars: pd.DataFrame, period: int = CHOP_PERIOD) -> pd.Series:
    """Choppiness Index: `100 * log10(sum_n(TR) / (max_n(H) - min_n(L))) / log10(n)`.

    Bounded (in the textbook, TR-consistent case) in `[0, 100]`: near `100` means the market
    covered its `period`-bar range through pure back-and-forth (true range summed to many times
    the net range actually traveled -- maximally choppy); near `0` means the market covered
    almost the whole window's true range in one direction with almost no retracement (a
    straight-line trend's theoretical floor). See `docs/validation-scan.md` for the 16-bar
    hand-checked fixture.

    Args:
        bars: Ascending-by-date daily bars with at least `high`, `low`, `close` columns.
        period: Window length `n`. Must be `>= 2` (`log10(1) == 0` would divide by zero).

    Returns:
        `pd.Series` named `f"chop_{period}"`, aligned to `bars.index`. `NaN` for the first
        `period - 1` bars (no full window yet) and wherever `max_n(H) - min_n(L)` is exactly
        zero -- the plan's own named failure mode ("illiquid symbols with repeated identical
        closes"), guarded here rather than propagating a `log10(0)` `-inf`.

    Raises:
        ValueError: `period < 2`.
    """
    if period < 2:
        raise ValueError(f"period must be >= 2, got {period}")

    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    tr_sum = true_range(bars).rolling(window=period, min_periods=period).sum()
    span = (
        high.rolling(window=period, min_periods=period).max()
        - low.rolling(window=period, min_periods=period).min()
    )

    with np.errstate(invalid="ignore", divide="ignore"):
        result = 100.0 * np.log10(tr_sum / span) / math.log10(period)
    result = result.where((span > 0.0) & (tr_sum > 0.0))
    result.name = f"chop_{period}"
    return result


def realized_vol(bars: pd.DataFrame, period: int = RV_PERIOD) -> pd.Series:
    """Annualized realized volatility: rolling sample stdev of log returns, `* sqrt(252)`.

    The plan's own formula, no deviation: "RV20 = annualized std of log returns over 20 bars,
    `sqrt(252)`". Sample (Bessel-corrected, `ddof=1`) standard deviation -- the conventional
    choice for a volatility *estimate* rather than a definitionally-known population figure.

    Args:
        bars: Ascending-by-date daily bars with at least a `close` column.
        period: Number of log-return observations in the rolling window. Must be `>= 2` (a
            standard deviation needs at least two observations).

    Returns:
        `pd.Series` named `f"rv_{period}"`, aligned to `bars.index`. `NaN` for the first
        `period` bars (`period` log returns need `period + 1` closes; the first close itself
        produces no return at all, so the first `period` positions have no full window).

    Raises:
        ValueError: `period < 2`.
    """
    if period < 2:
        raise ValueError(f"period must be >= 2, got {period}")

    close = bars["close"].astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        log_returns = np.log(close / close.shift(1))
    result = log_returns.rolling(window=period, min_periods=period).std(
        ddof=1
    ) * math.sqrt(TRADING_DAYS_PER_YEAR)
    result.name = f"rv_{period}"
    return result


def relative_volume(bars: pd.DataFrame, period: int = REL_VOLUME_PERIOD) -> pd.Series:
    """Today's volume as a multiple of the mean of the previous `period` sessions' volume.

    The reading that separates a move the market participated in from one it ignored. T92's
    motivating case, measured on stored bars: IWM ran seven consecutive sessions above its
    trailing average into 2026-09-18, closing that Friday at 1.51x.

    **The window excludes the current bar, and that choice is the whole number.** A 60-day mean
    that includes today is pulled up by today: a genuine 3x session inflates its own
    denominator by about 3%, damping exactly the spike this exists to detect. Excluding it also
    fixes the denominator before the session starts, so an intraday reading compares against a
    constant rather than against a baseline that moves as the day fills in. (This is the
    difference between the 1.51 above and the 1.49 an inclusive window gives -- if a caller
    ever reports a figure that disagrees with this module by a percent or two, this is why.)

    Args:
        bars: Ascending-by-date daily bars for **one symbol**, with a `volume` column. Passing
            a multi-symbol frame silently averages across the symbol boundary -- group first.
        period: Sessions in the trailing mean, excluding the current one.

    Returns:
        `pd.Series` named `f"rel_volume_{period}"`, aligned to `bars.index`. `NaN` for the
        first `period` bars, and `NaN` -- never `0.0` -- wherever volume is unknown.

    Raises:
        ValueError: `period < 1`.

    Note:
        **`NaN` where volume is absent is invariant 3's reasoning one layer up.** Five symbols
        in the scan universe (`^SKEW`, `^VIX3M`, `^VIX6M`, `^VIX9D`, `^VVIX`) are index quotes
        that report no volume at all, and "no volume reported" is not "traded nothing". Reading
        those as `0.0` would give every one of them a permanent 0x relative volume and silently
        exclude them from anything later built on this reading.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    volume = bars["volume"].astype(float)
    # `closed="left"` is what drops the current bar from its own window; `min_periods=period`
    # means a short history yields NaN rather than an average over whatever happens to exist.
    trailing_mean = volume.rolling(window=period, min_periods=period, closed="left").mean()
    with np.errstate(invalid="ignore", divide="ignore"):
        result = volume / trailing_mean
    # A zero trailing mean divides to inf rather than raising. It means the name did not trade
    # at all in the window, which is an unknown baseline, not a ratio of any size.
    result = result.replace([np.inf, -np.inf], np.nan)
    result.name = f"rel_volume_{period}"
    return result


def variance_ratio(bars: pd.DataFrame, q: int = VR_Q) -> tuple[float | None, float | None]:
    """Lo-MacKinlay (1988) variance ratio `VR(q)` and its heteroskedasticity-robust z-statistic.

    `VR(q) = Var(q-period overlapping log returns) / (q * Var(1-period log returns))`. Under
    the random-walk null, `VR(q) == 1`; `VR(q) > 1` indicates positive serial correlation
    (momentum/trending), `VR(q) < 1` indicates negative serial correlation (mean reversion).
    `z` is the heteroskedasticity-robust `M2` statistic from the same paper (not the simpler
    homoskedastic-null statistic) -- the plan names this explicitly ("the heteroskedasticity-
    robust z-statistic"), because daily-bar volatility clusters, which would make the
    homoskedastic statistic's variance estimate wrong and its z-scores overconfident.

    **Not a rolling `Series`** -- see the module docstring's "Deviations" section for why. This
    computes one `(vr, z)` pair over the *entire* `bars` frame handed to it; a caller wanting
    "VR over the last 126 bars" slices `bars.tail(126)` before calling this, the same way
    `app.modules.gex.scan.breakouts.detect_events`'s `n`/`k` windows are the caller's choice, not this
    module's.

    Args:
        bars: Ascending-by-date daily bars with at least a `close` column.
        q: Aggregation horizon (the "how many 1-period returns per q-period return"). Must be
            `>= 2`.

    Returns:
        `(vr, z)`, both `None` when `bars` has fewer than `VR_MIN_RETURNS_FACTOR * q` 1-period
        log returns (insufficient history -- see :data:`VR_MIN_RETURNS_FACTOR`), or when the
        heteroskedasticity-robust `theta(q)` the z-statistic divides by is exactly zero (every
        return in the sample is bit-for-bit identical -- a degenerate, not a real, series).
        `z` alone (with `vr` still populated) is `None` in the same `theta(q) == 0` case only;
        `vr` itself has no comparable failure mode once there is enough history to compute it.

    Raises:
        ValueError: `q < 2`.
    """
    if q < 2:
        raise ValueError(f"q must be >= 2, got {q}")

    close = bars["close"].astype(float).to_numpy()
    if close.size < 2:
        return None, None
    log_prices = np.log(close)
    returns = np.diff(log_prices)
    n = returns.size
    if n < VR_MIN_RETURNS_FACTOR * q:
        return None, None

    mu = float(returns.mean())
    centered = returns - mu

    # Var_a: unbiased sample variance of the 1-period log returns.
    var_1 = float(np.sum(centered**2) / (n - 1))

    # Var_b: Lo-MacKinlay's overlapping q-period return variance estimator. `diffs_q[i]` is
    # the q-period log return ending at the (q+i)-th price, for every valid overlapping
    # window -- "overlapping" (rather than the non-overlapping, throw-most-of-the-data-away
    # estimator) is what the paper's own asymptotic theory (and its robust `theta(q)` below)
    # assumes.
    diffs_q = log_prices[q:] - log_prices[:-q]
    m = q * (n - q + 1) * (1.0 - q / n)
    var_q = float(np.sum((diffs_q - q * mu) ** 2) / m)

    vr = var_q / var_1

    # theta(q): the heteroskedasticity-robust asymptotic variance of (VR(q) - 1), Lo-MacKinlay
    # (1988) eq. 10. Each `delta_j` is the sample autocorrelation of *squared* demeaned returns
    # at lag j -- large when volatility clusters, which is exactly the effect a homoskedastic
    # z-statistic would ignore.
    denom = float(np.sum(centered**2) ** 2)
    theta = 0.0
    if denom > 0.0:
        for j in range(1, q):
            num = float(np.sum((centered[j:] ** 2) * (centered[:-j] ** 2)))
            delta_j = num / denom
            weight = (2.0 * (q - j) / q) ** 2
            theta += weight * delta_j

    if theta <= 0.0:
        return float(vr), None
    z = (vr - 1.0) / math.sqrt(theta)
    return float(vr), float(z)
