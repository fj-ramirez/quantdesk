"""Sector/industry/asset rotation math -- pure module (T50,
plans/continuation/04-sector-rotation.md).

Answers "where is relative strength moving between groups against a benchmark, right now" --
the RRG-style scatter plus a relative-return rank table plus a coarse breadth reading.
**Pure, exactly like `app.gex.engine`, `app.scan.breakouts` and `app.scan.trend`**: no HTTP, DB,
filesystem or logging anywhere in this module. Every function takes `pd.DataFrame`/`pd.Series`
prices already fetched by the caller (`app.storage.bars_repository.read_bars` /
`read_universe_closes`'s shape) and returns a `pd.DataFrame` or a frozen dataclass; `app.api
.scan` is the only place in this feature that touches Postgres.


**This is an open approximation of a proprietary indicator, not the real thing.** JdK RS-Ratio
and RS-Momentum (the "RRG" -- Relative Rotation Graph -- popularized by Julius de Kempenaer)
are a proprietary, patented construction whose exact normalization is not published. What
follows is the commonly published *open approximation* of that idea (rolling z-scores of a
plain relative-strength ratio and its own week-over-week change) -- **never a claim of parity
with the real JdK indicator**. `rrg_approx` is named with the `_approx` suffix for exactly this
reason, and `app.api.scan`'s `/rotation` response carries the same disclaimer forward as an
explicit `note` field plus `rs_ratio_approx`/`rs_momentum_approx` field names, so a reader of
the JSON (or the page's info tooltip, T51/07-ui.md) sees the caveat without having to find this
docstring. Restated once more in `docs/validation-scan.md`, per the task's own requirement.


The cross-symbol alignment hazard, and how every function below handles it
-------------------------------------------------------------------------------

**Cross-symbol series in this app are not automatically date-aligned** -- a symbol can carry a
bar on a date another symbol does not (a market holiday one provider's own history quirk keeps
and another does not, a symbol that started trading later than its peers, a single missed
vendor day). Every function in this module divides one symbol's price series by another's (a
benchmark, or a second symbol for the breadth ratio), and a silent misalignment there --
dividing `symbol[t]` by `benchmark[t-1]` because one series had one fewer row than the other --
produces a plausible-looking number that is simply wrong, which is the worst failure mode this
particular page can have (per the task brief: "the worst possible failure for this page").

The fix used everywhere below is the same one: **never index two series by position and divide
-- always divide two columns of the *same* `DataFrame`,** so pandas aligns them by their shared
index (dates) before any arithmetic happens, and a date one column lacks becomes `NaN` in the
result rather than silently pairing with the wrong row. Concretely:

* `weekly_closes` resamples every column of its input **together, one shared `resample` call
  producing one shared weekly `DatetimeIndex`** -- it is never called once per symbol. A
  symbol's benchmark and every peer symbol therefore land on the exact same set of week-ending
  dates before `rrg_approx` ever divides one by another.
* `rrg_approx` takes its `benchmark` argument as a `pd.Series` and explicitly
  `.reindex(prices.index)`s it onto `prices`' own index before dividing -- a defensive
  realignment for a caller who (against this module's own contract) passed a benchmark series
  built from a different resample call; the intended, and only tested, calling convention is
  "pass the *same* `weekly_closes(...)` output's own benchmark column," making the reindex a
  no-op in the intended path and a safety net otherwise.
* `relative_returns` and `sector_breadth` both take one wide `prices` frame containing *every*
  symbol they need (including the benchmark, or `SPY`/`RSP`) as columns of that one frame, and
  do all division as `prices[a] / prices[b]` -- pandas' own column alignment on the shared
  index, never a `.to_numpy()` positional divide.

**What a missing week/day means, decided explicitly, once, here:** a date/week a symbol has no
bar for is `NaN` in that symbol's column and stays `NaN` through every computation that touches
it -- **never forward-filled, never dropped from the shared index.** `weekly_closes` uses
`resample(...).last()` (skips `NaN` *within* a week's bin if at least one real bar exists that
week, but never reaches backward into a prior week to invent one) and takes no ffill step
anywhere; every rolling window below (`_rolling_zscore`'s mean/std, the breadth SMAs) is built
with `min_periods` equal to the *full* window length, so **one missing week/day inside an
otherwise-full trailing window is enough to make that whole window's output `NaN`** until the
missing observation ages out of the window -- not silently computed from one fewer point, which
would be an unannounced narrowing of the window's own definition. This is the same "insufficient
history is `NaN`, never a fabricated number" discipline `app.scan.indicators` already applies,
extended to the specific "an internal gap, not just a warm-up prefix, must also count" case this
task's data hazard calls out by name.


Design decisions (plan's "Design decisions" -- the deliverable's contract)
-----------------------------------------------------------------------------

* **Weekly, not daily, for the RRG.** `weekly_closes` resamples `W-FRI` (last available close
  each week, labelled by that week's Friday) before `rrg_approx` ever runs -- daily RRG trails
  are noise, weekly with a 10-week trail is the standard reading (plan, verbatim), and the
  label date is the resample bin's own Friday even in a holiday-shortened week where the last
  real bar fell on Thursday (the plan's own named first-contact failure) -- see `weekly_closes`
  for why that is fine for the math and where the *actual* last-bar date is recovered for a
  tooltip (the API layer, not this module, which has no bar-level dates left after resampling).
* **`rs = 100 * P / B`; `rs_ratio_approx = 100 + z(rs, w)`; `rs_momentum_approx = 100 +
  z(rs_ratio_approx_t - rs_ratio_approx_{t-1}, w)`, `z` a rolling z-score over `w` weeks
  (default 14).** Exactly the plan's formula, no deviation in the math itself -- only in the
  honest naming discussed above.
* **`relative_returns` windows are *daily* bars, not weeks** -- the plan's own "1, 4 and 13
  weeks" reading is delivered as `n in {5, 20, 65}` trading days (plan, verbatim: "Relative
  return ... over n ∈ {5, 20, 65} bars"), operating on the *daily* wide frame directly, not on
  `weekly_closes`' output. This is why `relative_returns` and `rrg_approx` take differently
  shaped inputs (daily wide frame vs. weekly wide frame) even though both are "this module's
  price-ratio functions" -- they answer the plan's two different table/chart requirements, each
  at the granularity the plan names for it.
* **Breadth is coarse and says so.** `sector_breadth` computes only what a ~50-symbol universe
  can honestly support: the `RSP`/`SPY` ratio and its 20-day change (equal- vs. cap-weight
  leadership), and how many of the 11 sector ETFs sit above their own 20- and 50-day daily
  SMAs. It never claims to be S&P 500 constituent breadth (which needs 500 symbols of bars, out
  of this app's budget) -- the caller (`app.api.scan`) is responsible for the "sector-level
  breadth" label the plan requires on the outside of this dataclass; this module only computes
  the numbers.
* **Percentile ties / warm-up conventions already established by `app.scan.trend` are not
  reused here on purpose** -- this module has no cross-sectional percentile ranking at all (the
  plan's rotation page ranks nothing against the rest of the universe; every reading here is
  either "this symbol vs. its own benchmark" or "this symbol vs. its own trailing history").


How this was validated
------------------------

`docs/validation-scan.md` records, with the arithmetic: (1) a symbol whose price is an exact
constant multiple of the benchmark landing at exactly `(100.0, 100.0)` for every week once the
z-score's own warm-up completes -- the sharpest test of the formula, made exact (not
approximate) by the `std == 0` guard `_rolling_zscore` applies; (2) a hand-built, three-segment
piecewise-linear `rs` fixture whose *rate of increase* accelerates and then decelerates --
**not** a single unbroken straight line, which this module's own math makes provably incapable
of ever leaving `rs_momentum_approx == 100.0` (see `_rolling_zscore`'s docstring and
`docs/validation-scan.md` for the closed-form derivation of why) -- producing a genuine,
independently-recomputed leading-then-weakening sequence; (3) a hand-built 3-symbol
`relative_returns` fixture matched to `1e-9`; (4) an 11-symbol `sector_breadth` fixture with
exactly 4 symbols above their own 20-day SMA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "BREADTH_SMA_LONG",
    "BREADTH_SMA_SHORT",
    "RELATIVE_RETURN_WINDOWS",
    "RRG_ZSCORE_WINDOW",
    "SectorBreadth",
    "relative_returns",
    "rrg_approx",
    "sector_breadth",
    "weekly_closes",
]

#: Plan default: rolling z-score window for both `rs_ratio_approx` and `rs_momentum_approx`,
#: in *weeks* (the module docstring's "Weekly, not daily" design decision).
RRG_ZSCORE_WINDOW = 14

#: Plan default: the relative-return rank table's three trading-day windows, approximating
#: 1/4/13 weeks (plan, verbatim: "n ∈ {5, 20, 65}").
RELATIVE_RETURN_WINDOWS: tuple[int, ...] = (5, 20, 65)

#: `sector_breadth`'s two daily SMA windows (plan: "20- and 50-day averages").
BREADTH_SMA_SHORT = 20
BREADTH_SMA_LONG = 50


def _to_float(series: pd.Series) -> pd.Series:
    """`series`, coerced to plain `float64`, `pandas.NA`/`None`/anything non-numeric becoming
    `NaN` -- **not** `series.astype(float)`. `read_universe_closes` fills an entirely-absent
    symbol's column with the scalar `pandas.NA` (an `object`-dtype column of that one repeated
    value, per that function's own docstring), and `Series.astype(float)` on such a column
    *raises* `TypeError` in this pandas version (`float(pandas.NA)` is not a supported
    conversion at the numpy level `astype` falls through to) rather than producing `NaN` --
    `pandas.to_numeric(..., errors="coerce")` is the call that actually implements "anything
    that isn't a real number becomes `NaN`," which every function in this module relies on to
    normalize its inputs before dividing. Discovered by this task's own API-layer test against
    a symbol with zero stored bars (`app.api.scan.get_rotation`'s `XLF`-with-no-bars case) --
    see `docs/validation-scan.md`.
    """
    return pd.to_numeric(series, errors="coerce")


def weekly_closes(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample a wide daily-close frame (one column per symbol) down to one row per week.

    `W-FRI`, last available close inside each week's bin (plan: "last close of the week") --
    every column is resampled together in one call, which is what guarantees every symbol
    (and the benchmark, if it is one of the columns) lands on the *same* set of week-ending
    dates; see the module docstring's "cross-symbol alignment hazard" section for why this
    matters more than it looks like it should.

    A week with **no bar at all** for a symbol (every daily value inside that week's bin is
    `NaN` -- e.g. a symbol newly added to `SCAN_UNIVERSE` before its first bars-job run, or a
    genuine multi-day provider gap) resamples to `NaN` for that week, for that symbol only --
    `resample(...).last()` skips `NaN` rows *within* a bin that has at least one real value,
    but reaches into no other bin to invent one, and this function calls no `.ffill()`/
    `.fillna()` anywhere. This is the task brief's explicit instruction: "a symbol with no bar
    that week must not silently become a forward-filled or dropped row that shifts its trail."

    Args:
        daily: Wide frame, one column per symbol, index of dates (either a `DatetimeIndex`
            already, or anything `pandas.to_datetime` accepts elementwise -- `app.storage
            .bars_repository.read_universe_closes`'s own index is plain `datetime.date`
            objects, not `Timestamp`s, so this function converts rather than requiring the
            caller to). Values may be `pandas.NA`, `NaN`, or a real close; `read_universe_closes`
            fills an entirely-absent symbol's column with `pandas.NA`, which `_to_float`
            below normalizes to `NaN` the same way every other `app.scan` module treats a
            missing reading.

    Returns:
        Wide frame, one column per symbol (same columns, same order), one row per week-ending
        Friday inside `daily`'s date range, ascending. Empty (zero rows, same columns) when
        `daily` itself is empty.
    """
    if daily.empty:
        return daily.copy()

    frame = daily.apply(_to_float)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    weekly = frame.resample("W-FRI").last()
    return weekly


def _rolling_zscore(series: pd.Series, w: int) -> pd.Series:
    """Rolling z-score of `series` over a trailing `w`-observation window: `(x - mean) / std`,
    population `std` (`ddof=0`) -- the window itself is the whole population being described,
    not a sample estimating a larger one, which is the more natural reading for "how many
    standard deviations from *this window's own* average" than a sample-corrected estimate
    would be. `docs/validation-scan.md` records this as an explicit choice (the plan does not
    name a `ddof` convention either way).

    Both `mean` and `std` use `min_periods=w` -- **not** the smaller `min_periods` pandas would
    accept -- which is what makes a single `NaN` *anywhere inside* the trailing window (not
    only in a leading warm-up run) count against the window's own required size: a rolling
    window with 13 real values and 1 `NaN` inside a 14-observation window has only 13 non-null
    observations, short of `min_periods=14`, so the window's output is `NaN` until that missing
    observation ages out the far side -- never silently computed from the 13 it does have. This
    is the module docstring's "missing week... must not silently become a dropped row" contract,
    implemented via the same pandas `min_periods` mechanism `app.scan.indicators` already uses
    for its own rolling windows, applied here to a window that can have an *interior* gap and
    not only a leading one.

    A window with zero dispersion (`std == 0` -- every one of the `w` values in it identical,
    which is a genuine, not a warm-up, state: `rs` sitting at an exact constant multiple of the
    benchmark for `w` straight weeks is this module's own sharpest acceptance test) would
    otherwise divide `0 / 0` into a fabricated `NaN`; `.where(std != 0.0, 0.0)` forces that
    specific case to the honest `0.0` ("no deviation from the mean, because there is no
    variation to deviate from") instead, the same `0/0` guard pattern `app.scan.indicators.adx`
    already applies to its own valid-zero-denominator case. A genuine warm-up `NaN` in `std`
    survives this `.where` unchanged, because `NaN != 0.0` evaluates `True` in pandas/numpy --
    the condition that keeps the original (still-`NaN`) value rather than overwriting it.

    **Why a single unbroken linear ramp can never show this module's `rs_momentum_approx`
    above `100.0`:** for `x_t = a*t + b` (any constant slope `a`, any window position `t` once
    the window is full), the window's contents are `w` consecutive terms of the same arithmetic
    progression regardless of `t` -- a pure translation, not a reshaping, of the same `w`
    numbers -- so `(x_t - mean) / std` is *exactly* the same constant for every valid `t`:
    algebraically, `mean = a*(t - (w-1)/2) + b`, so `x_t - mean = a*(w-1)/2`, and
    `std = |a| * sqrt((w**2 - 1) / 12)` (the population standard deviation of `w` equally
    spaced points), giving `z = sign(a) * sqrt(3*(w-1)/(w+1))` -- a number with no `t` in it at
    all. Feeding that *constant* `rs_ratio_approx` into a second rolling z-score (for
    `rs_momentum_approx`) hands it a week-over-week diff series that is identically `0.0`
    everywhere it is defined, which the `std == 0` guard above resolves to exactly `100.0`,
    forever, once warm-up completes -- not `> 100.0`, and not a transient decaying toward
    `100.0` either, because there is no transient: the very first valid value already equals
    every later one. This is *why* this module's own acceptance fixture for "a symbol that
    lands in the leading quadrant, then drifts toward weakening" (`docs/validation-scan.md`)
    uses a piecewise-linear `rs` whose *slope itself* changes over time (accelerating, then
    decelerating) rather than a single straight line -- a pure straight line is mathematically
    incapable of ever producing that sequence under this exact formula, which is itself a fact
    worth a reader knowing rather than only a fixture worth having.
    """
    mean = series.rolling(window=w, min_periods=w).mean()
    std = series.rolling(window=w, min_periods=w).std(ddof=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (series - mean) / std
    return z.where(std != 0.0, 0.0)


def rrg_approx(prices: pd.DataFrame, benchmark: pd.Series, w: int = RRG_ZSCORE_WINDOW) -> pd.DataFrame:
    """The open RRG approximation's two coordinates, per symbol per week: `rs_ratio_approx` (x)
    and `rs_momentum_approx` (y). See the module docstring for the formula, the honest naming,
    and why this is not the real (proprietary) JdK indicator.

    Args:
        prices: Wide **weekly** close frame (`weekly_closes`'s own output shape), one column
            per symbol to be plotted. May include the benchmark's own symbol as one of its
            columns -- that symbol then lands at exactly `(100.0, 100.0)` for every week once
            warm-up completes, which is this module's own sharpest acceptance test, not a bug
            to special-case away.
        benchmark: The benchmark's own weekly close series. **Must come from the same
            `weekly_closes(...)` call that produced `prices`** (same underlying daily frame, so
            the same shared weekly index) -- this function `.reindex(prices.index)`s it as a
            defensive realignment, not a substitute for that calling contract; a benchmark
            built from an unrelated resample would realign onto `prices`' index but might
            realign onto the *wrong* week's value if the two resamples' bin boundaries ever
            disagreed (they should not, given the shared "W-FRI on the same daily source"
            contract, but this function cannot verify that from the series alone).
        w: Rolling z-score window in weeks, for both `rs_ratio_approx` and
            `rs_momentum_approx`. Plan default 14. Must be `>= 2` (a standard deviation needs
            at least two observations).

    Returns:
        Long `DataFrame`, columns `symbol, date, rs_ratio_approx, rs_momentum_approx`, one row
        per `(symbol, week)` in `prices`' own row/column order. `rs_ratio_approx` is `NaN` for
        roughly the first `w - 1` weeks (the first rolling z-score's own warm-up);
        `rs_momentum_approx` is `NaN` for roughly the first `2*w - 1` (it is a second rolling
        z-score built on the first one's own week-over-week diff, so its warm-up stacks on top
        of `rs_ratio_approx`'s) -- see `_rolling_zscore` for the exact mechanism, and never a
        partially-windowed number in either case.

    Raises:
        ValueError: `w < 2`.
    """
    if w < 2:
        raise ValueError(f"w must be >= 2, got {w}")

    aligned_benchmark = _to_float(benchmark).reindex(prices.index)

    frames: list[pd.DataFrame] = []
    for symbol in prices.columns:
        price = _to_float(prices[symbol])
        rs = 100.0 * price / aligned_benchmark
        rs_ratio = 100.0 + _rolling_zscore(rs, w)
        rs_momentum = 100.0 + _rolling_zscore(rs_ratio.diff(), w)
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": prices.index,
                    "rs_ratio_approx": rs_ratio.to_numpy(),
                    "rs_momentum_approx": rs_momentum.to_numpy(),
                }
            )
        )

    if not frames:
        return pd.DataFrame(columns=["symbol", "date", "rs_ratio_approx", "rs_momentum_approx"])
    return pd.concat(frames, ignore_index=True)


def _last_finite_pair(a: pd.Series, b: pd.Series, n: int) -> float:
    """`(a_t / a_{t-n}) / (b_t / b_{t-n}) - 1`, read off at the *most recent row where every one
    of the four terms is finite* -- not necessarily the frame's own last row, since one symbol
    can have a more recent gap than another (see the module docstring's alignment section).
    `a`/`b` must already share the same index (both columns of the same `DataFrame`), so the
    `n`-row shift and the division below are pandas-aligned by that shared index throughout, not
    a positional divide of two independently-shifted arrays.

    Returns `NaN` (this module's DataFrame-returning convention for "insufficient history,"
    the same one `app.scan.indicators` uses -- only the frozen-dataclass-returning
    `SectorBreadth` translates to a plain `None` at its own boundary) when no row has all four
    terms finite: fewer than `n + 1` rows of overlap between `a` and `b`, or `b`'s value at
    every such row is exactly `0.0`, an undefined relative return this function will not
    fabricate.

    Deliberately **searches backward** for the most recent computable row, unlike this module's
    own `_last_finite` (which checks only the frame's own last row and is used where "as of
    today, or `None`" is the honest answer -- `sector_breadth`). A relative-return rank table
    is read the other way: a symbol one day staler than its peers because of an ordinary
    single-day gap should still show its most recent real reading rather than blanking out
    entirely, the same "a symbol's own most recent value, whenever that was" reading
    `app.scan.breakouts.BreakoutSummary.last_event` already gives for an unrelated reason
    (there, no per-bar date to align against at all). The two helpers encode two different,
    both deliberate, answers to "what does a caller of *this* function actually want."
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = (a / a.shift(n)) / (b / b.shift(n)) - 1.0
    ratio = ratio.replace([np.inf, -np.inf], np.nan)
    finite = ratio.dropna()
    return float(finite.iloc[-1]) if not finite.empty else float("nan")


def relative_returns(
    prices: pd.DataFrame, benchmark: str, windows: tuple[int, ...] = RELATIVE_RETURN_WINDOWS
) -> pd.DataFrame:
    """Relative return versus `benchmark`, one row per symbol, one column per window in
    `windows`: `(P_t / P_{t-n}) / (B_t / B_{t-n}) - 1` (plan's formula, verbatim), evaluated at
    each symbol's own most recent fully-available row -- see `_last_finite_pair`.

    Args:
        prices: Wide **daily** close frame (`app.storage.bars_repository.read_universe_closes`'s
            own shape -- this function is the one place in this module that wants daily, not
            weekly, bars; see the module docstring's "Design decisions"). Must include
            `benchmark` as one of its own columns -- the division is `prices[symbol] /
            prices[benchmark]`, column-aligned by `prices`' own shared index, never a separately
            fetched benchmark series (unlike `rrg_approx`, which does take one -- the two
            functions differ here because this one already has every column it needs in one
            frame, and introducing a second frame would only reopen the alignment hazard this
            module exists to close).
        benchmark: One of `prices`' own column names.
        windows: Trading-day windows. Plan default `(5, 20, 65)`, approximating 1/4/13 weeks --
            a caller building a small hand-fixture is free to pass shorter windows; this
            function enforces no menu (the API layer's job, mirroring how `app.scan.breakouts
            .detect_events`'s `n`/`k` are unrestricted here for the same reason).

    Returns:
        `DataFrame` indexed by symbol (`prices`' own column order, benchmark included -- the
        benchmark's relative return against itself is always exactly `0.0` once it has `n + 1`
        rows, which is a correct reading, not a value worth suppressing), columns
        `f"return_{n}"` for each `n` in `windows`, `float` dtype. A cell is `NaN` wherever
        `_last_finite_pair` found no fully-overlapping row -- insufficient history, never a
        fabricated `0.0` (this is a `DataFrame`, so the convention is `NaN`, not `None` -- see
        `_last_finite_pair`'s own docstring for why this differs from `SectorBreadth`'s fields).

    Raises:
        ValueError: `benchmark` is not one of `prices`' columns.
    """
    if benchmark not in prices.columns:
        raise ValueError(f"benchmark {benchmark!r} is not a column of prices")

    bench = _to_float(prices[benchmark])
    rows: dict[str, dict[str, float]] = {}
    for symbol in prices.columns:
        price = _to_float(prices[symbol])
        rows[symbol] = {f"return_{n}": _last_finite_pair(price, bench, n) for n in windows}

    columns = [f"return_{n}" for n in windows]
    return pd.DataFrame.from_dict(rows, orient="index")[columns].astype(float)


@dataclass(frozen=True, slots=True)
class SectorBreadth:
    """Coarse, sector-*level* breadth (plan: "Constituent-level breadth... needs 500 symbols of
    bars and is out of budget") -- never to be presented as S&P 500 constituent breadth. The
    caller (`app.api.scan`) is responsible for carrying that "sector-level breadth" label
    forward into the response/UI; this dataclass only carries the numbers.

    `above_20d`/`above_50d` count *only* sectors with enough history to have a real SMA reading
    for that window at all -- `evaluated_20d`/`evaluated_50d` name the denominator (out of
    `len(app.scan.groups.SECTORS)`, normally 11) so a reader can tell "4 of 11 are above" from
    "4 of only 6 evaluable sectors are above" rather than the two being silently conflated. A
    sector missing its SMA entirely (insufficient daily history) is excluded from both the
    numerator and the denominator, never counted as "not above" -- the same "absence is not a
    negative reading" discipline `app.scan.trend.TrendComponents` already applies to a missing
    indicator.

    `equal_weight_ratio`/`equal_weight_ratio_change_20d` are `None` together whenever `RSP` or
    `SPY` has no overlapping history at all to compute the ratio from in the first place; the
    ratio can be present while its own 20-day change is `None` (ratio computable at the most
    recent row, but fewer than 21 rows of history to look back over for the change), the same
    "a later-stage computation can fail independently of an earlier one it depends on" pattern
    `app.scan.trend.TrendComponents.iv_rv_ratio` already has relative to `rv20`/`iv30`.
    """

    equal_weight_ratio: float | None
    equal_weight_ratio_change_20d: float | None
    above_20d: int
    evaluated_20d: int
    above_50d: int
    evaluated_50d: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "equal_weight_ratio": self.equal_weight_ratio,
            "equal_weight_ratio_change_20d": self.equal_weight_ratio_change_20d,
            "above_20d": self.above_20d,
            "evaluated_20d": self.evaluated_20d,
            "above_50d": self.above_50d,
            "evaluated_50d": self.evaluated_50d,
        }


def _last_finite(series: pd.Series) -> float | None:
    """`series`'s own last row, or `None` if `series` is empty or that last row is `NaN`.

    Deliberately **not** a backward search for the most recent non-`NaN` row -- this checks
    only `series.iloc[-1]`, matching `app.scan.trend`'s own private helper of the same name and
    exact behavior (not imported from there: a two-line, module-private detail each pure
    `app.scan` module keeps to itself, the same reasoning `app.scan.indicators.true_range`
    gives for staying public rather than folded away). For a single symbol's own rolling
    indicator (`app.scan.trend`'s use) the series' last row is `NaN` only during genuine
    warm-up, so "check the last row" and "find the most recent real reading" always agree. For
    this module's *wide, multi-symbol* frames that is not guaranteed -- a symbol can have `NaN`
    specifically on the shared frame's own most recent date (it simply did not trade that day)
    while an earlier row is perfectly good data -- and this function still returns `None` there
    rather than reaching backward for that earlier, staler row. That is the conservative
    choice on purpose: silently substituting a stale reading for "today" would itself be a
    small version of the same misalignment this module's docstring warns about at length, so a
    caller that wants "today's breadth" and gets `None` back for one sector is being told
    exactly why -- that sector has no reading as of the shared frame's own last date -- not
    handed a quietly-stale number instead.
    """
    if series.empty:
        return None
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)


def sector_breadth(prices: pd.DataFrame, sectors: tuple[str, ...] | None = None) -> SectorBreadth:
    """Sector-level breadth: `RSP`/`SPY` leadership plus the count of sector ETFs trading above
    their own 20-/50-day daily SMA. See `SectorBreadth`'s own docstring for exactly what each
    field means and when it is `None`/excluded rather than a fabricated reading.

    Args:
        prices: Wide **daily** close frame containing at least `RSP`, `SPY`, and every symbol
            in `sectors`. Missing columns (a sector not present at all) are treated the same as
            a present-but-empty column -- excluded from `evaluated_20d`/`evaluated_50d`, not an
            error -- so a caller can pass whatever universe subset it already fetched without a
            prior membership check.
        sectors: Defaults to `app.scan.groups.SECTORS` (the 11 SPDR sector ETFs). Not imported
            at module level and instead defaulted here via `None` so this module has no import
            dependency on `app.scan.groups` merely to know its own default -- a caller that
            already has the tuple (the ordinary case, `app.api.scan`) passes it in; a test
            wanting a smaller fixture passes a shorter tuple without needing to reach for the
            production group list at all.

    Returns:
        `SectorBreadth`.
    """
    from app.scan.groups import SECTORS as _default_sectors

    active_sectors = sectors if sectors is not None else _default_sectors

    equal_weight_ratio: float | None = None
    equal_weight_ratio_change_20d: float | None = None
    if "RSP" in prices.columns and "SPY" in prices.columns:
        rsp = _to_float(prices["RSP"])
        spy = _to_float(prices["SPY"])
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = rsp / spy
        equal_weight_ratio = _last_finite(ratio)
        if equal_weight_ratio is not None:
            with np.errstate(invalid="ignore", divide="ignore"):
                change = ratio / ratio.shift(BREADTH_SMA_SHORT) - 1.0
            change = change.replace([np.inf, -np.inf], np.nan)
            equal_weight_ratio_change_20d = _last_finite(change)

    above_20d = 0
    evaluated_20d = 0
    above_50d = 0
    evaluated_50d = 0
    for symbol in active_sectors:
        if symbol not in prices.columns:
            continue
        close = _to_float(prices[symbol])
        last_close = _last_finite(close)
        if last_close is None:
            continue

        sma20 = _last_finite(close.rolling(window=BREADTH_SMA_SHORT, min_periods=BREADTH_SMA_SHORT).mean())
        if sma20 is not None:
            evaluated_20d += 1
            if last_close > sma20:
                above_20d += 1

        sma50 = _last_finite(close.rolling(window=BREADTH_SMA_LONG, min_periods=BREADTH_SMA_LONG).mean())
        if sma50 is not None:
            evaluated_50d += 1
            if last_close > sma50:
                above_50d += 1

    return SectorBreadth(
        equal_weight_ratio=equal_weight_ratio,
        equal_weight_ratio_change_20d=equal_weight_ratio_change_20d,
        above_20d=above_20d,
        evaluated_20d=evaluated_20d,
        above_50d=above_50d,
        evaluated_50d=evaluated_50d,
    )
