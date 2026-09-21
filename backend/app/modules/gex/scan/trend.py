"""Trend-versus-chop scorer -- pure module (T45, plans/continuation/02-trend-chop-scorer.md).

Answers a different question than `app.modules.gex.scan.breakouts`: not "how have past breakouts played
out" but "what does the *current* tape look like, right now, across the universe." **Pure,
exactly like `app.modules.gex.gex.engine` and `app.modules.gex.scan.breakouts`**: no HTTP, DB, filesystem or logging.
`score_symbol` takes a `pd.DataFrame` of daily bars (the `read_bars` shape -- see
`app.modules.gex.scan.indicators`) plus an already-looked-up `iv30: float | None`; `app.modules.gex.api.scan` is where
the IV lookup itself happens (the latest stored option-chain snapshot, read the same way
`app.modules.gex.api.report` does), so this module never touches Parquet or Postgres and never decides
which symbols have chains -- it only ever sees the number it was handed, or `None`.


Design decisions (plan's "Design decisions" -- the deliverable's contract)
-----------------------------------------------------------------------------

* **Components, then a rank composite, never thresholds.** No absolute cut-off anywhere in
  this module ("ADX > 25 means trend" is folklore per the plan). `rank_universe` converts each
  component to a cross-sectional percentile across whatever universe it is given, on the same
  date, and the composite is the mean of the *finite* percentiles -- never a threshold
  classification.
* **Four components feed the composite: ADX14, ER20, CHOP14, VR(5) z.** These are the plan's
  "several independent measures" of trending-versus-ranging. `RV20`, `IV30` and `IV/RV` travel
  through `TrendComponents`/`TrendRow` for the table (per the plan's "What the user sees"
  column list) but never enter the composite:

  - **`IV/RV` is a hint, not a component** (plan, verbatim) -- "it exists for a minority of the
    universe" (28 of 47 `SCAN_UNIVERSE` symbols have an option chain at all, per T45's task
    brief). Folding it into the composite would silently reward/penalize optioned symbols
    against unoptioned ones on an axis the majority of the universe cannot even report.
  - **`RV20` is excluded for a different, but analogous, reason: it is not a trend-versus-chop
    reading at all.** It measures *how much* a symbol moves, not *whether* that movement nets
    out into a persistent direction or cancels itself out -- a high-RV symbol can be
    trending (large ADX, `ER` near 1) or violently chopping (large ADX-adjacent noise, `ER`
    near 0) with an identical RV20. Averaging it into "is this trending" would conflate two
    independent axes the same way averaging `IV30` itself (not `IV/RV`) into the composite
    would.
* **Percentile ties use the average-rank method** (plan's own "Likely first-contact failures":
  "Percentiles with ties. Use average rank and say so") -- `pandas.Series.rank(method=
  "average")`, scaled to `[0, 1]` via `(rank - 1) / (n - 1)`. With `n` distinct values this
  makes the lowest exactly `0.0`, the highest exactly `1.0`, and every tie share the mean of
  the ranks it spans.
* **Direction is normalized before averaging.** `ADX14` and `ER20` already read "higher =
  more trending." `CHOP14` reads the opposite (higher = choppier), so its percentile is taken
  on `-CHOP14` before scaling. `VR(5) z`'s *sign* carries the trend/chop meaning directly
  (plan: "positive means momentum, negative means mean reversion"), so its percentile is taken
  on the signed `z`, not `|z|` -- a large negative `z` (strong mean reversion, i.e. strong
  *chop*) must rank low, the same as a low `ADX`/`ER` or a high `CHOP`, not high. After this
  normalization, `1.0` means "most trending" on every one of the four inputs, which is what
  makes a plain unweighted mean of them meaningful at all.


How this was validated
------------------------

`docs/validation-scan.md` records: (1) a straight-line 25-bar fixture producing `ER = 1.0`,
`CHOP` at its theoretical floor, and a strongly positive `VR(5) z` -- the plan's own named
property check for "a trending series scores high"; (2) 100 seeded i.i.d. Gaussian-noise
fixtures, `VR ≈ 1` and `|z| < 2` on at least 95 of them -- the plan's own acceptance bar for "a
random walk lands near the middle on variance ratio"; (3) a 3-symbol `rank_universe` fixture
with three distinct composite inputs, percentiles landing at exactly `{0.0, 0.5, 1.0}` -- the
plan's own acceptance criterion, verbatim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.modules.gex.scan.indicators import (
    ADX_PERIOD,
    CHOP_PERIOD,
    ER_PERIOD,
    REL_VOLUME_PERIOD,
    RV_PERIOD,
    VR_Q,
    adx,
    choppiness,
    efficiency_ratio,
    realized_vol,
    relative_volume,
    variance_ratio,
)

__all__ = [
    "TREND_LOOKBACK",
    "TrendComponents",
    "TrendRow",
    "rank_universe",
    "score_symbol",
]

#: `variance_ratio`'s sample window, per the plan's design decision: "VR(q=5) on log returns
#: over 126 bars" -- the same ~6-month window `app.modules.gex.scan.breakouts.DEFAULT_LOOKBACK` uses for
#: its own summary, though the two are unrelated constants in unrelated modules that happen to
#: agree on "about 6 months of daily bars" as this codebase's standing definition of "recent."
TREND_LOOKBACK = 126


@dataclass(frozen=True, slots=True)
class TrendComponents:
    """One symbol's raw trend/chop readings as of its most recent bar -- no cross-sectional
    context yet (that is `rank_universe`'s job; a single symbol's numbers cannot be percentile-
    ranked against nothing). Every indicator field is `None` exactly when `app.modules.gex.scan.indicators`
    reported `NaN` for that symbol's most recent bar (insufficient history for that specific
    indicator's warm-up window -- see each indicator's own docstring for its exact threshold),
    translated to a plain `None` at this dataclass boundary the same way
    `app.modules.gex.scan.breakouts.BreakoutEvent.follow_through_atr` already translates its own `NaN`
    inputs. `iv30`/`iv_rv_ratio` are additionally `None` whenever the caller passed `iv30=None`
    in the first place (no option chain for this symbol, or one that has never been captured)
    -- this dataclass never substitutes a default for either reason, and does not distinguish
    between them (the API layer's `iv30=None` already collapses "no chain" and "insufficient
    IV history within a chain that exists" into one `None`, matching `app.modules.gex.gex.report.iv_regime`
    's own contract).
    """

    adx14: float | None
    er20: float | None
    chop14: float | None
    vr: float | None
    vr_z: float | None
    rv20: float | None
    iv30: float | None
    iv_rv_ratio: float | None
    #: T92. How much volume confirmed the most recent bar, against its own trailing baseline.
    #: Deliberately *not* a component of the composite and not percentile-ranked: this task
    #: adds the reading, and changing which symbols score as trending is a change to what the
    #: desk is told to trade, which deserves its own before/after rather than a ride-along.
    #: `None` for the five index symbols that report no volume at all -- never `0.0`.
    #:
    #: Defaulted so this stayed an additive field: every existing construction site, in this
    #: module and in the tests, keeps working unchanged. `score_symbol` always sets it, and
    #: `test_score_symbol_populates_rel_volume` is what stops the default quietly becoming the
    #: value the API serves.
    rel_volume: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adx14": self.adx14,
            "er20": self.er20,
            "chop14": self.chop14,
            "vr": self.vr,
            "vr_z": self.vr_z,
            "rv20": self.rv20,
            "iv30": self.iv30,
            "iv_rv_ratio": self.iv_rv_ratio,
            "rel_volume": self.rel_volume,
        }


@dataclass(frozen=True, slots=True)
class TrendRow:
    """One symbol's `TrendComponents` plus its cross-sectional percentile on each of the four
    composite-feeding components, and the composite itself -- one row of the `/scan?view=trend`
    table. `*_pct` fields are `None` exactly when the underlying component itself is `None` (a
    symbol cannot be ranked on a reading it does not have); `composite` is `None` only when
    *all four* are `None` (nothing at all to average -- see `rank_universe`'s docstring for why
    a *partial* set still produces a composite rather than propagating `None`).
    """

    symbol: str
    components: TrendComponents
    adx_pct: float | None
    er_pct: float | None
    chop_pct: float | None
    vr_pct: float | None
    composite: float | None

    def to_dict(self) -> dict[str, Any]:
        merged = self.components.to_dict()
        merged["symbol"] = self.symbol
        merged["adx_pct"] = self.adx_pct
        merged["er_pct"] = self.er_pct
        merged["chop_pct"] = self.chop_pct
        merged["vr_pct"] = self.vr_pct
        merged["composite"] = self.composite
        return merged


def _last_finite(series: pd.Series) -> float | None:
    """The most recent non-`NaN` value in `series`, or `None` for an empty series or one that
    is `NaN` all the way to its last element (insufficient history for the *entire* series
    length, not just its warm-up prefix -- e.g. a symbol with only 5 bars total handed to
    `adx`, whose ~27-bar warm-up never completes at all).
    """
    if series.empty:
        return None
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)


def score_symbol(bars: pd.DataFrame, iv30: float | None) -> TrendComponents:
    """Compute every trend/chop component for `bars`, as of its most recent row.

    `bars` is the full available history for one symbol, ascending by date (not pre-trimmed to
    any particular lookback) -- `app.modules.gex.scan.indicators.adx`/`efficiency_ratio`/`choppiness`/
    `realized_vol` are rolling indicators that want as much prior context as exists to have
    warmed up by "today," and this function reads only each one's *last* value
    (:func:`_last_finite`). `variance_ratio` is the one exception: it is not rolling (see
    `app.modules.gex.scan.indicators`'s module docstring), so this function is what slices `bars` down to
    :data:`TREND_LOOKBACK` bars before handing it over -- the plan's "VR(q=5) on log returns
    over 126 bars" is enforced exactly here, not inside the pure indicator itself.

    `iv30` is supplied by the caller (`app.modules.gex.api.scan`, following `api/report.py`'s snapshot
    lookup pattern) rather than looked up here -- this module stays pure, exactly like
    `app.modules.gex.gex.engine` (CLAUDE.md invariant 1) and exactly the caller/pure split T43 already used
    for its own calendar dependency (`app.modules.gex.jobs.calendar` stays out of `app.modules.gex.scan.breakouts` the
    same way Parquet/Postgres stay out of this module).

    Args:
        bars: Ascending-by-date daily bars (`date, open, high, low, close, volume, source`).
            May be empty or short -- every component degrades to `None` rather than raising.
        iv30: The ATM ~30-day implied vol for this symbol's latest captured option chain, or
            `None` if it has none (or none yet captured).

    Returns:
        `TrendComponents`. Never raises on `bars` alone.
    """
    adx14 = _last_finite(adx(bars, ADX_PERIOD))
    er20 = _last_finite(efficiency_ratio(bars, ER_PERIOD))
    chop14 = _last_finite(choppiness(bars, CHOP_PERIOD))
    rv20 = _last_finite(realized_vol(bars, RV_PERIOD))
    rel_vol = _last_finite(relative_volume(bars, REL_VOLUME_PERIOD))

    vr_window = bars.tail(TREND_LOOKBACK)
    vr, vr_z = variance_ratio(vr_window, VR_Q) if len(vr_window) >= 2 else (None, None)

    iv_rv_ratio = None
    if iv30 is not None and rv20 is not None and rv20 != 0.0:
        iv_rv_ratio = iv30 / rv20

    return TrendComponents(
        adx14=adx14,
        er20=er20,
        chop14=chop14,
        vr=vr,
        vr_z=vr_z,
        rv20=rv20,
        iv30=iv30,
        iv_rv_ratio=iv_rv_ratio,
        rel_volume=rel_vol,
    )


def _percentile_ranks(
    values: dict[str, float | None], *, higher_is_more_trending: bool
) -> dict[str, float | None]:
    """Cross-sectional percentile rank of every finite value in `values` against every other
    finite value in it, scaled to `[0, 1]` (`0.0` = lowest, `1.0` = highest) via average-rank
    ties (plan: "Percentiles with ties. Use average rank and say so"). A symbol whose own value
    is `None` (or non-finite) gets `None` back and is excluded from the ranking of everyone
    else's percentile too -- it contributes no information about where it would have placed.

    `higher_is_more_trending=False` negates the values before ranking (used only for
    `chop14`), so that after this function returns, `1.0` always means "most trending" on
    every component regardless of that component's own raw direction -- see `rank_universe`'s
    module-level "Design decisions" for why that normalization is what makes a plain mean of
    the four percentiles meaningful.

    A universe of exactly one finite value has nothing to rank it against; that one symbol gets
    `0.5` (the middle of `[0, 1]`) rather than an arbitrary `0.0` or `1.0` fabricated from a
    comparison that does not exist -- a real, if degenerate, state (e.g. a `SCAN_UNIVERSE`
    symbol added before its peers' first bars-job run).
    """
    finite = {
        symbol: value
        for symbol, value in values.items()
        if value is not None and math.isfinite(value)
    }
    if not finite:
        return dict.fromkeys(values)

    symbols = list(finite.keys())
    raw = np.array([finite[s] for s in symbols], dtype=float)
    if not higher_is_more_trending:
        raw = -raw

    n = raw.size
    if n == 1:
        percentiles = {symbols[0]: 0.5}
    else:
        ranks = pd.Series(raw).rank(method="average").to_numpy()
        scaled = (ranks - 1.0) / (n - 1.0)
        percentiles = {symbols[i]: float(scaled[i]) for i in range(n)}

    return {symbol: percentiles.get(symbol) for symbol in values}


def rank_universe(components_by_symbol: dict[str, TrendComponents]) -> list[TrendRow]:
    """Cross-sectional percentiles and the composite trend score for every symbol in
    `components_by_symbol`, all ranked against each other on the same date.

    Only `adx14`, `er20`, `chop14` and `vr_z` are ranked and averaged into `composite` -- see
    the module's "Design decisions" for why `rv20`/`iv30`/`iv_rv_ratio` are excluded from it
    even though they travel through onto every `TrendRow` for the table. A symbol missing one
    component (e.g. enough history for `ADX`/`ER`/`CHOP` but not the full 126-bar `VR` window)
    is still ranked on every component it does have, and its `composite` is the mean of
    whichever of the (up to four) percentiles are not `None` -- not `None` itself, since a
    partial set of real percentiles is still real information, unlike substituting a neutral
    value for a genuinely missing one (which is what CLAUDE.md's "no fabricated default for
    `None`" invariant, and this plan's `IV30`-specific version of it, actually forbid). A
    symbol with *zero* finite components (e.g. no bars stored yet at all) gets
    `composite=None`.

    Args:
        components_by_symbol: One `TrendComponents` per symbol, already computed by
            `score_symbol`. Ranking is over exactly this set -- a caller that wants a
            sub-universe rank (a sector-only leaderboard, say) filters before calling this,
            not after.

    Returns:
        One `TrendRow` per key in `components_by_symbol`, in the same iteration order.
    """
    adx_pct = _percentile_ranks(
        {symbol: c.adx14 for symbol, c in components_by_symbol.items()},
        higher_is_more_trending=True,
    )
    er_pct = _percentile_ranks(
        {symbol: c.er20 for symbol, c in components_by_symbol.items()},
        higher_is_more_trending=True,
    )
    chop_pct = _percentile_ranks(
        {symbol: c.chop14 for symbol, c in components_by_symbol.items()},
        higher_is_more_trending=False,
    )
    vr_pct = _percentile_ranks(
        {symbol: c.vr_z for symbol, c in components_by_symbol.items()},
        higher_is_more_trending=True,
    )

    rows: list[TrendRow] = []
    for symbol, components in components_by_symbol.items():
        pcts = [adx_pct[symbol], er_pct[symbol], chop_pct[symbol], vr_pct[symbol]]
        finite_pcts = [p for p in pcts if p is not None]
        composite = float(np.mean(finite_pcts)) if finite_pcts else None
        rows.append(
            TrendRow(
                symbol=symbol,
                components=components,
                adx_pct=adx_pct[symbol],
                er_pct=er_pct[symbol],
                chop_pct=chop_pct[symbol],
                vr_pct=vr_pct[symbol],
                composite=composite,
            )
        )
    return rows
