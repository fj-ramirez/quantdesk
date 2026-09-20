"""Cross-asset regime strip math -- pure module (T54,
plans/continuation/06-cross-asset-regime.md).

Answers "what kind of tape is it for everything at once" -- volatility term structure,
vol-of-vol, the volatility risk premium, cross-sector correlation, and three cross-asset
20-day moves. **Pure, exactly like `app.modules.gex.gex.engine`, `app.modules.gex.scan.indicators` and
`app.modules.gex.scan.rotation`**: no HTTP, DB, filesystem or logging anywhere in this module. Every
function takes `pd.Series`/`pd.DataFrame` closes already fetched by the caller
(`app.modules.gex.storage.bars_repository.read_universe_closes`'s wide shape) and returns a plain value, a
`(value, n)` pair, or the frozen `CrossAssetRow`; `app.modules.gex.api.scan` is the only place in this
feature that touches Postgres.

**No composite score.** Per the plan's own "Design decisions": nine tiles with rules are more
useful than one number and cannot hide a bad input. This module never combines its outputs
into a single score, and nothing here should ever grow one.

**Labels are rules, not opinions.** `term_structure` returns a fact about the curve
(`contango`/`backwardation`/`mixed`), not a trade recommendation -- see that function's own
docstring. A percentile-banded label (used by the frontend `RegimeStrip`, not computed here)
follows the same discipline: a tooltip may add one clause of historical color ("fade-friendly
historically" for contango with low VVIX, the plan's own example) and must go no further.

The cross-symbol alignment hazard, and how this module handles it
-----------------------------------------------------------------------
Same house rule `app.modules.gex.scan.rotation`'s module docstring establishes and `app.modules.gex.scan.rotation`
follows throughout: **never index two series by position and divide/subtract -- operate on
columns of the *same* `DataFrame` (or `Series` sharing the same index) so pandas aligns them by
date before any arithmetic happens.** A date one series lacks becomes `NaN` in the result
rather than silently pairing with the wrong row. `compute_cross_asset_row` takes every input as
a `pd.Series`/`pd.DataFrame` already sliced from one shared `read_universe_closes` frame (same
calling convention `app.modules.gex.api.scan.get_rotation` already uses for `app.modules.gex.scan.rotation`), and
defensively `.reindex()`s every scalar series onto the primary (`vix`) series' own index before
combining them -- a no-op in the intended calling convention, a safety net otherwise, the same
posture `app.modules.gex.scan.rotation.rrg_approx`'s own `benchmark.reindex(prices.index)` documents.

**The sector-correlation window straddling a missing bar for one ETF** (the plan's own named
"likely first-contact failure" for this task) is handled by :func:`sector_correlation`:
alignment is `DataFrame.dropna(how="any")` over the trailing window, so one sector's missing
day drops that *day* from every sector's sample, not that sector from the correlation. The
resulting sample size is always reported back (`SectorCorrelationResult.n`), never silently
absorbed into a same-looking `20`.

**Insufficient history is `None`, never a fabricated number** -- the same discipline
`app.modules.gex.scan.indicators`'s module docstring states for its own rolling indicators, extended here
to `percentile_252` (fewer than `PERCENTILE_MIN_BARS` observations) and to every field on
`CrossAssetRow` that depends on an indicator or ratio that could not be computed. A `None`
field always has a sibling `*_reason` string (or is self-evidently explained by its own
`*_n`/count field) so the frontend never has to guess why a tile reads "n/a".


Percentile convention
------------------------
`percentile_252` uses the same "average rank, scaled to `[0, 1]`, ties handled" construction
`app.modules.gex.scan.trend._percentile_ranks` already uses for its own (cross-sectional) percentiles --
reimplemented here rather than imported, because that function's percentile is *cross-sectional*
(one universe of symbols, one date) while this one is a *time-series self-percentile* (one
symbol/ratio, its own trailing history): different axis, same well-tested rank-and-scale
arithmetic, not worth threading a shared private helper across a module boundary for.


Definitions, verbatim from the plan
---------------------------------------
* **Term structure**: plain close ratios. `contango` when `VIX/VIX3M < 1` **and**
  `VIX9D/VIX < 1`; `backwardation` when both `> 1`; `mixed` otherwise (any other combination,
  including a ratio sitting at exactly `1.0`).
* **VRP** = `VIX - SPY RV20`, both annualized, in vol points (e.g. `18.2`, not `0.182`) -- Cboe
  already publishes VIX in vol points; `app.modules.gex.scan.indicators.realized_vol` returns a fraction,
  so :func:`vrp` multiplies it by 100 before subtracting.
* **Sector correlation** = mean of the pairwise 20-day correlation of daily log returns across
  the 11 sector ETFs (`app.modules.gex.scan.groups.SECTORS`, supplied by the caller as a wide closes
  frame -- this module has no built-in universe knowledge, matching `app.modules.gex.scan.rotation`'s own
  "the pure module accepts any window/frame, the caller picks the universe" split).
* **Percentiles** are over up to 252 bars (`PERCENTILE_WINDOW`), `None` under
  `PERCENTILE_MIN_BARS` (60) observations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.modules.gex.scan.indicators import RV_PERIOD, realized_vol

__all__ = [
    "PERCENTILE_MIN_BARS",
    "PERCENTILE_WINDOW",
    "SECTOR_CORRELATION_WINDOW",
    "CrossAssetRow",
    "SectorCorrelationResult",
    "compute_cross_asset_row",
    "percentile_252",
    "sector_correlation",
    "term_structure",
    "vrp",
]

#: Trailing window `percentile_252` ranks the current value against -- the plan's own "over
#: 252 bars" (roughly one trading year).
PERCENTILE_WINDOW = 252

#: Below this many finite observations, `percentile_252` returns `None` rather than a
#: percentile computed from too short (and therefore not meaningfully "one-year") a sample --
#: the plan's own explicit acceptance bar ("with fewer than 60 bars returns None").
PERCENTILE_MIN_BARS = 60

#: Trailing window `sector_correlation` computes its pairwise correlation over -- the plan's
#: own "20-day correlation".
SECTOR_CORRELATION_WINDOW = 20

#: `compute_cross_asset_row`'s 20-day return window for UUP/GLD/TLT.
_RETURN_WINDOW_DAYS = 20


def percentile_252(
    series: pd.Series, *, window: int = PERCENTILE_WINDOW, min_bars: int = PERCENTILE_MIN_BARS
) -> tuple[float | None, int]:
    """Percentile rank (`[0, 1]`) of `series`' most recent finite value within its own trailing
    `window` observations (fewer than `window` if the series itself has less history).

    Args:
        series: Any 1-D series of daily values, ascending by date. Non-finite values are
            dropped before windowing -- a gap in the underlying history narrows the sample
            (and therefore `n`), it never breaks the computation or pads with a guess.
        window: How many of the most recent finite observations to rank against. Must be `>= 1`.
        min_bars: The floor below which "not enough history" wins over "a technically
            computable but nearly meaningless percentile". Must be `>= 1`.

    Returns:
        `(percentile, n)`. `percentile` is `None` whenever `n < min_bars` -- the plan's own
        acceptance line, "with fewer than 60 bars returns None" -- `n` itself is always
        returned (even when `None`) so a caller can render *why* a tile is "n/a" rather than
        just that it is. Ties are handled by average rank, scaled to `[0, 1]`, same convention
        `app.modules.gex.scan.trend._percentile_ranks` uses for its own (cross-sectional) percentiles --
        `0.0` is the lowest value in the window, `1.0` the highest, a lone observation (only
        reachable with `min_bars <= 1`) is `0.5`.

    Raises:
        ValueError: `window < 1` or `min_bars < 1`.
    """
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    if min_bars < 1:
        raise ValueError(f"min_bars must be >= 1, got {min_bars}")

    finite = series.dropna()
    tail = finite.tail(window)
    n = len(tail)
    if n < min_bars:
        return None, n
    if n == 1:
        return 0.5, n

    ranks = tail.rank(method="average").to_numpy()
    scaled = (ranks - 1.0) / (n - 1.0)
    return float(scaled[-1]), n


def term_structure(vix: float, vix3m: float, vix9d: float) -> str:
    """The plan's three-way term-structure call from plain close ratios.

    `contango` when `VIX/VIX3M < 1` **and** `VIX9D/VIX < 1` (the whole curve slopes up from
    the front); `backwardation` when both ratios are `> 1` (the whole curve slopes down --
    near-term stress); `mixed` otherwise, including either ratio landing at exactly `1.0`. A
    fact about the curve's shape, not a trade call -- see the module docstring's "Labels are
    rules, not opinions".

    Args:
        vix: Current `^VIX` close.
        vix3m: Current `^VIX3M` close.
        vix9d: Current `^VIX9D` close.

    Returns:
        `"contango"`, `"backwardation"` or `"mixed"`.

    Raises:
        ValueError: `vix <= 0` or `vix3m <= 0` (both are ratio denominators here).
    """
    if vix <= 0:
        raise ValueError(f"vix must be > 0, got {vix}")
    if vix3m <= 0:
        raise ValueError(f"vix3m must be > 0, got {vix3m}")

    vix_vix3m = vix / vix3m
    vix9d_vix = vix9d / vix
    if vix_vix3m < 1.0 and vix9d_vix < 1.0:
        return "contango"
    if vix_vix3m > 1.0 and vix9d_vix > 1.0:
        return "backwardation"
    return "mixed"


def vrp(vix_close: float, spy_rv20: float) -> float:
    """Volatility risk premium: `VIX - SPY RV20`, both in vol points.

    Args:
        vix_close: Current `^VIX` close, already in vol points (Cboe's own convention, e.g.
            `18.2`).
        spy_rv20: SPY's annualized 20-day realized vol as a **fraction**
            (`app.modules.gex.scan.indicators.realized_vol`'s own units, e.g. `0.182` for 18.2%) -- this
            function multiplies by 100 before subtracting so both terms are in the same units.

    Returns:
        `vix_close - spy_rv20 * 100.0`, in vol points. Positive means options are pricing more
        vol than has recently realized (the usual, "vol sellers get paid" state); negative
        means realized vol has been running hotter than VIX is pricing.
    """
    return vix_close - spy_rv20 * 100.0


@dataclass(frozen=True, slots=True)
class SectorCorrelationResult:
    """`sector_correlation`'s output: the mean pairwise correlation plus the honest sample
    size it was measured over (the plan's own "report the effective sample size" requirement
    for the missing-bar alignment hazard)."""

    #: Mean of the pairwise correlation matrix's off-diagonal entries, or `None` when fewer
    #: than 2 aligned observations exist (a correlation needs at least 2 points) or fewer than
    #: 2 sector columns were supplied at all.
    mean_correlation: float | None
    #: Aligned rows actually used (after dropping any date where at least one sector column
    #: was `NaN`) -- may be less than the requested window, and is `0` when nothing aligned.
    n: int
    #: How many sector columns were supplied, informational only (not reduced by a missing
    #: column -- `read_universe_closes` always returns one column per requested symbol, filled
    #: with `NA` for a symbol with no bars at all in range).
    universe_n: int


def sector_correlation(
    closes: pd.DataFrame, *, window: int = SECTOR_CORRELATION_WINDOW
) -> SectorCorrelationResult:
    """Mean pairwise `window`-day correlation of daily log returns across every column of
    `closes` (the plan's own "sector correlation" definition, generalized to whatever columns
    the caller supplies -- the 11-sector universe is the caller's choice, per the module
    docstring).

    Args:
        closes: Wide closes frame, one column per sector ETF, one row per date, ascending --
            `app.modules.gex.storage.bars_repository.read_universe_closes`'s own shape. Log returns are
            computed from this frame's own columns (`np.log(closes / closes.shift(1))`), never
            from a series divided positionally against another -- the module docstring's
            alignment discipline.
        window: Trailing number of *sessions* to compute the correlation over. Dates on which
            no column in `closes` has a bar are dropped before the window is taken (see the
            comment in the body for the measured reason), so this counts sessions of this
            universe's own calendar rather than rows of whatever wider calendar the caller's
            frame happens to carry. Must be `>= 2`.

    Returns:
        `SectorCorrelationResult`. See the module docstring's "sector-correlation window
        straddling a missing bar" note for exactly how a gap in one column is handled: the
        whole *date* is dropped from the aligned sample (`DataFrame.dropna(how="any")`), so
        `n` -- not `window` -- is the true sample size the correlation was computed over.

    Raises:
        ValueError: `window < 2`.
    """
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")

    universe_n = closes.shape[1]
    if universe_n < 2:
        return SectorCorrelationResult(mean_correlation=None, n=0, universe_n=universe_n)

    # `read_universe_closes` fills a symbol with zero bars in range as a column of `pd.NA`
    # (object dtype), not `np.nan` -- a plain `.astype(float)` raises `TypeError` on that
    # column (`float()` does not accept `pd.NA`). `pd.to_numeric(..., errors="coerce")`
    # handles both `pd.NA` and a genuine `np.nan` uniformly, turning either into a real
    # float `NaN` that the rest of this function's `NaN`-aware arithmetic already expects.
    numeric = closes.apply(pd.to_numeric, errors="coerce")
    # Drop dates on which *no* column has a bar **before** taking the trailing window, so
    # `window` counts sessions this universe actually trades rather than rows of whatever
    # calendar the caller's frame happens to carry. Measured why this matters: the
    # `/api/gex/scan/cross-asset` endpoint reads one shared wide frame holding the sector ETFs
    # *and* the Cboe index symbols, and Cboe's index calendar has ~33 dates over five years
    # that the ETF histories do not (1287 `^VIX` rows against 1254 for every sector). Those
    # index-only dates are all-`NaN` in this sector slice, so a `tail(20)` taken first landed
    # on them and the "20-day" correlation was computed from 18 observations *every day* --
    # honestly reported via `n`, but systematically short of the window asked for.
    #
    # This is deliberately `how="all"`, not `how="any"`: an all-`NaN` row means "not a session
    # for this universe at all" (a calendar artifact), while a row where only *some* column is
    # missing is a genuine per-symbol gap. The latter is still dropped below, after the window
    # is taken, and is still what makes `n < window` -- which is the case the plan's
    # "straddling a missing bar" note is about, and the reason `n` is reported at all.
    sessions = numeric.dropna(how="all")
    with np.errstate(invalid="ignore", divide="ignore"):
        log_returns = np.log(sessions / sessions.shift(1))
    windowed = log_returns.tail(window)
    aligned = windowed.dropna(how="any")
    n = len(aligned)
    if n < 2:
        return SectorCorrelationResult(mean_correlation=None, n=n, universe_n=universe_n)

    corr = aligned.corr().to_numpy()
    k = corr.shape[0]
    off_diagonal = corr[~np.eye(k, dtype=bool)]
    mean_corr = float(np.mean(off_diagonal)) if off_diagonal.size else None
    return SectorCorrelationResult(mean_correlation=mean_corr, n=n, universe_n=universe_n)


def _last_finite(series: pd.Series) -> float | None:
    """Most recent non-`NaN` value in `series`, or `None`. Same small helper every other T5x
    scan module (`app.modules.gex.api.scan`, `app.modules.gex.scan.trend`) defines privately for itself rather than
    sharing across a module boundary for one three-line function."""
    finite = series.dropna()
    if finite.empty:
        return None
    return float(finite.iloc[-1])


def _n_day_return(closes: pd.Series, n: int) -> float | None:
    """Plain close-to-close return over the last `n` *observed* (finite) closes -- `None` when
    fewer than `n + 1` finite closes exist, or the bar `n` observations back closed at exactly
    zero (division would be undefined, not a real return). Mirrors
    `app.modules.gex.api.scan._return_5d`'s contract, generalized to any `n` and to a bare closes `Series`
    rather than a bars `DataFrame`."""
    finite = closes.dropna()
    if len(finite) <= n:
        return None
    prior = float(finite.iloc[-(n + 1)])
    if prior == 0.0:
        return None
    return float(finite.iloc[-1]) / prior - 1.0


@dataclass(frozen=True, slots=True)
class CrossAssetRow:
    """One snapshot of the cross-asset regime strip -- everything `RegimeStrip` (frontend,
    T54/07-ui.md) renders as its nine tiles, assembled by :func:`compute_cross_asset_row`.

    Every `None` field is a real, named state (see each field's own comment for why), never a
    fabricated placeholder -- CLAUDE.md invariant 3's None-vs-zero discipline, extended to
    every optional metric this row carries. No field here combines two or more of the others
    into a composite score -- see the module docstring's "No composite score".
    """

    #: Most recent date any of `vix`/`vvix` had a finite close, or `None` if neither did.
    as_of: Any

    vix: float | None
    vix3m: float | None
    vix9d: float | None
    vix_vix3m_ratio: float | None
    vix_vix3m_ratio_pct: float | None
    vix_vix3m_ratio_pct_n: int
    vix9d_vix_ratio: float | None
    vix9d_vix_ratio_pct: float | None
    vix9d_vix_ratio_pct_n: int
    #: `"contango"` / `"backwardation"` / `"mixed"`, or `None` -- see `term_structure_reason`.
    term_structure: str | None
    #: Populated exactly when `term_structure` is `None` (missing or non-positive VIX/VIX3M).
    term_structure_reason: str | None

    vvix: float | None
    vvix_pct: float | None
    vvix_pct_n: int

    #: VIX's own trailing 1-year percentile -- the strip's "VIX 1y pct" tile.
    vix_pct: float | None
    vix_pct_n: int

    #: SPY's annualized 20-day realized vol, as a fraction (see `vrp`'s own docstring for units).
    spy_rv20: float | None
    vrp: float | None
    vrp_pct: float | None
    vrp_pct_n: int
    #: Populated exactly when `vrp` is `None` (missing VIX close, or fewer than
    #: `RV_PERIOD + 1` SPY closes to seed RV20).
    vrp_reason: str | None

    sector_correlation: float | None
    sector_correlation_n: int
    sector_correlation_universe_n: int

    uup_return_20d: float | None
    gld_return_20d: float | None
    tlt_return_20d: float | None

    def to_dict(self) -> dict[str, Any]:
        """Flat dict, field name -> value -- same `dataclasses.asdict` convention
        `app.modules.gex.scan.breakouts.BreakoutEvent`/`app.modules.gex.scan.trend.TrendComponents` already use, so
        `app.modules.gex.api.scan`'s response model can build itself with `cls(**row.to_dict())`."""
        return asdict(self)


def compute_cross_asset_row(
    *,
    vix: pd.Series,
    vix3m: pd.Series,
    vix9d: pd.Series,
    vvix: pd.Series,
    spy_close: pd.Series,
    sector_closes: pd.DataFrame,
    uup_close: pd.Series,
    gld_close: pd.Series,
    tlt_close: pd.Series,
) -> CrossAssetRow:
    """Assemble one `CrossAssetRow` from the raw closes series/frames a caller has already
    fetched (in practice, columns sliced out of one shared
    `app.modules.gex.storage.bars_repository.read_universe_closes` frame -- see the module docstring's
    alignment discipline for why sharing one source frame matters).

    Args:
        vix, vix3m, vix9d, vvix: `^VIX`/`^VIX3M`/`^VIX9D`/`^VVIX` closes, ascending by date.
        spy_close: SPY closes, ascending by date -- feeds `spy_rv20` (via
            `app.modules.gex.scan.indicators.realized_vol`) and therefore `vrp`.
        sector_closes: Wide closes frame for the sector-correlation universe (11 columns under
            the app's own default, but this function does not assume a count) -- passed
            straight to `sector_correlation`.
        uup_close, gld_close, tlt_close: Closes for the three 20-day-return tiles.

    Every series/frame is defensively `.reindex()`d onto `vix`'s own index before any
    subtraction/division against it -- the module docstring's "safety net, no-op in the
    intended calling convention" note. `sector_closes` is the one exception: its correlation is
    computed purely among its own columns and does not need to share `vix`'s date range.

    Returns:
        `CrossAssetRow`. No field here is fabricated when an input is missing -- see that
        dataclass's own docstring.
    """
    # `read_universe_closes` fills a symbol with zero bars in the requested range as a column
    # of `pd.NA` (object dtype), not `np.nan` -- every arithmetic op below assumes a real
    # float `NaN`, so every input is coerced through `pd.to_numeric(..., errors="coerce")`
    # first (same fix, same reason, as `sector_correlation`'s own docstring note).
    vix = pd.to_numeric(vix, errors="coerce")
    vix3m = pd.to_numeric(vix3m, errors="coerce").reindex(vix.index)
    vix9d = pd.to_numeric(vix9d, errors="coerce").reindex(vix.index)
    vvix = pd.to_numeric(vvix, errors="coerce").reindex(vix.index)
    spy_close = pd.to_numeric(spy_close, errors="coerce").reindex(vix.index)
    uup_close = pd.to_numeric(uup_close, errors="coerce")
    gld_close = pd.to_numeric(gld_close, errors="coerce")
    tlt_close = pd.to_numeric(tlt_close, errors="coerce")

    vix_finite = vix.dropna()
    as_of = vix_finite.index[-1] if not vix_finite.empty else None

    vix_last = _last_finite(vix)
    vix3m_last = _last_finite(vix3m)
    vix9d_last = _last_finite(vix9d)

    with np.errstate(invalid="ignore", divide="ignore"):
        vix_vix3m_series = vix / vix3m
        vix9d_vix_series = vix9d / vix
    vix_vix3m_ratio = _last_finite(vix_vix3m_series)
    vix9d_vix_ratio = _last_finite(vix9d_vix_series)
    vix_vix3m_pct, vix_vix3m_pct_n = percentile_252(vix_vix3m_series)
    vix9d_vix_pct, vix9d_vix_pct_n = percentile_252(vix9d_vix_series)

    ts_label: str | None = None
    ts_reason: str | None = None
    if vix_last is None or vix3m_last is None or vix9d_last is None:
        ts_reason = "missing VIX, VIX3M or VIX9D close"
    else:
        try:
            ts_label = term_structure(vix_last, vix3m_last, vix9d_last)
        except ValueError as exc:
            ts_reason = str(exc)

    vvix_pct, vvix_pct_n = percentile_252(vvix)
    vix_pct, vix_pct_n = percentile_252(vix)

    # `.dropna()` first: realized vol is a *rolling* window, so a single all-`NaN` calendar row
    # inside the caller's frame does not cost one observation, it costs `RV_PERIOD` of them --
    # every trailing window that row falls into comes back `NaN`. Measured against live data:
    # the `/api/gex/scan/cross-asset` frame is the union calendar of the ETFs and the Cboe index
    # symbols, and over a 400-day lookback it carried **8** dates `^VIX` has and `SPY` does not.
    # Those 8 rows dropped the valid `RV20` count from 256 to 129, and with it `vrp_pct` -- a
    # tile labelled a one-year percentile was computed from 129 observations rather than 252.
    # Restricting to SPY's own sessions restores the full 252. (`sector_correlation` handles the
    # same hazard for its own frame; see the comment in its body.)
    spy_rv20_series = realized_vol(pd.DataFrame({"close": spy_close.dropna()}))
    spy_rv20_last = _last_finite(spy_rv20_series)

    vrp_value: float | None = None
    vrp_reason: str | None = None
    if vix_last is None or spy_rv20_last is None:
        vrp_reason = f"missing VIX close or SPY RV{RV_PERIOD} (needs >= {RV_PERIOD + 1} SPY closes)"
    else:
        vrp_value = vrp(vix_last, spy_rv20_last)
    vrp_series = vix - spy_rv20_series * 100.0
    vrp_pct, vrp_pct_n = percentile_252(vrp_series)

    corr_result = sector_correlation(sector_closes)

    return CrossAssetRow(
        as_of=as_of,
        vix=vix_last,
        vix3m=vix3m_last,
        vix9d=vix9d_last,
        vix_vix3m_ratio=vix_vix3m_ratio,
        vix_vix3m_ratio_pct=vix_vix3m_pct,
        vix_vix3m_ratio_pct_n=vix_vix3m_pct_n,
        vix9d_vix_ratio=vix9d_vix_ratio,
        vix9d_vix_ratio_pct=vix9d_vix_pct,
        vix9d_vix_ratio_pct_n=vix9d_vix_pct_n,
        term_structure=ts_label,
        term_structure_reason=ts_reason,
        vvix=_last_finite(vvix),
        vvix_pct=vvix_pct,
        vvix_pct_n=vvix_pct_n,
        vix_pct=vix_pct,
        vix_pct_n=vix_pct_n,
        spy_rv20=spy_rv20_last,
        vrp=vrp_value,
        vrp_pct=vrp_pct,
        vrp_pct_n=vrp_pct_n,
        vrp_reason=vrp_reason,
        sector_correlation=corr_result.mean_correlation,
        sector_correlation_n=corr_result.n,
        sector_correlation_universe_n=corr_result.universe_n,
        uup_return_20d=_n_day_return(uup_close, _RETURN_WINDOW_DAYS),
        gld_return_20d=_n_day_return(gld_close, _RETURN_WINDOW_DAYS),
        tlt_return_20d=_n_day_return(tlt_close, _RETURN_WINDOW_DAYS),
    )
