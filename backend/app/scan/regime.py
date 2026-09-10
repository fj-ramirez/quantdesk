"""Regime metrics -- pure module (T48, plans/continuation/03-regime-board.md).

Answers, per optioned symbol, "why is this fading or continuing": the dealer gamma sign, how
far spot sits from the flip point, how much room exists to the next wall in each direction, how
much of the gamma is 0DTE, implied-versus-realized vol, and a deterministic verdict built from
those numbers.

**Pure, exactly like `app.gex.engine`, `app.scan.breakouts` and `app.scan.trend`**: no HTTP, no
database, no filesystem, no logging. In particular this module does **not** import
`app.jobs.calendar`, for the same reason `app.scan.breakouts`'s own docstring gives for the same
exclusion: that module's `is_market_holiday` calls `logger.error(...)` on a stale calendar year,
which is itself a purity violation, so nothing that transitively logs may be imported here.
Staleness is therefore a **caller-computed input**, not something this module derives from a
clock -- `app.api.scan` calls `app.jobs.calendar.effective_data_time` (T34) once per symbol and
hands this module the already-computed `effective_at` / `chain_age_minutes` / `stale`, the same
caller/pure split T43 established for its own calendar dependency and T45 established for its
own `iv30` lookup.

Two engine types are reused rather than re-invented, per this task's brief:

* `app.gex.engine.KeyLevels` -- the persisted `gex_levels` row, already the exact shape this
  module needs (`net_gex`, `abs_gex`, `call_wall`/`put_wall` and their `*_gex`, `flip_point`,
  `spot`). Everything in it can come straight off a `GexLevel` database row -- see
  `app.api.scan._load_regime_inputs` for how the caller reconstructs one without opening
  Parquet.
* `app.gex.engine.StrikeGex` -- the persisted `gex_by_strike` rows, for the by-strike scans
  (room beyond, 0DTE share) below.

**Deliberately not `app.gex.engine.GexResult` itself**, even though the task's own plan
document describes this module as consuming one. `GexResult` also carries `profile`,
`diagnostics` and `expiries` -- none of which `gex_levels`/`gex_by_strike` persist, and none of
which any metric below reads. Building a real `GexResult` from persisted rows would mean
fabricating a `GexDiagnostics` (contract counts, IV-policy audit fields) that no capture ever
actually measured for this call, which is exactly the kind of invented-value the codebase's
None-vs-zero discipline (CLAUDE.md invariant 3) exists to prevent -- a fabricated `contracts=0`
reads identically to a genuine "zero contracts admitted" to anyone who later inspects it.
Accepting the two pieces (`KeyLevels`, `StrikeGex` rows) this module actually needs, instead of
the full envelope, keeps every field on `RegimeRow` traceable to a real, persisted number.

0DTE share
----------

**Measured against the live 16:20 SPY EOD snapshot (2026-09-09, id 38) and found to be
structurally unavailable on every EOD-only capture** -- see
`plans/continuation/03-regime-board.md`'s "The 0DTE share is not derivable from an EOD
snapshot" section for the full write-up; this is the summary. `gex_by_strike` does store a row
per `(snapshot, filter, strike)` for every filter `app.gex.store.DEFAULT_FILTERS` computes --
`ALL`, `ZERO_DTE`, `EX_ZERO_DTE` -- so the *mechanism* T48's original brief describes (derive
the share as the ratio of the two filters' |GEX|, at the same strike set) is real. What breaks
it is upstream of storage: on a same-day 16:20 ET capture, that day's own 0DTE series has
**already left Cboe's payload** by the time the snapshot is taken (verified on the live SPY
snapshot: earliest expiry `dte=1`, 330 contracts counted `expired` in the engine's own
diagnostics) -- so `ZERO_DTE`'s by-strike rows are empty not because there is no 0DTE gamma,
but because this snapshot cannot see same-day expiries at all. `0 / 483` (the live measurement)
would misreport "no 0DTE gamma today" as a claim about the market when the true state is "this
instrument cannot be observed from this chain."

:func:`zero_dte_share` therefore returns `None` -- never `0.0` -- whenever `ZERO_DTE`'s
by-strike rows are empty; **this is now the ordinary case for every EOD-only snapshot this app
has, not a corner case**, and genuine 0DTE composition only becomes available once intraday
capture exists (Phase 4 of the plan). When `ZERO_DTE`'s rows are *not* empty (the T47
verified-facts case of a thin ETF's `is_eod` row landing before its same-day series expired,
e.g. XBI at 11:39), the ratio is computed exactly as the original brief describes, restricted
to the strikes `ZERO_DTE` actually admitted (the plan's "at the same strike set" phrase) so a
strike with only longer-dated contracts cannot dilute the denominator.

**Consequence for the fade rule.** The first-cut fade rule (below) requires "0DTE share above a
documented floor." Since the share is `None` on essentially every real row today, a literal
reading of that rule would make `fade` unreachable in practice -- silently losing a third of
this module's vocabulary the day it ships. :func:`_verdict` **drops the 0DTE clause when the
share is unavailable** rather than treating an unmeasurable input as a failing one, and always
names which happened in `RegimeRow.reasons` ("0DTE share unavailable ... clause dropped" versus
"0DTE share N% ... below/above the floor") so a reader never mistakes a `fade` verdict reached
without 0DTE evidence for one that had it. This is a documented, deliberate choice over the
plan's other named option (a `dte <= 1` next-day proxy): that proxy needs a per-contract dte
bucket no persisted `gex_by_strike` filter isolates (only `ALL`/`ZERO_DTE`/`EX_ZERO_DTE` are
ever stored), so computing it would mean reopening Parquet -- exactly what this task's brief
and `app.api.scan._load_gex_inputs`'s own docstring say this endpoint must not do -- and the
plan's own conclusion is explicit that the honest state today is "this column is empty," not
"this column shows something adjacent to 0DTE labelled as if it were."

Verdict rules
-------------

Gated exactly like `app.gex.report.dealer_positioning`: below `POSITIONING_RATIO_FLOOR`
(imported from there, never re-derived -- CLAUDE.md's "don't duplicate the ratio floor")
the row is noise-dominated and carries no verdict, the same DIA precedent
(`docs/validation.md` section 9) that module already documents. First-cut rules, verbatim from
`plans/continuation/03-regime-board.md`'s "Design decisions":

* **fade**: positive net GEX, flip below spot by more than 1 ATR, the nearest wall in either
  direction within 1 ATR, 0DTE share above a documented floor **when the share is known** --
  see the "0DTE share" section above: on today's EOD-only data it almost never is, and the
  clause is dropped (not treated as failing) rather than making `fade` unreachable.
* **continuation**: negative net GEX, OR positive net GEX with spot within 0.5 ATR of the flip
  and the nearest wall in the direction of the last 5-day move more than 2 ATR away.
* **mixed**: everything else, with the reasons that pulled each way.

Every threshold above is a module-level constant with its own comment; every rule's trigger (or
near-miss) is appended to `RegimeRow.reasons` so a reader never has to re-derive why a verdict
landed where it did. The plan itself calls these "first-cut, to be refined once the agent has
seen a week of live rows" -- `ZERO_DTE_SHARE_FLOOR` in particular has no live measurement behind
it yet (T48 ships before a week of 16:45 extended-capture history exists) and is named here so a
future pass finds one constant to recalibrate, not logic to re-derive.

Staleness
---------

`app.api.scan` computes `chain_age_minutes` -- how many minutes before its trading day's close
the chain's `effective_data_time` (T34) instant sits, `0.0` for a chain that is honestly at (or
after) the close -- and `stale` (that age past `STALE_THRESHOLD_MINUTES`). This module
**suppresses the verdict** when `stale` is `True`, on top of (not instead of) carrying the raw
age on every row: see `RegimeRow.stale`/`chain_age_minutes` and `compute_regime_row`'s own
docstring "Staleness" section for why a chip alone was rejected as too easy to ignore, given the
T47 verified-facts finding this task was built to answer (a thin sector ETF's `is_eod` row can
be hours older than its `captured_at` suggests, e.g. XBI's 11:39 EOD row on a day the market
closed at 16:00).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.gex.engine import KeyLevels, StrikeGex
from app.gex.report import POSITIONING_RATIO_FLOOR, DealerPositioning, dealer_positioning

__all__ = [
    "CONTINUATION_FLIP_ATR",
    "CONTINUATION_WALL_ATR",
    "FADE_FLIP_ATR",
    "FADE_WALL_ATR",
    "ROOM_BEYOND_FRACTION",
    "STALE_THRESHOLD_MINUTES",
    "ZERO_DTE_SHARE_FLOOR",
    "RegimeRow",
    "WallInfo",
    "compute_regime_row",
    "zero_dte_share",
]


def _f(x: Any) -> float | None:
    """NaN/inf/None -> `None`, everything else a plain `float`. Same convention as
    `app.gex.engine._f` and `app.gex.report._f` -- each pure module keeps its own copy rather
    than importing a private helper across module boundaries.
    """
    if x is None:
        return None
    value = float(x)
    return value if math.isfinite(value) else None


# --------------------------------------------------------------------------------------
# Documented constants -- every threshold below is named so a future recalibration finds
# one constant, not logic to re-derive (see the module docstring's "Verdict rules" section).
# --------------------------------------------------------------------------------------

#: Plan's own number, verbatim: "flip below spot by more than 1 ATR" (fade).
FADE_FLIP_ATR = 1.0

#: Plan's own number, verbatim: "the nearest wall ... within 1 ATR" (fade).
FADE_WALL_ATR = 1.0

#: Plan's own number, verbatim: "spot within 0.5 ATR of the flip" (continuation).
CONTINUATION_FLIP_ATR = 0.5

#: Plan's own number, verbatim: "the nearest wall in the direction of the last 5-day move more
#: than 2 ATR away" (continuation).
CONTINUATION_WALL_ATR = 2.0

#: 0DTE share a chain must clear before this module lets it contribute to a *fade* verdict.
#: Not a plan-given number (the plan only says "above a documented floor") and not yet backed
#: by a week of live 16:45 extended-capture data (T47 shipped the same day as this task) --
#: chosen as "0DTE carries a clearly non-trivial minority of the book's gamma" rather than a
#: statistically fit threshold. Recalibrate here once live rows exist; see the module
#: docstring's "Verdict rules" section.
ZERO_DTE_SHARE_FLOOR = 0.15

#: "The next strike whose |GEX| exceeds 25% of the wall's" -- plan's own number, verbatim, for
#: `room beyond`.
ROOM_BEYOND_FRACTION = 0.25

#: How many minutes a chain's `effective_data_time` may sit before its trading day's close and
#: still be treated as "at the close" rather than stale. Not zero: even a genuinely fresh EOD
#: capture's `effective_data_time` lands at the close plus the feed's own entitlement delay
#: (15 minutes for Cboe) plus a few minutes of job runtime, all of which is normal and must not
#: itself trip the stale gate. 30 minutes is double that normal band -- generous enough that an
#: ordinary EOD row never trips it, tight enough that every symbol T47's verified-facts table
#: names (XBI 4h21m, XLC 2h27m, XLRE 2h25m) still trips it; GDX (27m) and KRE (25m) fall just
#: under it, which is the correct, honest answer for those two -- their chains really are only
#: mildly stale, not hours old, and 27/25 minutes of staleness is a materially different claim
#: from the other three's multi-hour gap.
STALE_THRESHOLD_MINUTES = 30.0


@dataclass(frozen=True, slots=True)
class WallInfo:
    """One wall (call or put) and everything a regime row needs about it: how far spot is,
    in three units, and how much "room" exists beyond it before the next strike with
    comparable gamma. See :data:`ROOM_BEYOND_FRACTION` and :func:`_room_beyond` for exactly
    what "room" means.

    `distance*` are always non-negative magnitudes -- which side of spot the wall sits on is
    already encoded by whether this object is `RegimeRow.wall_below` or `.wall_above`, so a
    signed distance here would just be redundant with the field name it is stored under.
    `distance_atr`/`room_beyond` are `None` when `atr14` was itself `None` (insufficient bars
    history) -- never a fabricated distance computed against a zero or missing ATR.
    """

    strike: float
    net_gex: float
    abs_gex: float
    distance: float
    distance_pct: float | None
    distance_atr: float | None
    room_beyond: float | None
    room_beyond_strike: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strike": _f(self.strike),
            "net_gex": _f(self.net_gex),
            "abs_gex": _f(self.abs_gex),
            "distance": _f(self.distance),
            "distance_pct": _f(self.distance_pct),
            "distance_atr": _f(self.distance_atr),
            "room_beyond": _f(self.room_beyond),
            "room_beyond_strike": _f(self.room_beyond_strike),
        }


@dataclass(frozen=True, slots=True)
class RegimeRow:
    """One symbol's full regime read: every input echoed back, plus the derived metrics and
    verdict. "Echoes every input" (the task's own requirement) means a consumer of this object
    never needs to keep the call's arguments around separately to know what chain, what spot,
    what ATR a given verdict was computed against -- the same "provenance travels with the
    result" discipline `app.gex.engine.SnapshotMeta` and `app.gex.report.ReportResult`
    already follow.

    `verdict` is `None` in exactly two cases, both spelled out in `reasons`: the chain is
    noise-dominated (`positioning.noise_dominated`, the same gate `app.gex.report
    .dealer_positioning` uses) or the chain is `stale` (see :data:`STALE_THRESHOLD_MINUTES`).
    Either alone is enough to suppress a verdict; both can apply to the same row at once.
    """

    underlying: str
    filter: str
    spot: float
    atr14: float | None
    iv30: float | None
    rv20: float | None
    iv_rv_ratio: float | None
    return_5d: float | None
    as_of: dt.datetime
    effective_at: dt.datetime
    chain_age_minutes: float
    stale: bool
    positioning: DealerPositioning
    flip_point: float | None
    flip_distance: float | None
    flip_distance_pct: float | None
    flip_distance_atr: float | None
    wall_below: WallInfo | None
    wall_above: WallInfo | None
    zero_dte_share: float | None
    verdict: str | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": str(self.underlying),
            "filter": str(self.filter),
            "spot": _f(self.spot),
            "atr14": _f(self.atr14),
            "iv30": _f(self.iv30),
            "rv20": _f(self.rv20),
            "iv_rv_ratio": _f(self.iv_rv_ratio),
            "return_5d": _f(self.return_5d),
            "as_of": self.as_of.isoformat(),
            "effective_at": self.effective_at.isoformat(),
            "chain_age_minutes": _f(self.chain_age_minutes),
            "stale": bool(self.stale),
            "positioning": self.positioning.to_dict(),
            "flip_point": _f(self.flip_point),
            "flip_distance": _f(self.flip_distance),
            "flip_distance_pct": _f(self.flip_distance_pct),
            "flip_distance_atr": _f(self.flip_distance_atr),
            "wall_below": None if self.wall_below is None else self.wall_below.to_dict(),
            "wall_above": None if self.wall_above is None else self.wall_above.to_dict(),
            "zero_dte_share": _f(self.zero_dte_share),
            "verdict": self.verdict,
            "reasons": list(self.reasons),
        }


def zero_dte_share(
    by_strike: Sequence[StrikeGex], zero_dte_by_strike: Sequence[StrikeGex]
) -> float | None:
    """Fraction of |GEX| that is 0DTE, restricted to the strikes the `ZERO_DTE` filter actually
    admitted -- see the module docstring's "0DTE share" section for why that restriction is the
    "at the same strike set" the plan asks for, and why an **empty** `zero_dte_by_strike` is
    `None`, never `0.0`.

    **Measured live 2026-09-09** (SPY snapshot id 38, 16:20 ET EOD capture): `zero_dte_by_strike`
    is empty on essentially every EOD-only snapshot, because that day's own 0DTE series has
    already left Cboe's payload by capture time (earliest expiry `dte=1`, 330 contracts counted
    `expired` in the engine's own diagnostics) -- not because 0DTE gamma is genuinely zero. An
    empty input therefore means "unobservable from this chain," and reporting `0.0` for that
    would misstate a data limitation as a fact about the market.

    Args:
        by_strike: `ALL`-filter per-strike rows for this snapshot.
        zero_dte_by_strike: `ZERO_DTE`-filter per-strike rows for the *same* snapshot.

    Returns:
        `None` when `zero_dte_by_strike` is empty (see above -- the ordinary case today), when
        none of its strikes are found in `by_strike` (should not happen for two filters computed
        from the same snapshot; `ALL` is always a superset), or when the matched `ALL`-side
        total is exactly zero. Otherwise the ratio, in `[0, 1]` under the ordinary case where
        0DTE contributes no more gamma at a strike than the full book does.
    """
    if not zero_dte_by_strike:
        return None

    all_by_strike = {row.strike: row for row in by_strike}
    matched = [row for row in zero_dte_by_strike if row.strike in all_by_strike]
    if not matched:
        return None

    zero_dte_abs = sum(row.abs_gex for row in matched)
    all_abs_at_matched = sum(all_by_strike[row.strike].abs_gex for row in matched)
    if all_abs_at_matched <= 0.0:
        return None
    return zero_dte_abs / all_abs_at_matched


def _room_beyond(
    sorted_by_strike: list[StrikeGex],
    wall_strike: float,
    wall_abs_gex: float,
    *,
    direction: int,
) -> tuple[float | None, float | None]:
    """First strike beyond `wall_strike` (in `direction`, +1 = toward higher strikes, -1 =
    toward lower) whose `abs_gex` exceeds `ROOM_BEYOND_FRACTION` of `wall_abs_gex`.

    Returns `(distance, strike)`, both `None` when no strike in `sorted_by_strike` clears the
    threshold -- a real "no wall beyond this one is big enough, within the strikes this chain
    actually has" answer, not a fabricated "unlimited room."
    """
    threshold = ROOM_BEYOND_FRACTION * wall_abs_gex
    candidates = (
        (row for row in sorted_by_strike if row.strike > wall_strike)
        if direction > 0
        else (row for row in reversed(sorted_by_strike) if row.strike < wall_strike)
    )
    for row in candidates:
        if row.abs_gex > threshold:
            return abs(row.strike - wall_strike), row.strike
    return None, None


def _wall_info(
    strike: float | None,
    net_gex: float | None,
    by_strike_map: dict[float, StrikeGex],
    sorted_by_strike: list[StrikeGex],
    spot: float,
    atr14: float | None,
    *,
    direction: int,
) -> WallInfo | None:
    if strike is None:
        return None

    row = by_strike_map.get(strike)
    # `abs_gex` should always be recoverable from the by-strike rows the wall itself came from
    # (the wall strike is, by construction, a strike that had admitted contracts) -- the
    # `net_gex`-derived fallback only fires if the two inputs somehow disagree (a caller error,
    # not an expected runtime state), and even then uses a real number rather than inventing 0.
    abs_gex = row.abs_gex if row is not None else abs(net_gex) if net_gex is not None else 0.0
    resolved_net_gex = net_gex if net_gex is not None else (row.net_gex if row is not None else 0.0)

    distance = abs(spot - strike)
    distance_pct = None if not spot else distance / spot * 100.0
    distance_atr = None if not atr14 else distance / atr14

    room_beyond, room_beyond_strike = _room_beyond(
        sorted_by_strike, strike, abs_gex, direction=direction
    )

    return WallInfo(
        strike=strike,
        net_gex=resolved_net_gex,
        abs_gex=abs_gex,
        distance=distance,
        distance_pct=distance_pct,
        distance_atr=distance_atr,
        room_beyond=room_beyond,
        room_beyond_strike=room_beyond_strike,
    )


def _nearest_walls(
    levels: KeyLevels, by_strike_map: dict[float, StrikeGex], spot: float
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Among `call_wall`/`put_wall` (whichever are not `None`), the nearest one strictly below
    spot and the nearest one strictly above -- see the module docstring: the two walls are not
    guaranteed to straddle spot (DIA's own 2026-09-05 capture has its two most negative strikes
    *above* spot, per `app.gex.report.LevelSet`'s docstring), so this picks by position, not by
    assuming `put_wall` is always "below" and `call_wall` always "above."

    Returns `((strike, net_gex), ...)` for below/above, `None` where no wall sits on that side.
    """
    candidates = [
        (levels.call_wall, levels.call_wall_gex),
        (levels.put_wall, levels.put_wall_gex),
    ]
    below = [(s, g) for s, g in candidates if s is not None and s < spot]
    above = [(s, g) for s, g in candidates if s is not None and s > spot]

    nearest_below = max(below, key=lambda pair: pair[0]) if below else None
    nearest_above = min(above, key=lambda pair: pair[0]) if above else None
    return nearest_below, nearest_above


def compute_regime_row(
    *,
    underlying: str,
    filter_: str,
    levels: KeyLevels,
    by_strike: Sequence[StrikeGex],
    zero_dte_by_strike: Sequence[StrikeGex],
    spot: float,
    atr14: float | None,
    iv30: float | None,
    rv20: float | None,
    return_5d: float | None,
    as_of: dt.datetime,
    effective_at: dt.datetime,
    chain_age_minutes: float,
    stale: bool,
    ratio_floor: float = POSITIONING_RATIO_FLOOR,
) -> RegimeRow:
    """Compute every regime metric and the verdict for one symbol's already-fetched inputs.

    Args:
        underlying: Echoed onto the result. Not validated against `app.models.chain.Underlying`
            here -- that is `app.api.scan`'s job, the same split `app.gex.report`'s own module
            keeps (this module trusts its caller's inputs, exactly like `compute_all` trusts a
            `ChainSnapshot` it is handed).
        filter_: The `ExpiryFilter` name `levels`/`by_strike` were computed under. Echoed, not
            interpreted -- 0DTE share always compares `zero_dte_by_strike` against `by_strike`
            regardless of what `filter_` names, since that comparison is a fixed diagnostic,
            not something the caller's chosen display filter should change.
        levels: The persisted `gex_levels` row's fields, as a `KeyLevels` (see module docstring
            for why this, not a full `GexResult`).
        by_strike: The persisted `gex_by_strike` rows for `(snapshot, filter_)`.
        zero_dte_by_strike: The persisted `gex_by_strike` rows for `(snapshot, ZERO_DTE)` --
            always the `ZERO_DTE` filter, independent of `filter_`.
        spot: The snapshot's spot. Passed separately from `levels.spot` (which should equal it)
            because `RegimeRow.spot` is a top-level echoed input, not a level the way
            `call_wall`/`put_wall` are.
        atr14: Daily-bar ATR(14), or `None` if unmeasurable (`app.scan.indicators.atr`'s own
            `NaN` translated to `None` at the caller's dataclass boundary).
        iv30: ATM ~30-day implied vol off the same snapshot (`app.gex.report.iv_regime`'s
            `atm_iv`), or `None`.
        rv20: Annualized realized vol(20) off daily bars, or `None`.
        return_5d: 5-trading-day close-to-close return, or `None`.
        as_of: The snapshot's own `captured_at` (tz-aware UTC).
        effective_at: `app.jobs.calendar.effective_data_time(captured_at, delayed_minutes)` --
            computed by the caller, since this module cannot import that calendar module (see
            the module docstring's opening paragraph).
        chain_age_minutes: Minutes `effective_at` sits before its trading day's close, `0.0`
            when at or after it. Also caller-computed, for the same reason.
        stale: `chain_age_minutes > STALE_THRESHOLD_MINUTES`. Passed in rather than recomputed
            here so the threshold constant and the gate it feeds stay in exactly one place
            (this module), while the caller still owns the clock-dependent "which trading day"
            arithmetic it must own anyway to produce `effective_at` at all.
        ratio_floor: Forwarded to `app.gex.report.dealer_positioning`. Defaults to
            `POSITIONING_RATIO_FLOOR`, imported (never redefined) per the task's own
            instruction.

    Returns:
        A frozen `RegimeRow`.
    """
    iv_rv_ratio = None
    if iv30 is not None and rv20 is not None and rv20 != 0.0:
        iv_rv_ratio = iv30 / rv20

    positioning = dealer_positioning(levels.net_gex, levels.abs_gex, ratio_floor=ratio_floor)

    flip_distance = flip_distance_pct = flip_distance_atr = None
    if levels.flip_point is not None:
        flip_distance = spot - levels.flip_point
        flip_distance_pct = None if not spot else flip_distance / spot * 100.0
        flip_distance_atr = None if not atr14 else flip_distance / atr14

    by_strike_map = {row.strike: row for row in by_strike}
    sorted_by_strike = sorted(by_strike, key=lambda row: row.strike)
    nearest_below, nearest_above = _nearest_walls(levels, by_strike_map, spot)

    wall_below = _wall_info(
        nearest_below[0] if nearest_below else None,
        nearest_below[1] if nearest_below else None,
        by_strike_map,
        sorted_by_strike,
        spot,
        atr14,
        direction=-1,
    )
    wall_above = _wall_info(
        nearest_above[0] if nearest_above else None,
        nearest_above[1] if nearest_above else None,
        by_strike_map,
        sorted_by_strike,
        spot,
        atr14,
        direction=1,
    )

    share = zero_dte_share(by_strike, zero_dte_by_strike)

    verdict, reasons = _verdict(
        positioning=positioning,
        flip_distance_atr=flip_distance_atr,
        wall_below=wall_below,
        wall_above=wall_above,
        zero_dte_share_value=share,
        return_5d=return_5d,
        stale=stale,
        chain_age_minutes=chain_age_minutes,
    )

    return RegimeRow(
        underlying=underlying,
        filter=str(filter_),
        spot=spot,
        atr14=atr14,
        iv30=iv30,
        rv20=rv20,
        iv_rv_ratio=iv_rv_ratio,
        return_5d=return_5d,
        as_of=as_of,
        effective_at=effective_at,
        chain_age_minutes=chain_age_minutes,
        stale=stale,
        positioning=positioning,
        flip_point=levels.flip_point,
        flip_distance=flip_distance,
        flip_distance_pct=flip_distance_pct,
        flip_distance_atr=flip_distance_atr,
        wall_below=wall_below,
        wall_above=wall_above,
        zero_dte_share=share,
        verdict=verdict,
        reasons=reasons,
    )


def _verdict(
    *,
    positioning: DealerPositioning,
    flip_distance_atr: float | None,
    wall_below: WallInfo | None,
    wall_above: WallInfo | None,
    zero_dte_share_value: float | None,
    return_5d: float | None,
    stale: bool,
    chain_age_minutes: float,
) -> tuple[str | None, tuple[str, ...]]:
    """The deterministic rules from the module docstring's "Verdict rules" section, as code.

    Every branch appends the reason it took (or the reasons it *didn't* take, on the `mixed`
    path) so `RegimeRow.reasons` always explains the verdict rather than just naming it.
    """
    reasons: list[str] = []

    if stale:
        reasons.append(
            f"chain is {chain_age_minutes:.0f} min stale relative to its trading day's close "
            f"(> {STALE_THRESHOLD_MINUTES:.0f} min threshold): verdict suppressed"
        )
        return None, tuple(reasons)

    if positioning.noise_dominated:
        reasons.append(positioning.description)
        return None, tuple(reasons)

    nearest_wall_atr = min(
        (w.distance_atr for w in (wall_below, wall_above) if w is not None and w.distance_atr is not None),
        default=None,
    )

    if positioning.direction == "SHORT":
        reasons.append(
            f"dealers are short gamma ({positioning.ratio:.1%} of gross): hedging chases the "
            "move -- continuation"
        )
        return "continuation", tuple(reasons)

    # LONG gamma from here on (positioning.direction == "LONG").
    flip_below_by_more_than_1atr = flip_distance_atr is not None and flip_distance_atr > FADE_FLIP_ATR
    wall_near = nearest_wall_atr is not None and nearest_wall_atr <= FADE_WALL_ATR

    # 0DTE share is `None` on essentially every EOD-only snapshot today (see the module
    # docstring's "0DTE share" section, and `zero_dte_share`'s own docstring, for the live
    # 2026-09-09 measurement behind this) -- an unmeasurable input must not count as a *failing*
    # one, or `fade` would become unreachable the day this ships. `zero_dte_gate_cleared` is
    # `True` both when the share is unknown (clause dropped) and when it is known and clears the
    # floor; `zero_dte_reason` always names which case applied, so a `fade` verdict never lets a
    # reader assume 0DTE evidence stood behind it when none was available.
    zero_dte_known = zero_dte_share_value is not None
    zero_dte_high = zero_dte_known and zero_dte_share_value > ZERO_DTE_SHARE_FLOOR
    zero_dte_gate_cleared = (not zero_dte_known) or zero_dte_high
    if zero_dte_known:
        zero_dte_reason = (
            f"0DTE share {zero_dte_share_value:.1%} above the {ZERO_DTE_SHARE_FLOOR:.0%} floor"
        )
    else:
        zero_dte_reason = (
            "0DTE share unavailable on this snapshot (same-day expiry already left the "
            "payload by capture time -- see app.scan.regime module docstring); fade evaluated "
            "on flip/wall distance alone, 0DTE clause dropped"
        )

    if flip_below_by_more_than_1atr and wall_near and zero_dte_gate_cleared:
        reasons.append(
            f"long gamma ({positioning.ratio:.1%} of gross) with flip {flip_distance_atr:.2f} "
            f"ATR below spot"
        )
        reasons.append(f"nearest wall only {nearest_wall_atr:.2f} ATR away")
        reasons.append(zero_dte_reason)
        return "fade", tuple(reasons)

    near_flip = flip_distance_atr is not None and abs(flip_distance_atr) <= CONTINUATION_FLIP_ATR
    directional_wall: WallInfo | None = None
    if return_5d is not None and return_5d > 0.0:
        directional_wall = wall_above
    elif return_5d is not None and return_5d < 0.0:
        directional_wall = wall_below
    wall_far = (
        directional_wall is not None
        and directional_wall.distance_atr is not None
        and directional_wall.distance_atr > CONTINUATION_WALL_ATR
    )

    if near_flip and wall_far and return_5d is not None:
        direction_word = "up" if return_5d > 0.0 else "down"
        reasons.append(
            f"long gamma but spot is only {abs(flip_distance_atr):.2f} ATR from the flip"
        )
        reasons.append(
            f"the wall in the direction of the last 5-day move ({direction_word}) is "
            f"{directional_wall.distance_atr:.2f} ATR away, past the "
            f"{CONTINUATION_WALL_ATR:.1f} ATR floor"
        )
        return "continuation", tuple(reasons)

    # mixed: record exactly which of the fade/continuation conditions held and which did not,
    # so a mixed row is never a bare label with no explanation.
    reasons.append(
        f"long gamma ({positioning.ratio:.1%} of gross)" if positioning.ratio is not None
        else "long gamma"
    )
    reasons.append(
        f"flip is {flip_distance_atr:.2f} ATR from spot (fade needs > {FADE_FLIP_ATR:.1f} below)"
        if flip_distance_atr is not None
        else "flip distance unavailable (no flip point or no ATR)"
    )
    reasons.append(
        f"nearest wall is {nearest_wall_atr:.2f} ATR away (fade needs <= {FADE_WALL_ATR:.1f})"
        if nearest_wall_atr is not None
        else "no wall distance available"
    )
    if not zero_dte_known:
        reasons.append(zero_dte_reason)  # "unavailable ... clause dropped" -- same wording fade uses
    elif zero_dte_high:
        reasons.append(
            f"0DTE share {zero_dte_share_value:.1%} is above the {ZERO_DTE_SHARE_FLOOR:.0%} "
            "floor, but the flip/wall condition(s) above did not clear, so fade was not "
            "reached on this alone"
        )
    else:
        reasons.append(
            f"0DTE share is {zero_dte_share_value:.1%} (fade needs > {ZERO_DTE_SHARE_FLOOR:.0%})"
        )
    reasons.append(
        "did not clear the continuation condition (spot within "
        f"{CONTINUATION_FLIP_ATR:.1f} ATR of the flip and the wall in the 5-day move's "
        f"direction more than {CONTINUATION_WALL_ATR:.1f} ATR away)"
    )
    return "mixed", tuple(reasons)
