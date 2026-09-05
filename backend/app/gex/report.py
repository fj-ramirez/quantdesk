"""Report analytics (T39) — the derived figures a *written* report needs, on top of the
per-strike GEX aggregates :mod:`app.gex.engine` already produces.

**This module is pure**, on exactly the same contract as :mod:`app.gex.engine` and
:mod:`app.gex.greeks`: no HTTP, no database, no filesystem, no logging, and deterministic
given its inputs. That is what lets the read API (`app.api.report`), a future capture-time
pre-computation, and an offline backtest all share one implementation. The clock is never
read here either — every "as of" instant comes from the :class:`~app.gex.engine.GexResult`
that was handed in, so re-running an old snapshot reproduces its report byte for byte.

Two inputs, mirroring ``compute_all``'s own split:

* a :class:`~app.gex.engine.GexResult` — spot, walls, flip point, top strikes, net/abs GEX;
* the ``to_frame`` DataFrame — per-contract ``open_interest``, ``volume``, ``iv``, ``bid``,
  ``ask``, ``dte``, which the aggregates have already summed away.

``filters`` re-applies :func:`~app.gex.engine.expiry_mask` to the frame so that every figure
here describes **the same contract population** as the ``GexResult`` beside it. Pass the same
``filters`` value that produced the result; passing a different one silently produces a report
whose two halves disagree, which is the one way to misuse this function.

Honesty rules this module exists to enforce
-------------------------------------------

The user supplied an example report (``context/example-report.md``) as a *layout* reference.
Its numbers were reconciled against the very GLD chain it claims to describe and do not
survive contact with the data — call OI off by 40x, put OI by 7x, the put/call ratio
**inverted** (it reports 2.54 puts per call and derives a bearish reading; GLD actually
carries 2.2 calls per put), max pain 410 against our 400, IV 17.9 % against our 22.9–32 %.
It is also internally inconsistent, listing $407 and $406 as *support* while listing $405 as
*resistance*. Every number this module emits is computed from the chain in front of it.

Three specific places where the honest answer is "we cannot say", and this module returns
``None`` rather than inventing a value — the same posture as T34's staleness badge and T37's
empty state:

1. **The IV regime label.** An ATM 30-day implied vol is computable from one snapshot; the
   words LOW / NORMAL / HIGH are not, because they are a statement about *this* vol relative
   to its own history and a single snapshot has no history. :class:`IvRegime` therefore always
   carries the number and carries the label only when at least :data:`MIN_IV_HISTORY`
   prior observations are supplied. There is nowhere in the schema that stores them today
   (see :func:`build_report`'s ``iv_history`` argument), so in practice the label is ``None``
   and the UI renders "insufficient history".
2. **The dealer-positioning direction on a noise-dominated chain.** See
   :data:`POSITIONING_RATIO_FLOOR`.
3. **Any playbook target or stop that is not itself a computed level.** The example report
   fills these with "+2 % move" and "Below $403", neither of which comes from anywhere.
   :class:`PlaybookEntry` leaves them ``None`` when no computed level sits there.

Trade suggestions
-----------------

:class:`PremiumSelling` and :class:`Playbook` exist because the user explicitly asked for them
on 2026-09-05, and PLAN.md §2's scope line was amended the same day to record that the app now
emits trade *suggestions* while still never routing an order. They are a **screen over the
current chain** — "these contracts sit beyond the computed walls in this DTE window", "this
computed level is the one a breakout would cross" — not recommendations, and every renderer of
them (:func:`render_text` here, the `/report` page in T40) labels them that way.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.gex.engine import (
    DEFAULT_IV_POLICY,
    ExpiryFilter,
    GexResult,
    IvPolicy,
    IvPolicyMode,
    SnapshotMeta,
    expiry_mask,
)

__all__ = [
    "ATM_MONEYNESS_WINDOW",
    "DEFAULT_PREMIUM_DTE_MAX",
    "DEFAULT_PREMIUM_DTE_MIN",
    "DEFAULT_TARGET_DTE",
    "FLIP_PROXIMITY_PCT",
    "MIN_IV_HISTORY",
    "POSITIONING_RATIO_FLOOR",
    "DealerPositioning",
    "IvRegime",
    "LevelSet",
    "MaxPain",
    "Playbook",
    "PlaybookEntry",
    "PremiumCandidate",
    "PremiumSelling",
    "PutCallRatios",
    "ReportLevel",
    "ReportResult",
    "RiskAlert",
    "build_report",
    "render_text",
]


# --------------------------------------------------------------------------------------
# Documented constants
# --------------------------------------------------------------------------------------

#: Constant-maturity point the IV regime is quoted at, in calendar days. 30 is the market
#: convention (it is what VIX and every "30-day IV" quote mean), which is the only reason to
#: prefer it over any other tenor.
DEFAULT_TARGET_DTE = 30

#: How far from spot a contract may sit and still count as "at the money" for the IV
#: calculation, as a fraction of spot. 5 % is wide enough that every symbol we track has
#: several strikes inside it at every expiry (GLD's ±20 points spans ~8 strikes) and narrow
#: enough that the skew has not yet bent the number away from the ATM vol.
ATM_MONEYNESS_WINDOW = 0.05

#: Prior observations of the *same symbol's* ATM IV required before this module will attach a
#: LOW / NORMAL / HIGH word to the current one. 20 is a floor, not a sufficiency claim: below
#: it a percentile is not a statistic, it is a rounding of a handful of points. Nothing in the
#: schema persists ATM IV today, so `build_report` is normally called with no history at all
#: and the label is `None` — see the module docstring.
MIN_IV_HISTORY = 20

#: ``|net_gex| / abs_gex`` a chain must clear before this module will report a *direction*
#: ("dealers are long/short gamma") rather than the word "noise-dominated".
#:
#: Derived, not chosen. ``docs/validation.md`` §9 measures what a *plausible* parameter error
#: does to net GEX: the global ``DIVIDEND_YIELD = 0.013`` is wrong for both ETFs, and moving it
#: to ``q = 0`` shifts net GEX by 0.057 B on GLD's 5.303 B gross (1.1 % of gross) and by
#: 0.017 B on DIA's 1.037 B gross (1.6 % of gross) — **flipping DIA's sign outright**. A net
#: that is smaller than the shift a known-wrong parameter can produce is not a reading. The
#: floor is set at ~2x the largest measured shift, 3 %, so the sign has to survive that stress
#: with margin before it is called a direction.
#:
#: Measured on live chains 2026-09-05: SPY 4.8 %, GLD 42.7 %, **DIA 0.9 %**. DIA lands a
#: factor of 3.7 below the floor and is reported as noise-dominated, which is precisely what
#: §9 says we are entitled to claim about it until T33 fits the carry per expiry from
#: put-call parity. Note this is a floor on the *direction*, not on the walls: §9 also
#: establishes that per-strike walls are unaffected, so they stay usable either way.
POSITIONING_RATIO_FLOOR = 0.03

#: DTE window the premium-selling screen looks in. 7 excludes the gamma-convexity end of the
#: curve where a screen on static OI is least meaningful; 45 is the far end of the usual
#: monthly premium-selling tenor.
DEFAULT_PREMIUM_DTE_MIN = 7
DEFAULT_PREMIUM_DTE_MAX = 45

#: How many candidates the premium screen returns per side.
DEFAULT_PREMIUM_TOP_N = 5

#: How close spot must be to the gamma flip, as a fraction of spot, before the report raises
#: a proximity alert. A display threshold for when to *mention* a computed level — it makes
#: no claim about the market, and no number downstream depends on it.
FLIP_PROXIMITY_PCT = 0.01


def _f(x: Any) -> float | None:
    """NumPy/None → JSON-safe float; NaN and infinities become ``None``, never a fake 0.

    Deliberately mirrors ``app.gex.engine._f`` rather than importing it: that one is private
    to the engine, and a report field reading ``0.0`` where the honest answer is "no value"
    is the exact failure this whole module is written to avoid.
    """
    if x is None:
        return None
    value = float(x)
    return value if math.isfinite(value) else None


def _i(x: Any) -> int:
    """NaN-tolerant int for a count that is genuinely zero when absent."""
    if x is None:
        return 0
    value = float(x)
    return int(value) if math.isfinite(value) else 0


# --------------------------------------------------------------------------------------
# Result records — frozen slotted dataclasses, matching the engine's house style
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaxPain:
    """The strike minimising the total intrinsic value of every open contract at expiry.

    The classic "max pain" reading: if every contract in scope settled at ``strike``, option
    holders collectively receive the least money. It is a statement about where open interest
    is concentrated, not a forecast, and this module never presents it as one.

    Population: contracts the expiry filter admitted that are **not expired** and whose open
    interest is *known*. Note this is deliberately a wider population than the GEX aggregates
    beside it, which additionally require a usable implied vol — max pain needs no vol, and
    dropping a contract for a missing IV it does not use would understate real open interest.
    ``contracts`` records exactly how many rows went in so the two populations can be compared.

    ``total_pain`` is in dollars: ``Σ open_interest × multiplier × intrinsic(strike)``. The
    multiplier is read per contract rather than assumed to be 100, matching the engine's rule,
    so an adjusted contract cannot silently skew the minimum.
    """

    strike: float | None
    distance: float | None
    distance_pct: float | None
    total_pain: float | None
    strikes_evaluated: int
    contracts: int
    open_interest: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "strike": _f(self.strike),
            "distance": _f(self.distance),
            "distance_pct": _f(self.distance_pct),
            "total_pain": _f(self.total_pain),
            "strikes_evaluated": int(self.strikes_evaluated),
            "contracts": int(self.contracts),
            "open_interest": int(self.open_interest),
        }


@dataclass(frozen=True, slots=True)
class PutCallRatios:
    """Put/call ratios and the raw totals behind them, split by right.

    Both ratios are **puts ÷ calls**, the standard orientation, and both are ``None`` rather
    than ``inf`` when the call side is zero. The example report inverted this ratio on GLD and
    derived a bearish reading from the inversion; the totals are carried alongside so the
    ratio can always be checked against its own numerator and denominator.

    ``missing_*`` count contracts the vendor reported as unknown (``None`` → ``NaN``), which
    are excluded from the sums. Per the codebase-wide rule, an open interest of ``0`` is
    genuine and is *included*, contributing zero — it is never collapsed into "unknown".
    """

    call_open_interest: int
    put_open_interest: int
    total_open_interest: int
    open_interest_ratio: float | None
    call_volume: int
    put_volume: int
    total_volume: int
    volume_ratio: float | None
    call_contracts: int
    put_contracts: int
    missing_open_interest: int
    missing_volume: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_open_interest": int(self.call_open_interest),
            "put_open_interest": int(self.put_open_interest),
            "total_open_interest": int(self.total_open_interest),
            "open_interest_ratio": _f(self.open_interest_ratio),
            "call_volume": int(self.call_volume),
            "put_volume": int(self.put_volume),
            "total_volume": int(self.total_volume),
            "volume_ratio": _f(self.volume_ratio),
            "call_contracts": int(self.call_contracts),
            "put_contracts": int(self.put_contracts),
            "missing_open_interest": int(self.missing_open_interest),
            "missing_volume": int(self.missing_volume),
        }


@dataclass(frozen=True, slots=True)
class IvRegime:
    """ATM implied vol at a constant ~30-day maturity, and a regime label only if earned.

    ``atm_iv`` is a decimal fraction (0.229 = 22.9 %), interpolated in **total variance**
    (``σ²·T`` linear in ``T``) between the two expiries bracketing :data:`DEFAULT_TARGET_DTE`
    — the standard constant-maturity construction, and the reason ``T`` rather than ``dte``
    drives it. Within each expiry the ATM vol is itself interpolated across strike to spot
    over the contracts inside :data:`ATM_MONEYNESS_WINDOW`.

    ``label`` is ``None`` unless ``history_observations >= min_history_required``. There is no
    honest way to call one number LOW or HIGH without a distribution to place it in, and the
    example report's "NORMAL VOLATILITY (17.9 %)" is a label attached to a wrong number with
    no history behind it at all. When ``label`` is ``None`` the renderer says
    "insufficient history", not "normal".

    ``interpolated`` is ``False`` when only one usable expiry existed, or when the 30-day
    point lies outside the range of available expiries — in which case ``lower_dte`` and
    ``upper_dte`` are equal and name the single expiry actually used. Extrapolating past the
    end of the term structure would be inventing a vol.
    """

    atm_iv: float | None
    target_dte: int
    lower_dte: int | None
    upper_dte: int | None
    interpolated: bool
    contracts: int
    label: str | None
    history_observations: int
    min_history_required: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "atm_iv": _f(self.atm_iv),
            "target_dte": int(self.target_dte),
            "lower_dte": None if self.lower_dte is None else int(self.lower_dte),
            "upper_dte": None if self.upper_dte is None else int(self.upper_dte),
            "interpolated": bool(self.interpolated),
            "contracts": int(self.contracts),
            "label": self.label,
            "history_observations": int(self.history_observations),
            "min_history_required": int(self.min_history_required),
        }


@dataclass(frozen=True, slots=True)
class DealerPositioning:
    """Direction of dealer gamma, gated on how large the net is relative to the gross.

    ``ratio`` is ``|net_gex| / abs_gex``. Below :data:`POSITIONING_RATIO_FLOOR` the chain's
    net is smaller than the shift a known-wrong carry parameter can produce
    (``docs/validation.md`` §9), so ``label`` reads ``NOISE-DOMINATED`` and ``direction`` is
    ``None`` — the report declines to say which way dealers are positioned rather than
    asserting something the validation document says we cannot support.

    ``direction`` is ``"LONG"`` (positive net GEX — hedging dampens moves) or ``"SHORT"``
    (negative — hedging amplifies them), and only ever set when the floor is cleared.
    """

    net_gex: float
    abs_gex: float
    ratio: float | None
    ratio_floor: float
    noise_dominated: bool
    direction: str | None
    label: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "net_gex": _f(self.net_gex),
            "abs_gex": _f(self.abs_gex),
            "ratio": _f(self.ratio),
            "ratio_floor": _f(self.ratio_floor),
            "noise_dominated": bool(self.noise_dominated),
            "direction": self.direction,
            "label": self.label,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class ReportLevel:
    """One support or resistance strike, with its distance from spot.

    ``side`` is ``"RESISTANCE"`` or ``"SUPPORT"``. ``above_spot`` is the raw geometric fact
    and is carried separately on purpose: a strike can carry negative (put-side) net GEX while
    sitting *above* spot, and conflating "which side of spot" with "which kind of level" is
    how the example report ended up listing $407 as support and $405 as resistance in the same
    breath. See :class:`LevelSet`.
    """

    strike: float
    net_gex: float
    abs_gex: float
    open_interest: int
    distance: float | None
    distance_pct: float | None
    side: str
    above_spot: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "strike": _f(self.strike),
            "net_gex": _f(self.net_gex),
            "abs_gex": _f(self.abs_gex),
            "open_interest": int(self.open_interest),
            "distance": _f(self.distance),
            "distance_pct": _f(self.distance_pct),
            "side": self.side,
            "above_spot": bool(self.above_spot),
        }


@dataclass(frozen=True, slots=True)
class LevelSet:
    """Resistance above spot, support below it, and whatever straddles the line.

    Construction, in one sentence: a strike with **positive** net GEX **above** spot is
    resistance, a strike with **negative** net GEX **below** spot is support, and anything
    else — positive net below spot, negative net above it — is neither and goes into
    ``straddling``.

    That third bucket is the point of this class. It is a real market condition (gamma
    concentrated on both sides of spot; DIA on the 2026-09-05 capture carries its two most
    negative strikes, 533 and 532, *above* a 532.34 spot) and T39 requires it be **labelled,
    not silently reordered**. The example report reordered: it printed 407 and 406 as support
    while printing 405 as resistance, with spot at 406.77, so its "support" sat above its
    "resistance" and the reader had no way to see why. Here ``overlapping`` says so out loud
    and ``overlap_note`` explains it, while ``resistance`` and ``support`` remain sorted such
    that every resistance strike is strictly above every support strike, always.

    Both lists are sorted by proximity to spot — the nearest level first, which is the one a
    move actually meets — not by GEX magnitude.
    """

    resistance: tuple[ReportLevel, ...]
    support: tuple[ReportLevel, ...]
    straddling: tuple[ReportLevel, ...]
    overlapping: bool
    overlap_note: str | None
    call_wall: float | None
    put_wall: float | None
    flip_point: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resistance": [level.to_dict() for level in self.resistance],
            "support": [level.to_dict() for level in self.support],
            "straddling": [level.to_dict() for level in self.straddling],
            "overlapping": bool(self.overlapping),
            "overlap_note": self.overlap_note,
            "call_wall": _f(self.call_wall),
            "put_wall": _f(self.put_wall),
            "flip_point": _f(self.flip_point),
        }


@dataclass(frozen=True, slots=True)
class PremiumCandidate:
    """One contract from the premium-selling screen. A screen result, never a recommendation.

    ``mid`` is ``(bid + ask) / 2`` and is ``None`` unless **both** sides are quoted — a
    one-sided market has no mid, and inventing one from the bid alone would overstate what a
    seller could realise. A contract with no bid at all is not screened in: it cannot be sold.
    """

    occ_symbol: str
    strike: float
    right: str
    expiry: dt.date
    dte: int
    bid: float | None
    ask: float | None
    mid: float | None
    iv: float | None
    open_interest: int
    distance_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "occ_symbol": self.occ_symbol,
            "strike": _f(self.strike),
            "right": self.right,
            "expiry": self.expiry.isoformat(),
            "dte": int(self.dte),
            "bid": _f(self.bid),
            "ask": _f(self.ask),
            "mid": _f(self.mid),
            "iv": _f(self.iv),
            "open_interest": int(self.open_interest),
            "distance_pct": _f(self.distance_pct),
        }


@dataclass(frozen=True, slots=True)
class PremiumSelling:
    """OTM contracts beyond each wall inside a DTE window — screening output only.

    The boundaries are the computed walls themselves: calls at or above ``call_boundary``
    (the call wall), puts at or below ``put_boundary`` (the put wall). When a wall sits far
    from spot the corresponding side legitimately returns **nothing** — GLD's put wall on the
    2026-09-05 capture is 335 against a 406.77 spot, 18 % away, where no put carries a
    sellable bid. An empty side is the honest answer to "what is beyond the put wall", and
    ``note`` records why it is empty rather than the screen quietly widening its own boundary
    until it found something.
    """

    calls: tuple[PremiumCandidate, ...]
    puts: tuple[PremiumCandidate, ...]
    dte_min: int
    dte_max: int
    call_boundary: float | None
    put_boundary: float | None
    note: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": [c.to_dict() for c in self.calls],
            "puts": [p.to_dict() for p in self.puts],
            "dte_min": int(self.dte_min),
            "dte_max": int(self.dte_max),
            "call_boundary": _f(self.call_boundary),
            "put_boundary": _f(self.put_boundary),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class PlaybookEntry:
    """One deterministic scenario, every number of which is a level computed elsewhere.

    ``trigger`` / ``target`` / ``invalidation`` are strikes or profile levels this report
    already computed, or ``None``. Nothing here is derived from a percentage of spot, a
    volatility estimate, or a rule of thumb: where no computed level sits, the field is
    ``None`` and the renderer prints "—". The example report filled the same three fields with
    "+2 % move" and "Below $403", numbers that appear nowhere in its own analysis.

    ``strategy`` is the only free text, and it names structures rather than prices.
    """

    key: str
    name: str
    trigger: float | None
    trigger_label: str
    target: float | None
    target_label: str
    invalidation: float | None
    invalidation_label: str
    strategy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "trigger": _f(self.trigger),
            "trigger_label": self.trigger_label,
            "target": _f(self.target),
            "target_label": self.target_label,
            "invalidation": _f(self.invalidation),
            "invalidation_label": self.invalidation_label,
            "strategy": self.strategy,
        }


@dataclass(frozen=True, slots=True)
class Playbook:
    """The scenario set, plus the range the two walls bracket.

    ``range_low`` / ``range_high`` are the put and call walls, and are only populated when
    spot actually sits **between** them. A "range" that does not contain the current price is
    not a range, and reporting one would be the same class of error as the example report's
    inverted support and resistance.
    """

    entries: tuple[PlaybookEntry, ...]
    range_low: float | None
    range_high: float | None
    range_magnet: float | None
    spot_in_range: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "range_low": _f(self.range_low),
            "range_high": _f(self.range_high),
            "range_magnet": _f(self.range_magnet),
            "spot_in_range": bool(self.spot_in_range),
        }


@dataclass(frozen=True, slots=True)
class RiskAlert:
    """One deterministic alert. ``code`` is stable for the UI; ``message`` is for a human.

    ``severity`` is ``"INFO"`` or ``"WARNING"`` and describes how much the alert should
    interrupt the reader, not how risky the market is — this module makes no risk forecast.
    """

    code: str
    severity: str
    message: str
    level: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "level": _f(self.level),
        }


@dataclass(frozen=True, slots=True)
class ReportResult:
    """Everything :func:`build_report` produces for one snapshot under one expiry filter.

    ``to_dict()`` is plain JSON — floats, ints, strings, ISO dates and ``None`` for anything
    genuinely absent — on the same terms as :meth:`app.gex.engine.GexResult.to_dict`, so the
    API layer is a pass-through rather than a translation.

    ``generated_at`` is the snapshot's ``captured_at``, **not** the wall clock: this module
    reads no clock, and a report regenerated from an old snapshot must reproduce exactly.
    """

    underlying: str
    filter: str
    spot: float
    generated_at: dt.datetime
    snapshot: SnapshotMeta
    max_pain: MaxPain
    ratios: PutCallRatios
    iv_regime: IvRegime
    positioning: DealerPositioning
    levels: LevelSet
    premium: PremiumSelling
    playbook: Playbook
    alerts: tuple[RiskAlert, ...] = field(default=())
    summary: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": str(self.underlying),
            "filter": str(self.filter),
            "spot": _f(self.spot),
            "generated_at": self.generated_at.isoformat(),
            "snapshot": self.snapshot.to_dict(),
            "max_pain": self.max_pain.to_dict(),
            "ratios": self.ratios.to_dict(),
            "iv_regime": self.iv_regime.to_dict(),
            "positioning": self.positioning.to_dict(),
            "levels": self.levels.to_dict(),
            "premium": self.premium.to_dict(),
            "playbook": self.playbook.to_dict(),
            "alerts": [a.to_dict() for a in self.alerts],
            "summary": list(self.summary),
        }


# --------------------------------------------------------------------------------------
# Population helpers
# --------------------------------------------------------------------------------------


def _open_mask(df: pd.DataFrame) -> np.ndarray:
    """Contracts that are live and whose open interest is *known*.

    Wider than ``engine.include_mask`` by one condition: no implied vol is required. Max pain
    and the put/call ratios are pure open-interest arithmetic and never touch a vol, so
    excluding a contract because the vendor omitted its IV would drop real open interest from
    a figure that does not depend on it. The GEX aggregates in the same report *do* require a
    vol, which is why the two populations can differ and why both counts are reported.
    """
    if df.empty:
        return np.zeros(0, dtype=bool)
    return ~df["expired"].to_numpy(dtype=bool) & ~np.isnan(
        df["open_interest"].to_numpy(dtype=float)
    )


def _usable_iv(df: pd.DataFrame, policy: IvPolicy) -> np.ndarray:
    """Rows with an implied vol this report is willing to quote.

    Applies the engine's own band under every mode except ``KEEP``, including ``CLAMP``:
    clamping is a defensible thing to do to a *gamma* input, but quoting a winsorized 300 %
    as "the ATM implied vol" would be reporting the policy's bound as if it were a market
    observation. The ATM window keeps this near-academic in practice — an ATM 30-day contract
    is essentially never outside the band — but the rule is stated rather than assumed.
    """
    if df.empty:
        return np.zeros(0, dtype=bool)
    iv = df["iv"].to_numpy(dtype=float)
    ok = ~np.isnan(iv) & ~df["expired"].to_numpy(dtype=bool)
    if policy.mode is not IvPolicyMode.KEEP:
        ok &= (iv >= policy.iv_min) & (iv <= policy.iv_max)
    return ok


# --------------------------------------------------------------------------------------
# Individual figures
# --------------------------------------------------------------------------------------


def max_pain(df: pd.DataFrame, spot: float) -> MaxPain:
    """Strike minimising ``Σ open_interest × multiplier × intrinsic`` across ``df``.

    Candidate settlement prices are the distinct strikes present in ``df`` — the only prices
    at which the piecewise-linear total-intrinsic curve can have its minimum, so evaluating
    the grid finely would find nothing a strike does not. Ties break to the lower strike
    (``argmin``'s own behaviour), matching the engine's tie rule for walls.

    Args:
        df: Rows already narrowed to the expiry scope in play.
        spot: Snapshot spot, used only to report the distance.
    """
    rows = df.loc[_open_mask(df)]
    if rows.empty:
        return MaxPain(
            strike=None,
            distance=None,
            distance_pct=None,
            total_pain=None,
            strikes_evaluated=0,
            contracts=0,
            open_interest=0,
        )

    strike = rows["strike"].to_numpy(dtype=float)
    is_call = rows["right"].to_numpy(dtype=object) == "C"
    notional = rows["open_interest"].to_numpy(dtype=float) * rows["multiplier"].to_numpy(
        dtype=float
    )

    candidates = np.unique(strike)
    # (candidates, contracts) intrinsic matrix. The chains here are a few thousand rows over a
    # few hundred strikes, so the dense form is ~1e6 floats at worst -- cheaper in both time
    # and code than looping, and this module is called once per request.
    settle = candidates[:, None]
    intrinsic = np.where(
        is_call[None, :],
        np.maximum(settle - strike[None, :], 0.0),
        np.maximum(strike[None, :] - settle, 0.0),
    )
    pain = intrinsic @ notional

    best = int(np.argmin(pain))
    level = float(candidates[best])
    return MaxPain(
        strike=level,
        distance=level - spot,
        distance_pct=None if not spot else (level - spot) / spot * 100.0,
        total_pain=float(pain[best]),
        strikes_evaluated=int(candidates.size),
        contracts=len(rows),
        open_interest=_i(np.nansum(rows["open_interest"].to_numpy(dtype=float))),
    )


def put_call_ratios(df: pd.DataFrame) -> PutCallRatios:
    """Put/call ratios on open interest and volume, plus the totals behind them.

    Open interest and volume are counted over *separate* populations on purpose: a contract
    with known OI and unknown volume contributes to the OI totals and not the volume ones,
    rather than being dropped from both. ``missing_open_interest`` / ``missing_volume`` say
    how many rows each exclusion cost.
    """
    live = df.loc[~df["expired"].to_numpy(dtype=bool)] if not df.empty else df
    if live.empty:
        return PutCallRatios(
            call_open_interest=0,
            put_open_interest=0,
            total_open_interest=0,
            open_interest_ratio=None,
            call_volume=0,
            put_volume=0,
            total_volume=0,
            volume_ratio=None,
            call_contracts=0,
            put_contracts=0,
            missing_open_interest=0,
            missing_volume=0,
        )

    is_call = live["right"].to_numpy(dtype=object) == "C"
    oi = live["open_interest"].to_numpy(dtype=float)
    vol = live["volume"].to_numpy(dtype=float)

    call_oi = _i(np.nansum(oi[is_call]))
    put_oi = _i(np.nansum(oi[~is_call]))
    call_vol = _i(np.nansum(vol[is_call]))
    put_vol = _i(np.nansum(vol[~is_call]))

    return PutCallRatios(
        call_open_interest=call_oi,
        put_open_interest=put_oi,
        total_open_interest=call_oi + put_oi,
        # `None`, never `inf`: a zero call side means the ratio is undefined, and `inf` would
        # render as a number and sort as one.
        open_interest_ratio=(put_oi / call_oi) if call_oi else None,
        call_volume=call_vol,
        put_volume=put_vol,
        total_volume=call_vol + put_vol,
        volume_ratio=(put_vol / call_vol) if call_vol else None,
        call_contracts=int(is_call.sum()),
        put_contracts=int((~is_call).sum()),
        missing_open_interest=int(np.isnan(oi).sum()),
        missing_volume=int(np.isnan(vol).sum()),
    )


def _expiry_atm_iv(rows: pd.DataFrame, spot: float) -> float | None:
    """ATM implied vol for one expiry, interpolated across strike to ``spot``.

    Calls and puts at the same strike are averaged before interpolating: they are two quotes
    of the same vol and averaging halves the quote noise. ``np.interp`` clamps outside the
    strike range rather than extrapolating, which is what we want — the nearest in-window
    strike's vol is a defensible stand-in, an extrapolated one is not.
    """
    if rows.empty:
        return None
    per_strike = rows.groupby("strike", sort=True)["iv"].mean()
    strikes = per_strike.index.to_numpy(dtype=float)
    ivs = per_strike.to_numpy(dtype=float)
    if strikes.size == 1:
        return float(ivs[0])
    return float(np.interp(spot, strikes, ivs))


def iv_regime(
    df: pd.DataFrame,
    spot: float,
    *,
    target_dte: int = DEFAULT_TARGET_DTE,
    moneyness: float = ATM_MONEYNESS_WINDOW,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    iv_history: Sequence[float] | None = None,
) -> IvRegime:
    """Constant-maturity ATM implied vol, and a regime label only when history earns one.

    Two interpolations, in this order:

    1. **Across strike, within each expiry** — :func:`_expiry_atm_iv` over the contracts
       inside ``moneyness`` of spot, giving one ATM vol per expiry.
    2. **Across maturity, between the two expiries bracketing** ``target_dte`` — linear in
       total variance ``σ²·T``, which is the construction that makes a 30-day vol comparable
       across days (linear-in-σ interpolation is not variance-additive and drifts on a steep
       term structure). ``T`` is the frame's own year fraction, honouring settlement time.

    When the 30-day point falls outside the available expiries, the nearest expiry's ATM vol
    is returned with ``interpolated=False`` rather than extrapolated.

    ``iv_history`` is prior ATM-IV observations for the *same symbol*. Supply at least
    :data:`MIN_IV_HISTORY` of them to get a label; supply nothing (the state of the world
    today — see :func:`build_report`) and ``label`` is ``None``.
    """
    label, observations = _iv_label(None, iv_history)
    empty = IvRegime(
        atm_iv=None,
        target_dte=target_dte,
        lower_dte=None,
        upper_dte=None,
        interpolated=False,
        contracts=0,
        label=None,
        history_observations=observations,
        min_history_required=MIN_IV_HISTORY,
    )
    if df.empty or not spot:
        return empty

    near = df.loc[
        _usable_iv(df, iv_policy)
        & (np.abs(df["strike"].to_numpy(dtype=float) / spot - 1.0) <= moneyness)
    ]
    if near.empty:
        return empty

    # One (dte, T, atm_iv) row per expiry that has usable ATM quotes.
    points: list[tuple[int, float, float]] = []
    for (dte, _expiry), rows in near.groupby(["dte", "expiry"], sort=True):
        vol = _expiry_atm_iv(rows, spot)
        t_years = float(rows["t"].mean())
        if vol is not None and math.isfinite(vol) and t_years > 0:
            points.append((int(dte), t_years, float(vol)))
    if not points:
        return empty

    points.sort(key=lambda p: p[1])
    dtes = [p[0] for p in points]
    ts = np.array([p[1] for p in points], dtype=float)
    vols = np.array([p[2] for p in points], dtype=float)

    target_t = target_dte / 365.0
    if len(points) == 1 or target_t <= ts[0] or target_t >= ts[-1]:
        # Outside the term structure (or only one expiry): use the nearest expiry verbatim.
        idx = int(np.argmin(np.abs(ts - target_t)))
        atm = float(vols[idx])
        lower = upper = dtes[idx]
        interpolated = False
    else:
        upper_i = int(np.searchsorted(ts, target_t))
        lower_i = upper_i - 1
        # Linear in total variance, then back out the vol at the target maturity.
        w_lo = vols[lower_i] ** 2 * ts[lower_i]
        w_hi = vols[upper_i] ** 2 * ts[upper_i]
        weight = (target_t - ts[lower_i]) / (ts[upper_i] - ts[lower_i])
        w = w_lo + weight * (w_hi - w_lo)
        atm = float(math.sqrt(max(w, 0.0) / target_t))
        lower, upper = dtes[lower_i], dtes[upper_i]
        interpolated = True

    label, observations = _iv_label(atm, iv_history)
    return IvRegime(
        atm_iv=_f(atm),
        target_dte=target_dte,
        lower_dte=lower,
        upper_dte=upper,
        interpolated=interpolated,
        contracts=len(near),
        label=label,
        history_observations=observations,
        min_history_required=MIN_IV_HISTORY,
    )


def _iv_label(atm: float | None, history: Sequence[float] | None) -> tuple[str | None, int]:
    """``(label, observations)``. The label is ``None`` below :data:`MIN_IV_HISTORY`.

    With enough history the current vol is placed by percentile against it: bottom third LOW,
    top third HIGH, otherwise NORMAL. Terciles are a description of the supplied sample, not a
    claim about volatility regimes in general — which is exactly why they only appear once
    there is a sample to describe.
    """
    usable = [float(v) for v in (history or []) if v is not None and math.isfinite(float(v))]
    observations = len(usable)
    if atm is None or observations < MIN_IV_HISTORY:
        return None, observations
    below = sum(1 for v in usable if v < atm)
    percentile = below / observations
    if percentile < 1 / 3:
        return "LOW", observations
    if percentile > 2 / 3:
        return "HIGH", observations
    return "NORMAL", observations


def dealer_positioning(
    net_gex: float,
    abs_gex: float,
    *,
    ratio_floor: float = POSITIONING_RATIO_FLOOR,
) -> DealerPositioning:
    """Direction of dealer gamma, or the word "noise-dominated" when the net is too small.

    See :data:`POSITIONING_RATIO_FLOOR` for where the floor comes from and why DIA must land
    below it (``docs/validation.md`` §9: DIA's net GEX is 0.9 % of its gross and its *sign*
    flips under a plausible carry correction, so a report calling DIA "short gamma" would be
    asserting something the validation document explicitly says we cannot support).
    """
    ratio = (abs(net_gex) / abs_gex) if abs_gex else None

    if ratio is None:
        return DealerPositioning(
            net_gex=net_gex,
            abs_gex=abs_gex,
            ratio=None,
            ratio_floor=ratio_floor,
            noise_dominated=True,
            direction=None,
            label="NO DATA",
            description="No contracts in scope, so there is no dealer position to report.",
        )

    if ratio < ratio_floor:
        return DealerPositioning(
            net_gex=net_gex,
            abs_gex=abs_gex,
            ratio=ratio,
            ratio_floor=ratio_floor,
            noise_dominated=True,
            direction=None,
            label="NOISE-DOMINATED",
            description=(
                f"Net gamma is {ratio:.1%} of gross, below the {ratio_floor:.0%} floor. "
                "A net this small is within the range a known-wrong carry assumption can "
                "move it (docs/validation.md section 9), so no direction is reported. "
                "Per-strike walls below are unaffected and remain usable."
            ),
        )

    if net_gex > 0:
        return DealerPositioning(
            net_gex=net_gex,
            abs_gex=abs_gex,
            ratio=ratio,
            ratio_floor=ratio_floor,
            noise_dominated=False,
            direction="LONG",
            label="LONG GAMMA",
            description=(
                f"Dealers are net long gamma ({ratio:.1%} of gross). Hedging buys weakness "
                "and sells strength, which tends to dampen moves."
            ),
        )
    return DealerPositioning(
        net_gex=net_gex,
        abs_gex=abs_gex,
        ratio=ratio,
        ratio_floor=ratio_floor,
        noise_dominated=False,
        direction="SHORT",
        label="SHORT GAMMA",
        description=(
            f"Dealers are net short gamma ({ratio:.1%} of gross). Hedging chases the move, "
            "which tends to amplify it."
        ),
    )


def _level(row: Any, spot: float, side: str) -> ReportLevel:
    strike = float(row.strike)
    return ReportLevel(
        strike=strike,
        net_gex=float(row.net_gex),
        abs_gex=float(row.abs_gex),
        open_interest=int(row.open_interest),
        distance=strike - spot,
        distance_pct=None if not spot else (strike - spot) / spot * 100.0,
        side=side,
        above_spot=strike > spot,
    )


def support_resistance(result: GexResult) -> LevelSet:
    """Split the engine's top strikes into resistance above spot and support below it.

    Anything on the wrong side of spot for its sign goes to ``straddling`` and sets
    ``overlapping``; see :class:`LevelSet` for why that bucket exists rather than a reordering.
    """
    spot = float(result.spot)
    levels = result.levels

    resistance: list[ReportLevel] = []
    support: list[ReportLevel] = []
    straddling: list[ReportLevel] = []

    for row in levels.top_positive:
        # Positive net GEX is dealer long gamma, which sells into strength -- resistance, but
        # only where price would actually meet it, i.e. above spot.
        (resistance if row.strike > spot else straddling).append(
            _level(row, spot, "RESISTANCE" if row.strike > spot else "STRADDLING")
        )
    for row in levels.top_negative:
        (support if row.strike < spot else straddling).append(
            _level(row, spot, "SUPPORT" if row.strike < spot else "STRADDLING")
        )

    # Nearest first: the level a move meets next matters more than the biggest one.
    resistance.sort(key=lambda level: level.strike)
    support.sort(key=lambda level: -level.strike)
    straddling.sort(key=lambda level: abs(level.strike - spot))

    note: str | None = None
    if straddling:
        wrong_side = ", ".join(f"{level.strike:g}" for level in straddling)
        note = (
            f"Gamma is concentrated on both sides of spot: {wrong_side} carries net gamma "
            "of the sign normally found on the other side of the market. These strikes are "
            "listed separately rather than sorted into support or resistance, because "
            "either placement would misdescribe them."
        )

    return LevelSet(
        resistance=tuple(resistance),
        support=tuple(support),
        straddling=tuple(straddling),
        overlapping=bool(straddling),
        overlap_note=note,
        call_wall=levels.call_wall,
        put_wall=levels.put_wall,
        flip_point=levels.flip_point,
    )


def premium_selling(
    df: pd.DataFrame,
    result: GexResult,
    *,
    dte_min: int = DEFAULT_PREMIUM_DTE_MIN,
    dte_max: int = DEFAULT_PREMIUM_DTE_MAX,
    top_n: int = DEFAULT_PREMIUM_TOP_N,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
) -> PremiumSelling:
    """Screen for OTM contracts beyond the walls in a DTE window. Screening output only.

    Each side is ranked by mid price descending — the most premium per contract first — and
    truncated to ``top_n``. Contracts with no bid are excluded (they cannot be sold) and so
    are contracts with no quoted mid.
    """
    spot = float(result.spot)
    call_wall = result.levels.call_wall
    put_wall = result.levels.put_wall

    def _empty(note: str | None) -> PremiumSelling:
        return PremiumSelling(
            calls=(),
            puts=(),
            dte_min=dte_min,
            dte_max=dte_max,
            call_boundary=call_wall,
            put_boundary=put_wall,
            note=note,
        )

    if df.empty:
        return _empty("No contracts in scope.")
    if call_wall is None and put_wall is None:
        return _empty("No walls were computed for this filter, so there is no boundary to screen beyond.")

    dte = df["dte"].to_numpy(dtype=float)
    bid = df["bid"].to_numpy(dtype=float)
    ask = df["ask"].to_numpy(dtype=float)
    base = (
        _usable_iv(df, iv_policy)
        & (dte >= dte_min)
        & (dte <= dte_max)
        & ~np.isnan(bid)
        & ~np.isnan(ask)
        & (bid > 0)
    )
    window = df.loc[base]
    if window.empty:
        return _empty(
            f"No quoted contracts between {dte_min} and {dte_max} DTE in this expiry scope."
        )

    strike = window["strike"].to_numpy(dtype=float)
    is_call = window["right"].to_numpy(dtype=object) == "C"

    def _candidates(mask: np.ndarray) -> tuple[PremiumCandidate, ...]:
        rows = window.loc[mask]
        out = [
            PremiumCandidate(
                occ_symbol=str(row.occ_symbol),
                strike=float(row.strike),
                right=str(row.right),
                expiry=row.expiry,
                dte=int(row.dte),
                bid=_f(row.bid),
                ask=_f(row.ask),
                mid=_f((row.bid + row.ask) / 2.0),
                iv=_f(row.iv),
                open_interest=_i(row.open_interest),
                distance_pct=None if not spot else (float(row.strike) - spot) / spot * 100.0,
            )
            for row in rows.itertuples(index=False)
        ]
        out = [c for c in out if c.mid is not None]
        out.sort(key=lambda c: (-(c.mid or 0.0), c.strike))
        return tuple(out[:top_n])

    # "OTM contracts beyond the walls" is two conditions, not one, and on a chain where a
    # wall sits on the wrong side of spot they genuinely disagree: DIA's 533 put wall is
    # *above* its 532.34 spot, so "at or below the put wall" alone would screen in near-ATM
    # puts and call them premium selling. Both conditions are applied.
    calls = (
        _candidates(is_call & (strike >= call_wall) & (strike >= spot))
        if call_wall is not None
        else ()
    )
    puts = (
        _candidates(~is_call & (strike <= put_wall) & (strike <= spot))
        if put_wall is not None
        else ()
    )

    notes: list[str] = []
    if call_wall is not None and not calls:
        notes.append(
            f"Nothing quoted at or above the {call_wall:g} call wall in this DTE window."
        )
    if put_wall is not None and not puts:
        notes.append(f"Nothing quoted at or below the {put_wall:g} put wall in this DTE window.")
    if put_wall is not None and spot and (spot - put_wall) / spot > 0.10:
        notes.append(
            f"The put wall sits {((spot - put_wall) / spot) * 100:.1f} % below spot; the "
            "screen does not widen its boundary to find candidates closer in."
        )

    return PremiumSelling(
        calls=calls,
        puts=puts,
        dte_min=dte_min,
        dte_max=dte_max,
        call_boundary=call_wall,
        put_boundary=put_wall,
        note=" ".join(notes) or None,
    )


def build_playbook(result: GexResult, levels: LevelSet, pain: MaxPain) -> Playbook:
    """Deterministic scenarios, every number of which is a level computed elsewhere.

    Targets and invalidations are the *next computed level* past the trigger, never a
    percentage of spot. Where no such level exists the field is ``None``.
    """
    spot = float(result.spot)
    call_wall = levels.call_wall
    put_wall = levels.put_wall

    resistance = sorted({level.strike for level in levels.resistance})
    support = sorted({level.strike for level in levels.support}, reverse=True)

    def _next_above(reference: float | None) -> float | None:
        if reference is None:
            return None
        return next((s for s in resistance if s > reference), None)

    def _next_below(reference: float | None) -> float | None:
        if reference is None:
            return None
        return next((s for s in support if s < reference), None)

    entries = [
        PlaybookEntry(
            key="BULLISH_BREAKOUT",
            name="Upside break",
            trigger=call_wall,
            trigger_label="call wall",
            target=_next_above(call_wall),
            target_label="next resistance strike above the call wall",
            invalidation=support[0] if support else None,
            invalidation_label="nearest support strike below spot",
            strategy="Long calls or call spreads.",
        ),
        PlaybookEntry(
            key="BEARISH_BREAKDOWN",
            name="Downside break",
            trigger=put_wall,
            trigger_label="put wall",
            target=_next_below(put_wall),
            target_label="next support strike below the put wall",
            invalidation=resistance[0] if resistance else None,
            invalidation_label="nearest resistance strike above spot",
            strategy="Long puts or put spreads.",
        ),
    ]

    in_range = (
        call_wall is not None and put_wall is not None and put_wall < spot < call_wall
    )
    if in_range:
        entries.append(
            PlaybookEntry(
                key="RANGE_BOUND",
                name="Range",
                trigger=pain.strike,
                trigger_label="max pain, the strike open interest is centred on",
                target=call_wall,
                target_label="call wall (upper bound)",
                invalidation=put_wall,
                invalidation_label="put wall (lower bound)",
                strategy="Range structures such as iron condors or butterflies.",
            )
        )

    return Playbook(
        entries=tuple(entries),
        range_low=put_wall if in_range else None,
        range_high=call_wall if in_range else None,
        range_magnet=pain.strike if in_range else None,
        spot_in_range=in_range,
    )


def risk_alerts(
    result: GexResult,
    levels: LevelSet,
    positioning: DealerPositioning,
    *,
    flip_proximity: float = FLIP_PROXIMITY_PCT,
) -> tuple[RiskAlert, ...]:
    """Deterministic alerts derived from the computed levels. No forecast, no invented number."""
    spot = float(result.spot)
    alerts: list[RiskAlert] = []

    if not result.by_strike:
        alerts.append(
            RiskAlert(
                code="EMPTY_SCOPE",
                severity="INFO",
                message=(
                    f"The {result.filter} filter admitted no contracts, so every level in "
                    "this report is empty. On an end-of-day capture this is the normal "
                    "result for a 0DTE filter — same-day contracts have already expired."
                ),
                level=None,
            )
        )
        return tuple(alerts)

    flip = levels.flip_point
    if flip is None:
        alerts.append(
            RiskAlert(
                code="NO_FLIP_POINT",
                severity="INFO",
                message=(
                    "The gamma profile does not change sign anywhere in the ±10 % grid, so "
                    "there is no flip level to watch."
                ),
                level=None,
            )
        )
    elif spot and abs(flip - spot) / spot <= flip_proximity:
        alerts.append(
            RiskAlert(
                code="FLIP_PROXIMITY",
                severity="WARNING",
                message=(
                    f"Spot is within {flip_proximity:.0%} of the gamma flip at {flip:,.2f}. "
                    "Dealer hedging changes character across this level."
                ),
                level=flip,
            )
        )

    if levels.call_wall is not None and spot > levels.call_wall:
        alerts.append(
            RiskAlert(
                code="SPOT_ABOVE_CALL_WALL",
                severity="WARNING",
                message=(
                    f"Spot is already above the {levels.call_wall:g} call wall, so the "
                    "largest positive-gamma strike is below the market rather than above it."
                ),
                level=levels.call_wall,
            )
        )
    if levels.put_wall is not None and spot < levels.put_wall:
        alerts.append(
            RiskAlert(
                code="SPOT_BELOW_PUT_WALL",
                severity="WARNING",
                message=(
                    f"Spot is already below the {levels.put_wall:g} put wall, so the largest "
                    "negative-gamma strike is above the market rather than below it."
                ),
                level=levels.put_wall,
            )
        )

    if positioning.noise_dominated:
        alerts.append(
            RiskAlert(
                code="POSITIONING_NOISE_DOMINATED",
                severity="WARNING",
                message=positioning.description,
                level=None,
            )
        )
    if levels.overlapping and levels.overlap_note:
        alerts.append(
            RiskAlert(
                code="LEVELS_OVERLAP",
                severity="INFO",
                message=levels.overlap_note,
                level=None,
            )
        )
    if not levels.resistance:
        alerts.append(
            RiskAlert(
                code="NO_RESISTANCE",
                severity="INFO",
                message="No positive-gamma strike sits above spot in this expiry scope.",
                level=None,
            )
        )
    if not levels.support:
        alerts.append(
            RiskAlert(
                code="NO_SUPPORT",
                severity="INFO",
                message="No negative-gamma strike sits below spot in this expiry scope.",
                level=None,
            )
        )

    return tuple(alerts)


def _summary(
    result: GexResult,
    positioning: DealerPositioning,
    levels: LevelSet,
    pain: MaxPain,
    regime: IvRegime,
) -> tuple[str, ...]:
    """Executive summary lines. Each restates a computed figure; none adds a new claim."""
    lines: list[str] = []
    # `positioning.description` is already a full sentence naming the direction, so it is
    # used verbatim rather than prefixed with a restatement of the same fact.
    if positioning.noise_dominated:
        lines.append(f"Dealer positioning is not readable on this chain. {positioning.description}")
    else:
        lines.append(positioning.description)

    if levels.call_wall is not None and levels.put_wall is not None:
        lines.append(
            f"The computed walls bracket {levels.put_wall:g} to {levels.call_wall:g}, "
            f"against a spot of {result.spot:,.2f}."
        )
    if pain.strike is not None:
        lines.append(
            f"Open interest is centred on {pain.strike:g} (max pain), "
            f"{pain.distance_pct:+.2f} % from spot."
        )
    if regime.atm_iv is not None:
        if regime.label is None:
            lines.append(
                f"ATM {regime.target_dte}-day implied vol is {regime.atm_iv:.1%}. No regime "
                f"label: that needs at least {regime.min_history_required} prior snapshots "
                f"to compare against and there are {regime.history_observations}."
            )
        else:
            lines.append(
                f"ATM {regime.target_dte}-day implied vol is {regime.atm_iv:.1%} "
                f"({regime.label} against {regime.history_observations} prior observations)."
            )
    return tuple(lines)


# --------------------------------------------------------------------------------------
# Front door
# --------------------------------------------------------------------------------------


def build_report(
    result: GexResult,
    frame: pd.DataFrame,
    filters: Any = ExpiryFilter.ALL,
    *,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    iv_history: Sequence[float] | None = None,
    target_dte: int = DEFAULT_TARGET_DTE,
    premium_dte_min: int = DEFAULT_PREMIUM_DTE_MIN,
    premium_dte_max: int = DEFAULT_PREMIUM_DTE_MAX,
    premium_top_n: int = DEFAULT_PREMIUM_TOP_N,
    ratio_floor: float = POSITIONING_RATIO_FLOOR,
) -> ReportResult:
    """Compute every report figure for one ``GexResult`` and its frame.

    Args:
        result: The output of :func:`app.gex.engine.compute_all` for this snapshot and filter.
        frame: The **unfiltered** ``to_frame`` DataFrame for the same snapshot. This function
            applies ``filters`` to it itself, so that the per-contract figures describe the
            same population as ``result``'s aggregates.
        filters: The same filter value that produced ``result``. Passing a different one
            yields a report whose two halves describe different contract sets; there is no way
            for this function to detect that, because ``result.filter`` is a label
            (``"EXPIRIES:..."`` for an explicit list) rather than the filter object.
        iv_policy: Implied-vol admission policy, matching the engine's.
        iv_history: Prior ATM-IV observations for this symbol, oldest or newest first (order
            is irrelevant; only the distribution is used). **Nothing in the schema persists
            these today** — ``gex_levels`` has no ATM-IV column and adding one is a migration
            T39 was told not to make — so the API layer passes nothing and the regime label
            comes back ``None``. That is the honest current state, and the UI renders
            "insufficient history" for it rather than a fabricated band.
        target_dte: Constant maturity for the ATM IV, in calendar days.
        premium_dte_min, premium_dte_max, premium_top_n: Premium-selling screen window.
        ratio_floor: Positioning confidence floor; see :data:`POSITIONING_RATIO_FLOOR`.

    Returns:
        A :class:`ReportResult`; ``.to_dict()`` is JSON-serializable.
    """
    selected = frame.loc[expiry_mask(frame, filters)] if not frame.empty else frame

    pain = max_pain(selected, float(result.spot))
    ratios = put_call_ratios(selected)
    regime = iv_regime(
        selected,
        float(result.spot),
        target_dte=target_dte,
        iv_policy=iv_policy,
        iv_history=iv_history,
    )
    positioning = dealer_positioning(
        result.levels.net_gex, result.levels.abs_gex, ratio_floor=ratio_floor
    )
    levels = support_resistance(result)
    premium = premium_selling(
        selected,
        result,
        dte_min=premium_dte_min,
        dte_max=premium_dte_max,
        top_n=premium_top_n,
        iv_policy=iv_policy,
    )
    playbook = build_playbook(result, levels, pain)
    alerts = risk_alerts(result, levels, positioning)

    return ReportResult(
        underlying=str(result.underlying),
        filter=str(result.filter),
        spot=float(result.spot),
        # The snapshot's own effective instant, never `datetime.now()` -- this module reads no
        # clock, so regenerating a month-old snapshot's report reproduces it exactly.
        generated_at=result.snapshot.captured_at,
        snapshot=result.snapshot,
        max_pain=pain,
        ratios=ratios,
        iv_regime=regime,
        positioning=positioning,
        levels=levels,
        premium=premium,
        playbook=playbook,
        alerts=alerts,
        summary=_summary(result, positioning, levels, pain, regime),
    )


# --------------------------------------------------------------------------------------
# Plain-text rendering
# --------------------------------------------------------------------------------------

_WIDTH = 78
_RULE = "-" * _WIDTH
_DASH = "--"


def _banner(title: str) -> list[str]:
    return ["=" * _WIDTH, title.center(_WIDTH), "=" * _WIDTH]


def _num(value: float | None, spec: str = ",.2f") -> str:
    return _DASH if value is None else format(value, spec)


def _strike(value: float | None) -> str:
    return _DASH if value is None else f"{value:g}"


def _pct(value: float | None, digits: int = 2, width: int = 8) -> str:
    """A distance already expressed in percent (not a fraction), right-aligned.

    Padded because a level list mixes ``+2.02%`` with ``+10.63%`` and an unpadded column
    stops lining up exactly where the reader is scanning down it.
    """
    text = _DASH if value is None else f"{value:+.{digits}f}%"
    return text.rjust(width)


def render_text(result: ReportResult) -> str:
    """Render a :class:`ReportResult` as plain text, so the report exists outside the browser.

    Deliberately separate from the computation — nothing is derived here, every value is read
    off ``result``. The section order follows ``context/example-report.md`` (that file's
    *layout* is the reference; its numbers are not — see the module docstring). Emoji and box
    drawing are dropped in favour of ASCII: this output is piped, diffed and snapshot-tested,
    and the T40 panel renders it in a monospace block where alignment matters more than
    decoration.
    """
    r = result
    out: list[str] = []

    out += _banner(f"{r.underlying} OPTIONS INTELLIGENCE")
    out.append(f"Filter:          {r.filter}")
    out.append(f"As of:           {r.generated_at.isoformat()}")
    out.append(f"Source:          {r.snapshot.source} (delayed {r.snapshot.delayed_minutes}m)")
    out.append(f"Current price:   {r.spot:,.2f}")
    out.append(
        f"Max pain:        {_strike(r.max_pain.strike)} "
        f"({_num(r.max_pain.distance, '+,.2f')}, {_pct(r.max_pain.distance_pct)})"
    )
    iv = r.iv_regime
    iv_text = _DASH if iv.atm_iv is None else f"{iv.atm_iv:.1%}"
    if iv.atm_iv is not None:
        iv_text += (
            f" ATM ~{iv.target_dte}d"
            f" ({'interpolated ' if iv.interpolated else ''}"
            f"{iv.lower_dte}-{iv.upper_dte} DTE)"
        )
    out.append(f"ATM implied vol: {iv_text}")
    out.append(
        "IV regime:       "
        + (
            iv.label
            if iv.label
            else (
                f"insufficient history ({iv.history_observations} of "
                f"{iv.min_history_required} prior observations needed)"
            )
        )
    )
    out.append("")

    out += _banner("DEALER POSITIONING")
    out.append(f"Status: {r.positioning.label}")
    out.append(f"Net GEX:   {r.positioning.net_gex:+,.0f}")
    out.append(f"Gross GEX: {r.positioning.abs_gex:,.0f}")
    ratio = r.positioning.ratio
    out.append(
        f"|Net| / gross: {_DASH if ratio is None else f'{ratio:.1%}'} "
        f"(floor {r.positioning.ratio_floor:.0%})"
    )
    out += _wrap(r.positioning.description)
    out.append("")

    out += _banner("GAMMA EXPOSURE LANDSCAPE")
    out.append("RESISTANCE (positive net gamma above spot)")
    out.append(_RULE)
    out += _level_lines(r.levels.resistance)
    out.append("")
    out.append("SUPPORT (negative net gamma below spot)")
    out.append(_RULE)
    out += _level_lines(r.levels.support)
    if r.levels.straddling:
        out.append("")
        out.append("STRADDLING SPOT (neither support nor resistance)")
        out.append(_RULE)
        out += _level_lines(r.levels.straddling)
        if r.levels.overlap_note:
            out += _wrap(r.levels.overlap_note)
    out.append("")
    out.append(f"Call wall: {_strike(r.levels.call_wall)}")
    out.append(f"Put wall:  {_strike(r.levels.put_wall)}")
    out.append(f"Gamma flip: {_num(r.levels.flip_point)}")
    out.append("")

    out += _banner("MARKET SENTIMENT")
    ratios = r.ratios
    out.append(
        f"P/C ratio (open interest): "
        f"{_DASH if ratios.open_interest_ratio is None else f'{ratios.open_interest_ratio:.2f}'}"
    )
    out.append(
        f"P/C ratio (volume):        "
        f"{_DASH if ratios.volume_ratio is None else f'{ratios.volume_ratio:.2f}'}"
    )
    out.append("")
    out.append(f"{'':<14}{'Calls':>14}{'Puts':>14}{'Total':>14}")
    out.append(
        f"{'Open interest':<14}{ratios.call_open_interest:>14,}"
        f"{ratios.put_open_interest:>14,}{ratios.total_open_interest:>14,}"
    )
    out.append(
        f"{'Volume':<14}{ratios.call_volume:>14,}"
        f"{ratios.put_volume:>14,}{ratios.total_volume:>14,}"
    )
    out.append(
        f"{'Contracts':<14}{ratios.call_contracts:>14,}"
        f"{ratios.put_contracts:>14,}{ratios.call_contracts + ratios.put_contracts:>14,}"
    )
    if ratios.missing_open_interest or ratios.missing_volume:
        out.append(
            f"Excluded as unknown: {ratios.missing_open_interest:,} open interest, "
            f"{ratios.missing_volume:,} volume."
        )
    out.append("")

    out += _banner("PREMIUM SELLING SCREEN")
    out += _wrap(
        "Screening output computed from the current chain, not a recommendation. These are "
        "the quoted contracts that sit beyond the computed walls in the "
        f"{r.premium.dte_min}-{r.premium.dte_max} DTE window; no order is ever routed."
    )
    out.append("")
    out.append(f"Calls at or above the {_strike(r.premium.call_boundary)} call wall:")
    out += _candidate_lines(r.premium.calls)
    out.append("")
    out.append(f"Puts at or below the {_strike(r.premium.put_boundary)} put wall:")
    out += _candidate_lines(r.premium.puts)
    if r.premium.note:
        out.append("")
        out += _wrap(r.premium.note)
    out.append("")

    out += _banner("PLAYBOOK")
    out += _wrap(
        "Deterministic scenarios built from the levels above. Every trigger, target and "
        "invalidation is a computed level or is left blank; none is a percentage of spot or "
        "a rule of thumb. Screening output, not a recommendation."
    )
    out.append("")
    for entry in r.playbook.entries:
        out.append(f"{entry.name.upper()}")
        out.append(f"  Trigger:      {_strike(entry.trigger)}  ({entry.trigger_label})")
        out.append(f"  Target:       {_strike(entry.target)}  ({entry.target_label})")
        out.append(
            f"  Invalidation: {_strike(entry.invalidation)}  ({entry.invalidation_label})"
        )
        out.append(f"  Structures:   {entry.strategy}")
        out.append("")
    if r.playbook.spot_in_range:
        out.append(
            f"Spot sits inside the {_strike(r.playbook.range_low)}-"
            f"{_strike(r.playbook.range_high)} wall range, magnet "
            f"{_strike(r.playbook.range_magnet)}."
        )
        out.append("")

    out += _banner("RISK ALERTS")
    if not r.alerts:
        out.append("None.")
    for alert in r.alerts:
        out.append(f"[{alert.severity}] {alert.code}")
        out += _wrap(alert.message, indent="  ")
    out.append("")

    out += _banner("EXECUTIVE SUMMARY")
    for line in r.summary:
        out += _wrap(line)
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _wrap(text: str, *, indent: str = "") -> list[str]:
    """Greedy wrap to :data:`_WIDTH`. ``textwrap`` would do, but this keeps the indent rule
    in one place and never re-flows an already short line into something unexpected."""
    words = text.split()
    lines: list[str] = []
    current = indent
    for word in words:
        candidate = f"{current}{word} " if current.strip() else f"{indent}{word} "
        if len(candidate.rstrip()) > _WIDTH and current.strip():
            lines.append(current.rstrip())
            current = f"{indent}{word} "
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip())
    return lines or [""]


def _level_lines(levels: tuple[ReportLevel, ...]) -> list[str]:
    if not levels:
        return ["  (none)"]
    return [
        f"  {i:>2}. {level.strike:>10g}  net {level.net_gex:>+16,.0f}  "
        f"{_pct(level.distance_pct)}  OI {level.open_interest:>10,}"
        for i, level in enumerate(levels, start=1)
    ]


def _candidate_lines(candidates: tuple[PremiumCandidate, ...]) -> list[str]:
    if not candidates:
        return ["  (none)"]
    return [
        f"  {i:>2}. {c.strike:>10g} {c.right}  mid {_num(c.mid):>7}  "
        f"IV {_DASH if c.iv is None else f'{c.iv:.1%}':>7}  {c.dte:>3}d  "
        f"{_pct(c.distance_pct)}  OI {c.open_interest:>9,}"
        for i, c in enumerate(candidates, start=1)
    ]
