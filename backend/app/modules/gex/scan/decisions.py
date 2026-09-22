"""Decision engine -- pure module (T60).

Turns the numbers the rest of the app already computes into *opportunities*: a side, an entry,
a stop, a target, the thesis behind them and the conditions that invalidate them. It sits on
top of `app.modules.gex.scan.regime` (dealer positioning, walls, flip, ATR-normalized distances and the
fade/continuation verdict), `app.modules.gex.scan.trend` (the cross-sectional trend composite) and
`app.modules.gex.scan.breakouts` (the historical continuation base rate), and adds no data source of its
own -- every number it emits is a level, a distance or a rate one of those modules measured.

**Pure, on exactly the same contract as `app.modules.gex.gex.engine`, `app.modules.gex.scan.regime` and friends**: no
HTTP, no database, no filesystem, no logging, no clock. `app.modules.gex.api.decisions` is the only caller
that does I/O; it builds the inputs the same way `app.modules.gex.api.scan.get_regime` does and hands them
here. That split is what lets an offline test hand-build a `RegimeRow` and get byte-identical
suggestions, and what will let a backtest replay a week of stored levels through the same code.

Scope, unchanged from PLAN.md section 2 and `app.modules.gex.gex.report`'s "Trade suggestions" section:
these are **suggestions over the current chain and bars, never orders**. The app routes nothing.
Every renderer labels them as such.

What an opportunity is
----------------------

Two setups, mirroring the two verdicts `app.modules.gex.scan.regime` can reach:

* **Fade** (dealers long gamma). Hedging sells strength and buys weakness, so a move into a
  wall meets supply (call wall above) or demand (put wall below). The entry is *the wall
  itself* -- a resting limit, not a market order -- the stop is a fixed ATR buffer beyond it
  (:data:`FADE_STOP_BUFFER_ATR`), and the target is the first strike with meaningful gamma
  on the way back toward the opposite wall that pays at least :data:`MIN_REWARD_RISK` times the
  risk, with the opposite wall itself as the extended target. One fade per wall, so a symbol
  sitting between two walls can carry both a short-at-the-call-wall and a long-at-the-put-wall
  suggestion at once: they are the same range read from both ends.

* **Continuation** (dealers short gamma, or long gamma but within
  `CONTINUATION_FLIP_ATR` of the flip with room to run). Hedging chases the move, so the entry
  is spot in the direction of the last five sessions' move, the stop is the nearest computed
  level *behind* the entry (the flip, or the wall on the wrong side) plus a buffer when one sits
  within :data:`STOP_MAX_ATR`, and otherwise a plain :data:`VOLATILITY_STOP_ATR` volatility
  stop -- named as such in `stop_label`, never dressed up as a level. The target is the wall
  ahead, then the strike beyond it with comparable gamma.

Entry/stop/target are always *distinct* prices on the correct side of each other, or the
opportunity is not emitted at all. A setup that resolves to a reward/risk below
:data:`MIN_REWARD_RISK` *is* emitted, as ``status="rejected"`` with the reason named -- the
honest answer to "why is there nothing to do on SPY today" is a row that says so, not an
absent row.

Status
------

``active``   the entry is reachable now: a continuation entry (always spot), or a fade whose
             wall sits within :data:`FADE_REACH_ATR` of spot.
``watch``    a fade whose wall is between :data:`FADE_REACH_ATR` and :data:`WATCH_REACH_ATR`
             away -- a level to pre-plan, not one to work today.
``rejected`` the geometry does not pay: reward/risk below :data:`MIN_REWARD_RISK`.

Score
-----

An integer 0-100 with every component listed on the opportunity (`score_breakdown`), so a
reader can see *why* a B is a B. The weights below sum to exactly 100:

=====================  =====  ==========================================================
component              max    rule
=====================  =====  ==========================================================
regime alignment        35    setup matches the verdict: 35; `mixed`: 20; contradicts: 0
positioning conviction  20    `min(20, ratio * 40)` -- |net|/gross of 0.5 saturates it
reward/risk             20    `5 + (rr - 1) * 7.5`, clamped to [0, 20]; rr 3 saturates
trend context           15    continuation: `composite * 15`; fade: `(1 - composite) * 15`
breakout base rate      10    continuation: `rate * 10`; fade: `(1 - rate) * 10`
=====================  =====  ==========================================================

A component whose input is unavailable (`None` composite, `None` breakout rate) scores 0 and is
listed with a note saying so -- the same "an unmeasurable input is not a failing one, but it is
never a passing one either" posture `app.modules.gex.scan.regime._verdict` takes for the 0DTE share. Grades:
A >= 75, B >= 60, C >= 45, D below. The thresholds are named constants; nothing here has a live
calibration behind it yet, and `docs/` should gain one once a few weeks of rows exist.

No-trade
--------

`DecisionResult.no_trade_reasons` is non-empty exactly when `opportunities` is empty, and says
which gate closed: a stale chain, a noise-dominated net GEX, no ATR to size a stop with, no wall
on either side, or a continuation regime with no five-day direction to follow. A row with an
empty `opportunities` and an empty `no_trade_reasons` cannot be produced.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.modules.gex.gex.engine import StrikeGex
from app.modules.gex.scan.breakouts import BreakoutSummary, Direction, Outcome
from app.modules.gex.scan.regime import (
    CONTINUATION_FLIP_ATR,
    CONTINUATION_WALL_ATR,
    FADE_WALL_ATR,
    ROOM_BEYOND_FRACTION,
    RegimeRow,
    WallInfo,
)

__all__ = [
    "FADE_REACH_ATR",
    "FADE_STOP_BUFFER_ATR",
    "GRADE_A",
    "GRADE_B",
    "GRADE_C",
    "IV_CHEAP_RATIO",
    "IV_RICH_RATIO",
    "LEVEL_STOP_BUFFER_ATR",
    "MIN_REWARD_RISK",
    "MIN_TARGET_ATR",
    "PIN_REACH_ATR",
    "STOP_MAX_ATR",
    "TARGET_FALLBACK_ATR",
    "VOLATILITY_STOP_ATR",
    "WATCH_REACH_ATR",
    "DecisionResult",
    "Opportunity",
    "ScoreComponent",
    "decide",
]


# --------------------------------------------------------------------------------------
# Documented constants. Every ATR multiple below is a judgment call with no live calibration
# behind it yet (see the module docstring's "Score" section); each is named here so a later
# pass finds one number to move, not logic to re-derive.
# --------------------------------------------------------------------------------------

#: A fade is `active` when its wall is within this many ATR of spot: the level can plausibly
#: be tagged in the next session. Deliberately wider than `app.modules.gex.scan.regime.FADE_WALL_ATR`
#: (1.0, the *verdict's* gate) -- a wall 1.3 ATR away is not "near" enough to call the whole
#: regime a fade, but it is near enough to rest a limit at.
FADE_REACH_ATR = 1.5

#: Beyond `FADE_REACH_ATR` but within this, a fade is emitted as `watch`: a level to pre-plan
#: around. Past it the wall is not emitted at all -- three ATR is more than a week of ordinary
#: range and the chain will have been recaptured several times before spot gets there.
WATCH_REACH_ATR = 3.0

#: How close spot must sit to a positive-gamma strike *below* it before that strike is a
#: `GAMMA_PIN` rather than a level price has left behind (T99).
#:
#: A pin is a claim about proximity, not about reach: the setup is "spot is resting on the
#: largest positive-gamma strike in the book and dealer hedging is holding it there", which
#: stops being true once price has actually travelled away. The five historical rows that
#: motivated this key sat 0.10, 0.11, 0.17, 0.25 and 0.72 ATR from spot, so 1.0 covers every
#: observed case with margin while excluding the 1.0-3.0 ATR band where `WATCH_REACH_ATR`
#: would otherwise have emitted one. Deliberately tighter than `FADE_REACH_ATR`: a wall you
#: can rest a limit at is a different claim from a strike price is pinned to.
PIN_REACH_ATR = 1.0

#: Stop distance beyond a faded wall. Half an ATR is enough that an intraday probe through the
#: strike (the ordinary way a wall gets *tested*) does not stop the trade, while a close a full
#: half-ATR beyond it is a genuine absorption of the strike's gamma.
FADE_STOP_BUFFER_ATR = 0.5

#: Buffer past a computed level (flip or wall) used as a continuation stop. Smaller than the
#: fade buffer because the level here is *behind* the entry: it is the point at which the
#: hedging regime the trade rides changes, so there is less reason to give it room.
LEVEL_STOP_BUFFER_ATR = 0.25

#: A computed level is only used as a continuation stop when it sits within this many ATR of
#: the entry. Further than that and the "level" stop is really an uncapped one -- the
#: volatility stop below is the honest choice instead.
STOP_MAX_ATR = 1.5

#: The volatility stop for a continuation entry with no computed level close enough behind it.
#: One ATR is the standard daily-range stop; it is labelled as exactly that on the opportunity.
VOLATILITY_STOP_ATR = 1.0

#: A continuation target when no wall exists ahead at all: a plain ATR multiple, labelled as
#: such. Two ATR against a one-ATR stop is the minimum reward/risk the engine considers
#: worthwhile, so this fallback exactly clears `MIN_REWARD_RISK` and no more.
TARGET_FALLBACK_ATR = 2.0

#: A target strike closer than this to the entry is skipped when walking the by-strike ladder:
#: a level a quarter-ATR away is inside the noise of the next session's open.
MIN_TARGET_ATR = 0.25

#: Below this reward/risk an opportunity is emitted as `rejected`. Two-to-one is the
#: conventional floor for a discretionary setup; one-to-one is the floor below which the
#: geometry cannot be worth the spread. The engine uses the latter and lets the score (which
#: saturates at 3:1) rank everything above it.
MIN_REWARD_RISK = 1.0

#: `iv_rv_ratio` at or above this reads as "implied rich versus realized" -- structure hints
#: lean toward selling premium. At or below `IV_CHEAP_RATIO` they lean toward buying it.
#: Between the two, no vol view is expressed. Ten percent either way is the smallest band
#: that is not just noise in a 20-day realized-vol estimate.
IV_RICH_RATIO = 1.10
IV_CHEAP_RATIO = 0.90

#: Grade thresholds on the 0-100 score.
GRADE_A = 75
GRADE_B = 60
GRADE_C = 45

# Score weights -- kept as private constants so the docstring table and the code cannot drift
# without a test (`test_scan_decisions.py` pins their sum to 100).
_W_REGIME = 35
_W_CONVICTION = 20
_W_REWARD_RISK = 20
_W_TREND = 15
_W_BREAKOUT = 10


# --------------------------------------------------------------------------------------
# Result records
# --------------------------------------------------------------------------------------


def _f(x: Any) -> float | None:
    """NaN/inf -> `None`, everything else -> `float`. Same helper every result module has."""
    if x is None:
        return None
    value = float(x)
    return value if math.isfinite(value) else None


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """One line of an opportunity's score breakdown: the component, the points it earned out of
    its maximum, and a one-line note naming the input that produced them (or its absence)."""

    name: str
    points: int
    max_points: int
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "points": self.points,
            "max_points": self.max_points,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Opportunity:
    """One suggested trade. See the module docstring for the vocabulary of `setup`, `side` and
    `status`, and for exactly where `entry`/`stop`/`target` come from.

    `target_2` is the extended target (the opposite wall for a fade, the strike beyond the wall
    for a continuation) and is `None` when it would coincide with `target` or when no such
    level exists. `rr`, `risk_atr` and `reward_atr` are computed on the primary `target` only.

    `thesis` and `invalidation` are ordered tuples of sentences, every number in them the same
    number that sits in the typed fields beside them -- the prose is a reading of the fields,
    never a second source of truth.
    """

    key: str
    setup: str
    side: str
    status: str
    score: int
    grade: str
    entry: float
    entry_label: str
    stop: float
    stop_label: str
    target: float
    target_label: str
    target_2: float | None
    target_2_label: str | None
    risk: float
    reward: float
    rr: float | None
    risk_atr: float | None
    reward_atr: float | None
    thesis: tuple[str, ...]
    invalidation: tuple[str, ...]
    structure: str
    warnings: tuple[str, ...]
    score_breakdown: tuple[ScoreComponent, ...]
    rejection_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "setup": self.setup,
            "side": self.side,
            "status": self.status,
            "score": self.score,
            "grade": self.grade,
            "entry": _f(self.entry),
            "entry_label": self.entry_label,
            "stop": _f(self.stop),
            "stop_label": self.stop_label,
            "target": _f(self.target),
            "target_label": self.target_label,
            "target_2": _f(self.target_2),
            "target_2_label": self.target_2_label,
            "risk": _f(self.risk),
            "reward": _f(self.reward),
            "rr": _f(self.rr),
            "risk_atr": _f(self.risk_atr),
            "reward_atr": _f(self.reward_atr),
            "thesis": list(self.thesis),
            "invalidation": list(self.invalidation),
            "structure": self.structure,
            "warnings": list(self.warnings),
            "score_breakdown": [c.to_dict() for c in self.score_breakdown],
            "rejection_reason": self.rejection_reason,
        }


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """Everything `decide` concluded about one symbol: the opportunities it found, ranked
    (`active` before `watch` before `rejected`, then by score), or the reasons it found none.

    `verdict` and `positioning_direction` are echoed from the `RegimeRow` so a consumer can
    show the regime the suggestions were built under without a second request.
    """

    underlying: str
    filter: str
    spot: float
    atr14: float | None
    as_of: dt.datetime
    effective_at: dt.datetime
    stale: bool
    verdict: str | None
    positioning_direction: str | None
    positioning_ratio: float | None
    opportunities: tuple[Opportunity, ...]
    no_trade_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.underlying,
            "filter": self.filter,
            "spot": _f(self.spot),
            "atr14": _f(self.atr14),
            "as_of": self.as_of.isoformat(),
            "effective_at": self.effective_at.isoformat(),
            "stale": self.stale,
            "verdict": self.verdict,
            "positioning_direction": self.positioning_direction,
            "positioning_ratio": _f(self.positioning_ratio),
            "opportunities": [o.to_dict() for o in self.opportunities],
            "no_trade_reasons": list(self.no_trade_reasons),
        }


# --------------------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------------------

_STATUS_RANK = {"active": 0, "watch": 1, "rejected": 2}


def _grade(score: int) -> str:
    if score >= GRADE_A:
        return "A"
    if score >= GRADE_B:
        return "B"
    if score >= GRADE_C:
        return "C"
    return "D"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _fmt(value: float) -> str:
    """Price/strike formatting for prose: two decimals, thousands separator."""
    return f"{value:,.2f}"


def _gex(value: float) -> str:
    """Gamma in prose, in billions or millions of dollars per 1 % move."""
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"${magnitude / 1e9:,.2f}bn"
    return f"${magnitude / 1e6:,.0f}m"


def _ladder(
    by_strike: Sequence[StrikeGex],
    *,
    start: float,
    end: float,
    reference_abs_gex: float,
    direction: int,
) -> list[StrikeGex]:
    """Strikes strictly between `start` and `end` (walking in `direction`, +1 up / -1 down)
    whose `abs_gex` is at least `ROOM_BEYOND_FRACTION` of `reference_abs_gex`, nearest to
    `start` first. The same "comparable gamma" threshold `app.modules.gex.scan.regime._room_beyond` uses,
    so a target and a room-beyond strike agree on what counts as a level."""
    threshold = ROOM_BEYOND_FRACTION * reference_abs_gex
    if direction > 0:
        rows = [r for r in by_strike if start < r.strike < end and r.abs_gex >= threshold]
        rows.sort(key=lambda r: r.strike)
    else:
        rows = [r for r in by_strike if end < r.strike < start and r.abs_gex >= threshold]
        rows.sort(key=lambda r: -r.strike)
    return rows


def _score(
    *,
    setup: str,
    verdict: str | None,
    ratio: float | None,
    rr: float | None,
    trend_composite: float | None,
    breakout_rate: float | None,
) -> tuple[int, tuple[ScoreComponent, ...]]:
    components: list[ScoreComponent] = []

    if verdict == setup:
        pts, note = _W_REGIME, f"setup matches the {verdict} verdict"
    elif verdict == "mixed":
        pts, note = 20, "verdict is mixed: neither confirms nor contradicts"
    else:
        pts, note = 0, f"setup contradicts the {verdict} verdict"
    components.append(ScoreComponent("regime alignment", pts, _W_REGIME, note))

    if ratio is None:
        pts, note = 0, "positioning ratio unavailable"
    else:
        pts = round(_clamp(ratio * 40.0, 0.0, _W_CONVICTION))
        note = f"|net| is {ratio:.1%} of gross gamma"
    components.append(ScoreComponent("positioning conviction", pts, _W_CONVICTION, note))

    if rr is None:
        pts, note = 0, "reward/risk undefined"
    else:
        pts = round(_clamp(5.0 + (rr - 1.0) * 7.5, 0.0, _W_REWARD_RISK))
        note = f"reward/risk {rr:.2f} (saturates at 3.0)"
    components.append(ScoreComponent("reward/risk", pts, _W_REWARD_RISK, note))

    if trend_composite is None:
        pts, note = 0, "trend composite unavailable (no bars history for this symbol)"
    else:
        c = _clamp(trend_composite, 0.0, 1.0)
        aligned = c if setup == "continuation" else 1.0 - c
        pts = round(aligned * _W_TREND)
        note = (
            f"trend composite at the {c:.0%} percentile "
            + ("favours continuation" if setup == "continuation" else "favours mean reversion")
            if aligned >= 0.5
            else f"trend composite at the {c:.0%} percentile leans against this setup"
        )
    components.append(ScoreComponent("trend context", pts, _W_TREND, note))

    if breakout_rate is None:
        pts, note = 0, "breakout base rate unavailable (fewer than five events in the window)"
    else:
        r = _clamp(breakout_rate, 0.0, 1.0)
        aligned = r if setup == "continuation" else 1.0 - r
        pts = round(aligned * _W_BREAKOUT)
        note = f"{r:.0%} of recent breakouts continued"
    components.append(ScoreComponent("breakout base rate", pts, _W_BREAKOUT, note))

    total = int(_clamp(sum(c.points for c in components), 0, 100))
    return total, tuple(components)


def _vol_clause(iv_rv_ratio: float | None) -> str:
    """How to describe a vol reading that expresses no directional view (T103).

    **"Unavailable" and "in line" are different facts and this said the first when it meant
    the second.** Between `IV_CHEAP_RATIO` and `IV_RICH_RATIO` the ratio is a real measurement
    that happens to be neutral; the fallback text called that "no implied-versus-realized view
    is available", which reads as "this desk cannot see implied vol at all". It can, and does
    -- decision 78 on 2026-09-21 carried a perfectly good IV/RV and still said that, which is
    what the 2026-09-21 review read as evidence that no IV existed anywhere.

    The same distinction T100 enforces on a null aggregate: unmeasured is not flat.
    """
    if iv_rv_ratio is None:
        return "no implied-versus-realized view is available"
    return (
        f"IV/RV {iv_rv_ratio:.2f}: implied is in line with realized, so neither selling nor "
        "buying premium is favoured"
    )


def _structure(setup: str, side: str, iv_rv_ratio: float | None) -> str:
    """A one-line options-structure hint. Reads the IV/RV ratio when there is one; says so
    plainly when there is not -- and, between the thresholds, says the reading is neutral
    rather than missing. See :func:`_vol_clause`."""
    if setup == "pin":
        # Same legs as a fade -- a credit spread with its short strike at the level is the
        # structure either way -- but the reasoning differs enough to be worth saying: a pin
        # is sold because price is expected to stay, not because a level is expected to hold.
        if iv_rv_ratio is not None and iv_rv_ratio >= IV_RICH_RATIO:
            return (
                f"Sell a put credit spread with the short strike at the pinned level (IV/RV "
                f"{iv_rv_ratio:.2f}: implied is rich versus realized, and a pinned strike is "
                "where decay is paid for twice)."
            )
        return (
            "Buy the pinned strike in the underlying with a stop below it, or sell a put "
            "credit spread whose short strike sits on it; the edge is dealer hedging holding "
            f"price near the strike, not a directional view ({_vol_clause(iv_rv_ratio)})."
        )
    if setup == "fade":
        if iv_rv_ratio is not None and iv_rv_ratio >= IV_RICH_RATIO:
            leg = "call credit spread" if side == "SHORT" else "put credit spread"
            return (
                f"Sell a {leg} with the short strike at the wall (IV/RV {iv_rv_ratio:.2f}: "
                "implied is rich versus realized, so collecting premium is paid for)."
            )
        if iv_rv_ratio is not None and iv_rv_ratio <= IV_CHEAP_RATIO:
            leg = "put debit spread" if side == "SHORT" else "call debit spread"
            return (
                f"Buy a {leg} from the wall toward the target (IV/RV {iv_rv_ratio:.2f}: "
                "implied is cheap versus realized, so paying premium is cheap)."
            )
        return (
            "Fade the level in the underlying or CFD, or with a credit spread whose short "
            f"strike sits at the wall; {_vol_clause(iv_rv_ratio)}."
        )
    # continuation
    if iv_rv_ratio is not None and iv_rv_ratio >= IV_RICH_RATIO:
        leg = "call debit spread" if side == "LONG" else "put debit spread"
        return (
            f"Buy a {leg} capped at the target (IV/RV {iv_rv_ratio:.2f}: implied is rich, "
            "so cap the vega paid)."
        )
    leg = "calls" if side == "LONG" else "puts"
    if iv_rv_ratio is not None and iv_rv_ratio <= IV_CHEAP_RATIO:
        return (
            f"Buy outright {leg} or the underlying (IV/RV {iv_rv_ratio:.2f}: implied is "
            "cheap versus realized, so long convexity is paid for)."
        )
    return f"Buy outright {leg} or trade the underlying; {_vol_clause(iv_rv_ratio)}."


def _breakout_lines(breakouts: BreakoutSummary | None, setup: str) -> list[str]:
    """Thesis sentences from the breakout ledger, when one is supplied."""
    if breakouts is None:
        return []
    lines: list[str] = []
    if breakouts.rate is not None:
        lines.append(
            f"Over the last {breakouts.lookback} bars {breakouts.continued} of "
            f"{breakouts.continued + breakouts.failed} resolved range breakouts continued "
            f"({breakouts.rate:.0%})"
            + (
                ", a base rate that supports chasing the move."
                if setup == "continuation" and breakouts.rate >= 0.5
                else ", a base rate that supports fading the level."
                if setup == "fade" and breakouts.rate < 0.5
                else ", a base rate that leans against this setup."
            )
        )
    last = breakouts.last_event
    if last is not None and last.outcome is Outcome.PENDING:
        lines.append(
            f"A {last.direction.value}side range breakout through {_fmt(last.level)} on "
            f"{last.date.isoformat()} is still open ({last.bars_elapsed} bars in)."
        )
    return lines


def _pending_breakout_direction(breakouts: BreakoutSummary | None) -> Direction | None:
    if breakouts is None or breakouts.last_event is None:
        return None
    if breakouts.last_event.outcome is not Outcome.PENDING:
        return None
    return breakouts.last_event.direction


# --------------------------------------------------------------------------------------
# Setups
# --------------------------------------------------------------------------------------


def _fade(
    regime: RegimeRow,
    *,
    wall: WallInfo,
    opposite: WallInfo | None,
    side: str,
    by_strike: Sequence[StrikeGex],
    trend_composite: float | None,
    breakouts: BreakoutSummary | None,
) -> Opportunity | None:
    """A fade at `wall`, or a pin on it. `side` is `SHORT` for the wall above spot, `LONG` for
    the one below. Returns `None` when the wall is out of reach, or when the setup the caller
    asked for contradicts the wall's own gamma -- see below.

    **A wall is named by its net gamma, never by which side of spot it landed on** (T99).
    `app.modules.gex.scan.regime._nearest_walls` deliberately selects the nearest wall below
    and above spot *by position*, because the two walls are not guaranteed to straddle spot;
    in doing so it discards which one was the call wall and which the put wall. Re-deriving
    that identity from `side` -- as this function did until T99 -- renames whichever wall
    happens to sit on the "wrong" side, and produces confident prose asserting the wrong
    structural fact. Six emitted decisions carried that error.

    The four combinations of position and sign, stated exhaustively because collapsing them
    into a single sign assertion is the fix that looks right and is not:

    ============ ========== =================================== ========================
    position     net gamma  dealer hedging into it              emitted as
    ============ ========== =================================== ========================
    above spot   positive   sells strength: resists             ``FADE_CALL_WALL`` (SHORT)
    below spot   negative   buys weakness: resists              ``FADE_PUT_WALL`` (LONG)
    below spot   positive   buys weakness: holds price          ``GAMMA_PIN`` (LONG)
    above spot   negative   chases the move: **amplifies**      nothing
    ============ ========== =================================== ========================

    The last row is suppressed rather than renamed. Every fade here rests on dealers being
    long gamma so that hedging leans against the move; at the most negative-gamma strike in
    the book hedging does the opposite, so the thesis is inverted rather than mislabelled.
    Shorting into it was a real trade error, not a naming one.
    """
    atr = regime.atr14
    assert atr is not None  # gated in `decide`
    spot = regime.spot
    direction = -1 if side == "SHORT" else 1  # direction the trade *profits* in

    if wall.distance_atr is None or wall.distance_atr > WATCH_REACH_ATR:
        return None

    is_call_wall = wall.net_gex > 0
    if side == "SHORT" and not is_call_wall:
        return None  # put wall above spot: the long-gamma rationale is inverted. See docstring.

    is_pin = side == "LONG" and is_call_wall
    if is_pin and wall.distance_atr > PIN_REACH_ATR:
        # A positive-gamma strike this far below spot is a level price has left behind, not a
        # magnet holding it. It is also not a put wall, so there is nothing honest to emit.
        return None

    status = "active" if wall.distance_atr <= FADE_REACH_ATR else "watch"

    entry = wall.strike
    stop = entry - direction * FADE_STOP_BUFFER_ATR * atr
    risk = abs(stop - entry)
    wall_word = "call wall" if is_call_wall else "put wall"
    beyond_word = "above" if side == "SHORT" else "below"
    # The opposite wall's identity is derived the same way, for the same reason.
    opposite_word = (
        ("call wall" if opposite.net_gex > 0 else "put wall")
        if opposite is not None
        else "the opposite side of the book"
    )

    # Target: walk the by-strike ladder from the entry back toward the opposite wall (or, with
    # no opposite wall, toward the far edge of the chain) and take the first strike with
    # comparable gamma that is both outside the noise band and pays MIN_REWARD_RISK.
    far_edge = opposite.strike if opposite is not None else (
        entry + direction * WATCH_REACH_ATR * atr * 10
    )
    candidates = _ladder(
        by_strike, start=entry, end=far_edge, reference_abs_gex=wall.abs_gex, direction=direction
    )
    target: float | None = None
    target_label = ""
    for row in candidates:
        distance = abs(row.strike - entry)
        if distance < MIN_TARGET_ATR * atr:
            continue
        if distance >= MIN_REWARD_RISK * risk:
            target = row.strike
            target_label = (
                f"first strike back toward the {opposite_word} carrying at least "
                f"{ROOM_BEYOND_FRACTION:.0%} of the {'pinned' if is_pin else 'faded'} strike's "
                f"gamma ({_gex(row.abs_gex)})"
            )
            break
    target_2: float | None = None
    target_2_label: str | None = None
    if opposite is not None:
        if target is None:
            target = opposite.strike
            target_label = f"the opposite wall ({opposite_word})"
        elif opposite.strike != target:
            target_2 = opposite.strike
            target_2_label = f"the opposite wall ({opposite_word})"
    if target is None:
        # No opposite wall and no ladder strike: the fade has nowhere computed to aim at.
        # Spot itself is the one honest reference left -- the trade is "back to where it
        # came from", labelled as exactly that.
        if abs(spot - entry) < MIN_TARGET_ATR * atr:
            return None
        target = spot
        target_label = "current spot (no computed level lies between the wall and here)"

    reward = abs(target - entry)
    rr = reward / risk if risk > 0 else None

    score, breakdown = _score(
        # A pin scores in the **fade family**, deliberately, while carrying its own `setup`
        # label on the row below. `_score` compares `setup` against the regime *verdict*, and
        # the verdict a pin occurs under is "fade" -- a long-gamma, range-bound book is the
        # precondition for both. Passing "pin" here would score it as contradicting the very
        # regime that makes it work, and print that contradiction in the breakdown.
        setup="fade",
        verdict=regime.verdict,
        ratio=regime.positioning.ratio,
        rr=rr,
        trend_composite=trend_composite,
        breakout_rate=breakouts.rate if breakouts is not None else None,
    )

    ratio_clause = (
        f" ({regime.positioning.ratio:.0%} of gross)" if regime.positioning.ratio is not None else ""
    )
    if is_pin:
        # A pin is not a fade and must not be described as one: spot is resting *on* the
        # strike, so the claim is that hedging holds it there, not that it turns it away.
        lead = (
            f"Spot is sitting on the largest positive-gamma strike in the book "
            f"({_fmt(wall.strike)}, {_gex(wall.net_gex)} net). Dealers are long "
            f"gamma{ratio_clause}, so hedging sells every move up and buys every move down: "
            f"the strike acts as a magnet, not a boundary."
        )
    else:
        lead = (
            f"Dealers are long gamma{ratio_clause}: hedging sells strength and buys weakness, "
            f"so a move into the {wall_word} at {_fmt(wall.strike)} meets "
            f"{'supply' if side == 'SHORT' else 'demand'}."
        )
    thesis = [
        lead,
        f"The {wall_word} carries {_gex(wall.abs_gex)} of gamma and sits "
        f"{wall.distance_atr:.2f} ATR {beyond_word} spot"
        + (
            f"; the next comparable strike {beyond_word} it is {_fmt(wall.room_beyond_strike)}, "
            f"so a break has {wall.room_beyond / atr:.1f} ATR of open air."
            if wall.room_beyond_strike is not None and wall.room_beyond is not None
            else "; no comparable strike sits beyond it in this chain."
        ),
    ]
    if regime.flip_distance_atr is not None:
        if regime.flip_distance_atr > 0:
            thesis.append(
                f"The gamma flip is {regime.flip_distance_atr:.2f} ATR below spot"
                + (
                    ": the pin holds inside a long-gamma regime."
                    if is_pin
                    else ": a rejection at the wall plays out inside a long-gamma regime."
                    if regime.flip_distance_atr > CONTINUATION_FLIP_ATR
                    else ", close enough that a downside break would flip dealers short."
                )
            )
        else:
            thesis.append(
                f"Spot is {abs(regime.flip_distance_atr):.2f} ATR *below* the gamma flip at "
                f"{_fmt(regime.flip_point)}; net gamma is positive only in aggregate."
            )
    if trend_composite is not None:
        thesis.append(
            f"Trend composite at the {_clamp(trend_composite, 0, 1):.0%} percentile of the "
            f"universe: {'chop, which favours fading levels' if trend_composite < 0.5 else 'trending, which argues against fading'}."
        )
    thesis.extend(_breakout_lines(breakouts, "fade"))

    invalidation = [
        (
            f"A close {beyond_word} {_fmt(stop)} ({wall_word} {'+' if side == 'SHORT' else '-'} "
            f"{FADE_STOP_BUFFER_ATR:.1f} ATR): the strike's gamma has been absorbed."
        ),
        (
            "Net GEX turning negative on the next capture: the fade relies on dealers being "
            "long gamma, and a short-gamma chain chases the move instead of leaning against it."
        ),
        (
            f"The {wall_word} migrating {beyond_word} on the next capture: the level being "
            "faded no longer exists where the entry rests."
        ),
    ]
    if regime.flip_point is not None and side == "LONG" and regime.flip_distance_atr is not None:
        invalidation.append(
            f"Spot trading below the flip at {_fmt(regime.flip_point)}: the demand at the "
            f"{wall_word} is only there while dealers are long gamma above the flip."
        )

    warnings: list[str] = []
    if regime.verdict == "continuation":
        warnings.append(
            "The regime verdict is continuation: this fade runs against the row's own reading."
        )
    pending = _pending_breakout_direction(breakouts)
    if pending is not None and (
        (pending is Direction.UP and side == "SHORT") or (pending is Direction.DOWN and side == "LONG")
    ):
        warnings.append(
            f"An open {pending.value}side breakout is running into this wall; fading it means "
            "betting the breakout fails."
        )
    if regime.iv_rv_ratio is None:
        warnings.append("IV/RV unavailable: no vol view in the structure hint.")

    rejection = None
    if rr is None or rr < MIN_REWARD_RISK:
        status = "rejected"
        rejection = (
            f"reward/risk {rr:.2f} is below the {MIN_REWARD_RISK:.1f} floor: the nearest "
            "computed target does not pay for the stop"
            if rr is not None
            else "reward/risk undefined"
        )

    return Opportunity(
        # Derived from the wall's gamma, never from `side`. See this function's docstring.
        key="GAMMA_PIN" if is_pin else f"FADE_{'CALL' if is_call_wall else 'PUT'}_WALL",
        setup="pin" if is_pin else "fade",
        side=side,
        status=status,
        score=score,
        grade=_grade(score),
        entry=entry,
        entry_label=(
            f"resting limit at the pinned strike ({wall_word})"
            if is_pin
            else f"resting limit at the {wall_word}"
        ),
        stop=stop,
        stop_label=f"{wall_word} {'+' if side == 'SHORT' else '-'} {FADE_STOP_BUFFER_ATR:.1f} ATR",
        target=target,
        target_label=target_label,
        target_2=target_2,
        target_2_label=target_2_label,
        risk=risk,
        reward=reward,
        rr=rr,
        risk_atr=risk / atr,
        reward_atr=reward / atr,
        thesis=tuple(thesis),
        invalidation=tuple(invalidation),
        structure=_structure("pin" if is_pin else "fade", side, regime.iv_rv_ratio),
        warnings=tuple(warnings),
        score_breakdown=breakdown,
        rejection_reason=rejection,
    )


def _continuation(
    regime: RegimeRow,
    *,
    side: str,
    by_strike: Sequence[StrikeGex],
    trend_composite: float | None,
    breakouts: BreakoutSummary | None,
) -> Opportunity:
    """A continuation entry at spot in `side`'s direction."""
    atr = regime.atr14
    assert atr is not None  # gated in `decide`
    spot = regime.spot
    direction = 1 if side == "LONG" else -1
    ahead = regime.wall_above if side == "LONG" else regime.wall_below
    behind = regime.wall_below if side == "LONG" else regime.wall_above
    short_gamma = regime.positioning.direction == "SHORT"

    entry = spot
    entry_label = "current spot; a continuation regime is entered at market, not at a level"

    # Stop: the nearest computed level behind the entry, if one sits within STOP_MAX_ATR,
    # else a volatility stop. The flip counts as "behind" only when it is on the wrong side
    # of the trade -- for a LONG that means below spot.
    level_candidates: list[tuple[float, str]] = []
    if regime.flip_point is not None and (regime.flip_point - spot) * direction < 0:
        level_candidates.append((regime.flip_point, "gamma flip"))
    if behind is not None:
        level_candidates.append((behind.strike, "call wall" if side == "SHORT" else "put wall"))
    level_candidates = [
        (lvl, name) for lvl, name in level_candidates if abs(spot - lvl) <= STOP_MAX_ATR * atr
    ]
    if level_candidates:
        level, name = min(level_candidates, key=lambda pair: abs(spot - pair[0]))
        stop = level - direction * LEVEL_STOP_BUFFER_ATR * atr
        stop_label = f"{name} at {_fmt(level)} {'-' if side == 'LONG' else '+'} {LEVEL_STOP_BUFFER_ATR:.2f} ATR"
    else:
        stop = spot - direction * VOLATILITY_STOP_ATR * atr
        stop_label = f"volatility stop, {VOLATILITY_STOP_ATR:.1f} ATR from entry"
    risk = abs(entry - stop)

    # Target: the wall ahead if it pays, then the comparable strike beyond it, then a plain ATR
    # multiple labelled as such.
    target: float | None = None
    target_label = ""
    target_2: float | None = None
    target_2_label: str | None = None
    ahead_word = "call wall" if side == "LONG" else "put wall"
    if ahead is not None:
        wall_distance = abs(ahead.strike - entry)
        wall_pays = (
            wall_distance >= MIN_TARGET_ATR * atr and wall_distance >= MIN_REWARD_RISK * risk
        )
        if wall_pays:
            target = ahead.strike
            target_label = f"the {ahead_word} ahead ({_gex(ahead.abs_gex)} of gamma)"
            if ahead.room_beyond_strike is not None:
                target_2 = ahead.room_beyond_strike
                target_2_label = f"next comparable strike beyond the {ahead_word}"
        elif ahead.room_beyond_strike is not None:
            # The wall is inside the noise band or does not pay for the stop: aim past it at
            # the next strike with comparable gamma, which `app.modules.gex.scan.regime` already found.
            target = ahead.room_beyond_strike
            target_label = (
                f"next comparable strike beyond the {ahead_word} (the wall itself at "
                f"{_fmt(ahead.strike)} is too close to pay for the stop)"
            )
    if target is None:
        # Nothing computed ahead: fall back to the by-strike ladder past spot, then to an ATR
        # multiple.
        reference = max((r.abs_gex for r in by_strike), default=0.0)
        rows = _ladder(
            by_strike,
            start=entry,
            end=entry + direction * WATCH_REACH_ATR * atr * 10,
            reference_abs_gex=reference,
            direction=direction,
        )
        for row in rows:
            if abs(row.strike - entry) >= max(MIN_TARGET_ATR * atr, MIN_REWARD_RISK * risk):
                target = row.strike
                target_label = f"first strike ahead carrying at least {ROOM_BEYOND_FRACTION:.0%} of the chain's largest gamma"
                break
    if target is None:
        target = entry + direction * TARGET_FALLBACK_ATR * atr
        target_label = f"{TARGET_FALLBACK_ATR:.1f} ATR from entry (no computed level ahead)"

    reward = abs(target - entry)
    rr = reward / risk if risk > 0 else None

    score, breakdown = _score(
        setup="continuation",
        verdict=regime.verdict,
        ratio=regime.positioning.ratio,
        rr=rr,
        trend_composite=trend_composite,
        breakout_rate=breakouts.rate if breakouts is not None else None,
    )

    move_word = "up" if side == "LONG" else "down"
    thesis: list[str] = []
    if short_gamma:
        thesis.append(
            f"Dealers are short gamma ({regime.positioning.ratio:.0%} of gross): hedging buys "
            "strength and sells weakness, so moves extend rather than mean-revert."
            if regime.positioning.ratio is not None
            else "Dealers are short gamma: hedging chases the move."
        )
    else:
        thesis.append(
            f"Dealers are long gamma in aggregate but spot is only "
            f"{abs(regime.flip_distance_atr):.2f} ATR from the flip at {_fmt(regime.flip_point)}: "
            "a push through it turns hedging from pinning to chasing."
            if regime.flip_distance_atr is not None and regime.flip_point is not None
            else "Dealers are long gamma but the regime row reads continuation."
        )
    if regime.return_5d is not None:
        thesis.append(
            f"The last five sessions moved {regime.return_5d:+.2%}; the trade follows that "
            f"direction ({move_word})."
        )
    if ahead is not None and ahead.distance_atr is not None:
        thesis.append(
            f"The {ahead_word} at {_fmt(ahead.strike)} is {ahead.distance_atr:.2f} ATR ahead"
            + (
                f", past the {CONTINUATION_WALL_ATR:.1f}-ATR room-to-run floor."
                if ahead.distance_atr > CONTINUATION_WALL_ATR
                else ", more than an ATR of room before the move meets it."
                if ahead.distance_atr > FADE_WALL_ATR
                else ", close enough that it is the first place the move can stall."
            )
        )
    else:
        thesis.append(f"No wall sits {move_word.replace('up', 'above').replace('down', 'below')} spot in this chain: nothing computed stands in the way.")
    if trend_composite is not None:
        thesis.append(
            f"Trend composite at the {_clamp(trend_composite, 0, 1):.0%} percentile of the "
            f"universe: {'trending, which supports continuation' if trend_composite >= 0.5 else 'choppy, which argues against chasing'}."
        )
    thesis.extend(_breakout_lines(breakouts, "continuation"))

    invalidation = [
        f"A close {'below' if side == 'LONG' else 'above'} {_fmt(stop)} ({stop_label}).",
        (
            "Net GEX turning positive on the next capture with spot above the flip: hedging "
            "flips from chasing to pinning and the continuation thesis is gone."
            if short_gamma
            else f"Spot failing to cross the flip at {_fmt(regime.flip_point)} and net GEX "
            "staying positive: the pinning regime holds and the move stalls."
            if regime.flip_point is not None
            else "Net GEX staying positive with spot well away from the flip: the pinning regime holds."
        ),
        "The five-day return changing sign: the direction the entry follows is no longer there.",
    ]

    warnings: list[str] = []
    if regime.verdict != "continuation":
        warnings.append(
            f"The regime verdict is {regime.verdict}: this continuation runs against the row's own reading."
        )
    pending = _pending_breakout_direction(breakouts)
    if pending is not None and (
        (pending is Direction.UP and side == "SHORT") or (pending is Direction.DOWN and side == "LONG")
    ):
        warnings.append(
            f"An open {pending.value}side breakout points the other way from this entry."
        )
    if regime.iv_rv_ratio is None:
        warnings.append("IV/RV unavailable: no vol view in the structure hint.")
    if "volatility stop" in stop_label:
        warnings.append(
            f"The stop is an ATR multiple, not a computed level: nothing sits within "
            f"{STOP_MAX_ATR:.1f} ATR behind the entry."
        )

    status = "active"
    rejection = None
    if rr is None or rr < MIN_REWARD_RISK:
        status = "rejected"
        rejection = (
            f"reward/risk {rr:.2f} is below the {MIN_REWARD_RISK:.1f} floor"
            if rr is not None
            else "reward/risk undefined"
        )

    return Opportunity(
        key=f"CONTINUATION_{'UP' if side == 'LONG' else 'DOWN'}",
        setup="continuation",
        side=side,
        status=status,
        score=score,
        grade=_grade(score),
        entry=entry,
        entry_label=entry_label,
        stop=stop,
        stop_label=stop_label,
        target=target,
        target_label=target_label,
        target_2=target_2,
        target_2_label=target_2_label,
        risk=risk,
        reward=reward,
        rr=rr,
        risk_atr=risk / atr,
        reward_atr=reward / atr,
        thesis=tuple(thesis),
        invalidation=tuple(invalidation),
        structure=_structure("continuation", side, regime.iv_rv_ratio),
        warnings=tuple(warnings),
        score_breakdown=breakdown,
        rejection_reason=rejection,
    )


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def decide(
    regime: RegimeRow,
    *,
    by_strike: Sequence[StrikeGex] = (),
    trend_composite: float | None = None,
    breakouts: BreakoutSummary | None = None,
) -> DecisionResult:
    """Every opportunity the current inputs support for one symbol, or the reasons for none.

    Args:
        regime: The symbol's `app.modules.gex.scan.regime.RegimeRow` -- spot, ATR, walls, flip, verdict.
        by_strike: The same persisted `gex_by_strike` rows the regime row was computed from,
            for the target ladder. Optional: without them targets fall back to the walls.
        trend_composite: `app.modules.gex.scan.trend.TrendRow.composite` for this symbol, a 0-1 percentile
            across the universe, or `None` when it has no bars history.
        breakouts: `app.modules.gex.scan.breakouts.BreakoutSummary` for this symbol, or `None`.

    Returns:
        A frozen `DecisionResult`. Never raises on the inputs' *content* (a `None` wall, a
        `None` ATR, an empty ladder); only on a malformed `RegimeRow`.
    """
    reasons: list[str] = []

    if regime.stale:
        reasons.append(
            f"chain is {regime.chain_age_minutes:.0f} min stale relative to its trading day's "
            "close: no suggestion is built on a chain that predates the close"
        )
    if regime.positioning.noise_dominated:
        reasons.append(
            "net GEX is noise-dominated: "
            + regime.positioning.description
        )
    if regime.atr14 is None or regime.atr14 <= 0:
        reasons.append("ATR(14) unavailable (insufficient bars history): stops cannot be sized")
    if regime.wall_above is None and regime.wall_below is None:
        reasons.append("no wall on either side of spot in this expiry scope")

    opportunities: list[Opportunity] = []
    if not reasons:
        verdict = regime.verdict
        long_gamma = regime.positioning.direction == "LONG"

        if verdict == "continuation":
            side: str | None = None
            if regime.return_5d is not None and regime.return_5d > 0:
                side = "LONG"
            elif regime.return_5d is not None and regime.return_5d < 0:
                side = "SHORT"
            else:
                # No five-day direction: fall back to an open breakout's direction, the one
                # other directional fact the inputs carry.
                pending = _pending_breakout_direction(breakouts)
                if pending is Direction.UP:
                    side = "LONG"
                elif pending is Direction.DOWN:
                    side = "SHORT"
            if side is None:
                reasons.append(
                    "continuation regime but no direction to follow: the five-day return is "
                    "flat or unavailable and no breakout is open"
                )
            else:
                opportunities.append(
                    _continuation(
                        regime,
                        side=side,
                        by_strike=by_strike,
                        trend_composite=trend_composite,
                        breakouts=breakouts,
                    )
                )
        elif long_gamma:
            # fade or mixed under long gamma: one fade per wall within reach.
            for wall, opposite, side in (
                (regime.wall_above, regime.wall_below, "SHORT"),
                (regime.wall_below, regime.wall_above, "LONG"),
            ):
                if wall is None:
                    continue
                opp = _fade(
                    regime,
                    wall=wall,
                    opposite=opposite,
                    side=side,
                    by_strike=by_strike,
                    trend_composite=trend_composite,
                    breakouts=breakouts,
                )
                if opp is not None:
                    opportunities.append(opp)
            if not opportunities:
                nearest = min(
                    (
                        w.distance_atr
                        for w in (regime.wall_above, regime.wall_below)
                        if w is not None and w.distance_atr is not None
                    ),
                    default=None,
                )
                reasons.append(
                    f"long gamma but the nearest wall is {nearest:.1f} ATR away, beyond the "
                    f"{WATCH_REACH_ATR:.1f}-ATR watch horizon"
                    if nearest is not None
                    else "long gamma but no wall distance is available"
                )
        else:
            # Short gamma always reads `continuation` in `app.modules.gex.scan.regime._verdict`; a `None`
            # verdict that is neither stale nor noise-dominated cannot be produced by that
            # module today. Named rather than silently skipped, so a future verdict value
            # does not become an empty row with no explanation.
            reasons.append(
                f"no setup is defined for verdict {verdict!r} under "
                f"{regime.positioning.direction!r} positioning"
            )

    opportunities.sort(key=lambda o: (_STATUS_RANK[o.status], -o.score, o.key))

    return DecisionResult(
        underlying=regime.underlying,
        filter=regime.filter,
        spot=regime.spot,
        atr14=regime.atr14,
        as_of=regime.as_of,
        effective_at=regime.effective_at,
        stale=regime.stale,
        verdict=regime.verdict,
        positioning_direction=regime.positioning.direction,
        positioning_ratio=regime.positioning.ratio,
        opportunities=tuple(opportunities),
        no_trade_reasons=tuple(reasons),
    )
