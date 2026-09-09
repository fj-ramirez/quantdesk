"""Shared technical indicators for `app/scan/*` (T43,
plans/continuation/01-breakout-ledger.md).

**Pure, exactly like `app.gex.engine`**: no HTTP, DB, filesystem or logging anywhere in this
module. Every function takes a `pd.DataFrame` of daily bars -- the shape `app.storage
.bars_repository.read_bars` returns: columns `date, open, high, low, close, volume, source`,
ascending by date, one row per trading day, `date` a `datetime.date` (never a `datetime`) --
and returns a `pd.Series` aligned 1:1 to `bars.index` by position. Nothing here touches the
network, a database, or a clock; a caller builds the frame (from the repository, or by hand in
a test) and everything downstream is deterministic.

This module holds exactly one indicator today, `atr`, because T43 needs only that one. It is
written as a **shared module from the start**, not a private helper `app/scan/breakouts.py`
happens to expose, because T45 (trend scorer) extends this exact file with `adx`,
`efficiency_ratio`, `choppiness`, `realized_vol` and `variance_ratio`. Every future addition
here must keep the same two conventions `atr` establishes:

* **Signature**: `indicator(bars: pd.DataFrame, period: int = N, ...) -> pd.Series`, one row
  per input row, no resampling and no reindexing by date.
* **Insufficient history is `NaN`, never a fabricated `0.0` or a silently shortened window.**
  This is the same discipline `app.gex.engine.GexLevel` applies to a missing scalar level
  (CLAUDE.md invariant 3's "`None` means unknown, never collapse it into a real zero"), carried
  over to a `Series`: pandas has no per-element nullable-float sentinel that survives ordinary
  arithmetic as cleanly as `NaN` does, so `NaN` is this module's `None`. A caller who wants a
  scalar out of a `Series` (`app.scan.breakouts.detect_events` does, for the ATR value at one
  specific bar) gets `NaN` back the same way and must decide there, explicitly, what an
  unmeasurable window means for that caller -- this module never decides it for them by
  dropping rows or padding with a plausible-looking number.

`true_range` is exposed publicly, not folded into `atr`, because Wilder's ADX (T45) is built
from the same true-range series plus directional movement -- computing it twice from two
near-identical private helpers is exactly the kind of duplication this module exists to avoid.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["ATR_PERIOD", "atr", "true_range"]

#: The window `app.scan.breakouts` uses for follow-through and excursion sizing (T43 plan:
#: "Follow-through ... / ATR14[t]"). Exported so callers name the same constant rather than
#: repeating the literal `14`.
ATR_PERIOD = 14


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
    "how many recent typical days did the market move" units, not a live trading signal). Any
    alternative smoothing is recorded as such in `docs/validation-scan.md` per the plan.

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
