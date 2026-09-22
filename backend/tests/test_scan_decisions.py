"""Tests for `app/modules/gex/scan/decisions.py` (T60). Fully offline: every test hand-builds a
`RegimeRow` through `compute_regime_row` from a `KeyLevels`/`StrikeGex` fixture -- no database,
no filesystem, no network -- in keeping with the module's purity contract.

Geometry used throughout unless a test says otherwise: spot 100, ATR 5, so one ATR is 5.00 and
every distance below reads directly in ATR by dividing by five.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.modules.gex.gex.engine import KeyLevels, StrikeGex
from app.modules.gex.scan.breakouts import BreakoutEvent, BreakoutSummary, Direction, Outcome
from app.modules.gex.scan.decisions import (
    FADE_REACH_ATR,
    FADE_STOP_BUFFER_ATR,
    LEVEL_STOP_BUFFER_ATR,
    MIN_REWARD_RISK,
    TARGET_FALLBACK_ATR,
    VOLATILITY_STOP_ATR,
    WATCH_REACH_ATR,
    decide,
)
from app.modules.gex.scan.regime import compute_regime_row

_NOW = dt.datetime(2026, 9, 9, 20, 15, tzinfo=dt.UTC)


def _levels(**overrides) -> KeyLevels:
    defaults = {
        "net_gex": 5.0e9,
        "call_gex": 12.0e9,
        "put_gex": -7.0e9,
        "abs_gex": 19.0e9,
        "call_wall": 104.0,
        "call_wall_gex": 8.0e9,
        "put_wall": 96.0,
        "put_wall_gex": -6.0e9,
        "max_abs_strike": 104.0,
        "max_abs_gex": 8.0e9,
        "max_net_strike": 104.0,
        "min_net_strike": 96.0,
        "flip_point": 90.0,
        "spot": 100.0,
        "computed_at": _NOW,
    }
    defaults.update(overrides)
    return KeyLevels(**defaults)


def _strike(strike: float, call_gex: float, put_gex: float) -> StrikeGex:
    return StrikeGex(
        strike=strike,
        call_gex=call_gex,
        put_gex=put_gex,
        net_gex=call_gex + put_gex,
        abs_gex=call_gex - put_gex,
    )


_DEFAULT_STRIKES = [_strike(96.0, 0.0, -6.0e9), _strike(104.0, 8.0e9, 0.0)]


def _regime(
    *,
    levels: KeyLevels | None = None,
    by_strike: list[StrikeGex] | None = None,
    spot: float = 100.0,
    atr14: float | None = 5.0,
    iv30: float | None = None,
    rv20: float | None = None,
    return_5d: float | None = None,
    stale: bool = False,
    chain_age_minutes: float = 0.0,
):
    return compute_regime_row(
        underlying="TEST",
        filter_="ALL",
        levels=levels if levels is not None else _levels(),
        by_strike=by_strike if by_strike is not None else _DEFAULT_STRIKES,
        zero_dte_by_strike=[],
        spot=spot,
        atr14=atr14,
        iv30=iv30,
        rv20=rv20,
        return_5d=return_5d,
        as_of=_NOW,
        effective_at=_NOW,
        chain_age_minutes=chain_age_minutes,
        stale=stale,
    )


def _summary(rate: float | None, *, pending: Direction | None = None, events: int = 10) -> BreakoutSummary:
    last = None
    if pending is not None:
        last = BreakoutEvent(
            date=dt.date(2026, 9, 8),
            direction=pending,
            level=101.0,
            close=102.0,
            outcome=Outcome.PENDING,
            resolved_at=None,
            bars_elapsed=2,
            follow_through_atr=None,
            excursion_atr=0.3,
            mfe_atr=0.4,
            mae_atr=-0.1,
        )
    continued = 0 if rate is None else round(rate * events)
    return BreakoutSummary(
        lookback=126,
        events=events,
        continued=continued,
        failed=events - continued,
        pending=0 if pending is None else 1,
        rate=rate,
        mean_follow_through_atr=None,
        last_event=last,
    )


# --------------------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------------------


def test_score_weights_sum_to_one_hundred():
    result = decide(_regime())
    opp = result.opportunities[0]
    assert sum(c.max_points for c in opp.score_breakdown) == 100


def test_result_is_json_serializable_and_reasons_are_exclusive_with_opportunities():
    for regime in (_regime(), _regime(stale=True, chain_age_minutes=120.0)):
        result = decide(regime)
        json.dumps(result.to_dict())
        assert bool(result.opportunities) != bool(result.no_trade_reasons)


# --------------------------------------------------------------------------------------
# Fade
# --------------------------------------------------------------------------------------


def test_fade_regime_emits_one_fade_per_wall_with_levels_on_the_right_side():
    """Walls 0.8 ATR either side of spot, flip 2 ATR below: the regime verdict is `fade`, and
    the engine rests one short at the call wall and one long at the put wall."""
    result = decide(_regime())
    assert result.verdict == "fade"
    keys = {o.key: o for o in result.opportunities}
    assert set(keys) == {"FADE_CALL_WALL", "FADE_PUT_WALL"}

    short = keys["FADE_CALL_WALL"]
    assert short.side == "SHORT"
    assert short.status == "active"
    assert short.entry == 104.0
    assert short.stop == pytest.approx(104.0 + FADE_STOP_BUFFER_ATR * 5.0)
    # No intermediate ladder strike between the walls -> target is the opposite wall.
    assert short.target == 96.0
    assert short.target_2 is None
    assert short.stop > short.entry > short.target
    assert short.rr == pytest.approx(8.0 / 2.5)

    long = keys["FADE_PUT_WALL"]
    assert long.side == "LONG"
    assert long.entry == 96.0
    assert long.stop == pytest.approx(96.0 - FADE_STOP_BUFFER_ATR * 5.0)
    assert long.target == 104.0
    assert long.stop < long.entry < long.target


def test_fade_thesis_and_invalidation_name_the_computed_numbers():
    result = decide(_regime())
    short = next(o for o in result.opportunities if o.key == "FADE_CALL_WALL")
    text = " ".join(short.thesis)
    assert "long gamma" in text
    assert "104.00" in text  # the wall
    assert "0.80 ATR" in text  # its distance
    assert "flip" in text.lower()
    inv = " ".join(short.invalidation)
    assert "106.50" in inv  # the stop
    assert "Net GEX turning negative" in inv
    assert "migrating" in inv
    assert short.rejection_reason is None


def test_fade_target_walks_the_ladder_and_keeps_the_opposite_wall_as_target_2():
    """A strike at 100 carrying 25 % of the call wall's gamma is the first level back toward
    the put wall that pays the 2.5-point stop -> primary target, with 96 as the extension."""
    strikes = [*_DEFAULT_STRIKES, _strike(100.0, 2.0e9, 0.0)]
    result = decide(_regime(by_strike=strikes), by_strike=strikes)
    short = next(o for o in result.opportunities if o.key == "FADE_CALL_WALL")
    assert short.target == 100.0
    assert short.target_2 == 96.0
    assert short.rr == pytest.approx(4.0 / 2.5)
    assert "25%" in short.target_label


def test_fade_ladder_skips_a_strike_that_does_not_pay_for_the_stop():
    """A big strike at 103 is only 1 point from the 104 entry against a 2.5-point stop: it is
    skipped, and the next qualifying level (100) is taken instead."""
    strikes = [*_DEFAULT_STRIKES, _strike(103.0, 5.0e9, 0.0), _strike(100.0, 2.0e9, 0.0)]
    result = decide(_regime(by_strike=strikes), by_strike=strikes)
    short = next(o for o in result.opportunities if o.key == "FADE_CALL_WALL")
    assert short.target == 100.0


def test_fade_wall_between_reach_and_watch_horizon_is_a_watch():
    """Call wall 2 ATR above spot: past `FADE_REACH_ATR`, inside `WATCH_REACH_ATR`."""
    assert FADE_REACH_ATR < 2.0 < WATCH_REACH_ATR
    levels = _levels(call_wall=110.0, max_abs_strike=110.0, max_net_strike=110.0)
    strikes = [_strike(96.0, 0.0, -6.0e9), _strike(110.0, 8.0e9, 0.0)]
    result = decide(_regime(levels=levels, by_strike=strikes))
    short = next(o for o in result.opportunities if o.key == "FADE_CALL_WALL")
    assert short.status == "watch"
    long = next(o for o in result.opportunities if o.key == "FADE_PUT_WALL")
    assert long.status == "active"
    # Ranking: active first.
    assert result.opportunities[0].key == "FADE_PUT_WALL"


def test_fade_walls_beyond_the_watch_horizon_yield_no_trade_with_a_reason():
    levels = _levels(
        call_wall=120.0, put_wall=80.0, max_abs_strike=120.0, max_net_strike=120.0,
        min_net_strike=80.0,
    )
    strikes = [_strike(80.0, 0.0, -6.0e9), _strike(120.0, 8.0e9, 0.0)]
    result = decide(_regime(levels=levels, by_strike=strikes))
    assert result.opportunities == ()
    assert any("watch horizon" in r for r in result.no_trade_reasons)


def test_fade_under_mixed_verdict_scores_partial_alignment_and_no_warning():
    """Flip only 0.6 ATR below spot fails fade's `> 1 ATR` gate, so the verdict is `mixed`;
    fades are still emitted, with 20 of 35 alignment points and no contradiction warning."""
    levels = _levels(flip_point=97.0)
    result = decide(_regime(levels=levels))
    assert result.verdict == "mixed"
    short = next(o for o in result.opportunities if o.key == "FADE_CALL_WALL")
    alignment = next(c for c in short.score_breakdown if c.name == "regime alignment")
    assert alignment.points == 20
    assert not any("verdict" in w for w in short.warnings)


def test_fade_warns_when_an_open_breakout_runs_into_the_faded_wall():
    result = decide(_regime(), breakouts=_summary(0.4, pending=Direction.UP))
    short = next(o for o in result.opportunities if o.key == "FADE_CALL_WALL")
    long = next(o for o in result.opportunities if o.key == "FADE_PUT_WALL")
    assert any("upside breakout" in w for w in short.warnings)
    assert not any("breakout" in w for w in long.warnings)
    assert any("still open" in t for t in short.thesis)


def test_fade_structure_reads_the_iv_rv_ratio():
    rich = decide(_regime(iv30=0.30, rv20=0.20))
    cheap = decide(_regime(iv30=0.15, rv20=0.20))
    none = decide(_regime())
    short_rich = next(o for o in rich.opportunities if o.key == "FADE_CALL_WALL")
    short_cheap = next(o for o in cheap.opportunities if o.key == "FADE_CALL_WALL")
    short_none = next(o for o in none.opportunities if o.key == "FADE_CALL_WALL")
    assert "call credit spread" in short_rich.structure
    assert "put debit spread" in short_cheap.structure
    assert "no implied-versus-realized view" in short_none.structure
    assert any("IV/RV unavailable" in w for w in short_none.warnings)
    assert not any("IV/RV unavailable" in w for w in short_rich.warnings)


# --------------------------------------------------------------------------------------
# Continuation
# --------------------------------------------------------------------------------------


def _short_gamma_levels(**overrides) -> KeyLevels:
    """Net negative: `app.modules.gex.scan.regime` reads this as SHORT positioning -> continuation."""
    base = {
        "net_gex": -5.0e9,
        "call_gex": 7.0e9,
        "put_gex": -12.0e9,
        "abs_gex": 19.0e9,
        "call_wall": 106.0,
        "call_wall_gex": 6.0e9,
        "put_wall": 99.0,
        "put_wall_gex": -8.0e9,
        "max_abs_strike": 99.0,
        "max_abs_gex": 8.0e9,
        "max_net_strike": 106.0,
        "min_net_strike": 99.0,
        "flip_point": 110.0,
    }
    base.update(overrides)
    return _levels(**base)


_SHORT_GAMMA_STRIKES = [_strike(99.0, 0.0, -8.0e9), _strike(106.0, 6.0e9, 0.0)]


def test_short_gamma_continuation_follows_the_five_day_move_with_a_level_stop():
    """Spot 100, put wall 99 (0.2 ATR behind a long), call wall 106 ahead: the stop is the put
    wall less a quarter-ATR buffer and the target is the call wall."""
    result = decide(
        _regime(levels=_short_gamma_levels(), by_strike=_SHORT_GAMMA_STRIKES, return_5d=0.03)
    )
    assert result.verdict == "continuation"
    assert len(result.opportunities) == 1
    opp = result.opportunities[0]
    assert opp.key == "CONTINUATION_UP"
    assert opp.side == "LONG"
    assert opp.status == "active"
    assert opp.entry == 100.0
    assert opp.stop == pytest.approx(99.0 - LEVEL_STOP_BUFFER_ATR * 5.0)
    assert "put wall" in opp.stop_label
    assert opp.target == 106.0
    assert opp.stop < opp.entry < opp.target
    assert opp.rr == pytest.approx(6.0 / 2.25)
    assert any("short gamma" in t for t in opp.thesis)
    assert any("+3.00%" in t for t in opp.thesis)
    assert any("Net GEX turning positive" in i for i in opp.invalidation)


def test_short_gamma_continuation_down_mirrors_the_geometry():
    result = decide(
        _regime(levels=_short_gamma_levels(), by_strike=_SHORT_GAMMA_STRIKES, return_5d=-0.02)
    )
    opp = result.opportunities[0]
    assert opp.key == "CONTINUATION_DOWN"
    assert opp.side == "SHORT"
    # Behind a short is the call wall at 106: 1.2 ATR, within STOP_MAX_ATR.
    assert opp.stop == pytest.approx(106.0 + LEVEL_STOP_BUFFER_ATR * 5.0)
    # Ahead is the put wall at 99 -- only 1 point against a 7.25 stop, so it does not pay and
    # there is no comparable strike beyond it: the fallback ATR target applies.
    assert opp.target == pytest.approx(100.0 - TARGET_FALLBACK_ATR * 5.0)
    assert "no computed level ahead" in opp.target_label
    assert opp.stop > opp.entry > opp.target


def test_continuation_uses_a_volatility_stop_when_no_level_is_close_behind():
    levels = _short_gamma_levels(put_wall=80.0, min_net_strike=80.0, max_abs_strike=80.0)
    strikes = [_strike(80.0, 0.0, -8.0e9), _strike(106.0, 6.0e9, 0.0)]
    result = decide(_regime(levels=levels, by_strike=strikes, return_5d=0.01))
    opp = result.opportunities[0]
    assert opp.stop == pytest.approx(100.0 - VOLATILITY_STOP_ATR * 5.0)
    assert "volatility stop" in opp.stop_label
    assert any("ATR multiple" in w for w in opp.warnings)


def test_continuation_target_prefers_the_strike_beyond_a_wall_that_is_too_close():
    """Call wall at 101 (does not pay) with a comparable strike at 108 beyond it."""
    levels = _short_gamma_levels(call_wall=101.0, max_net_strike=101.0)
    strikes = [_strike(99.0, 0.0, -8.0e9), _strike(101.0, 6.0e9, 0.0), _strike(108.0, 3.0e9, 0.0)]
    result = decide(_regime(levels=levels, by_strike=strikes, return_5d=0.01), by_strike=strikes)
    opp = result.opportunities[0]
    assert opp.target == 108.0
    assert "too close" in opp.target_label


def test_long_gamma_continuation_near_the_flip_stops_at_the_flip():
    """Long gamma, flip 0.2 ATR below spot, call wall 2.4 ATR above with a +5d move: the
    regime verdict is `continuation` and the flip is the natural stop."""
    levels = _levels(flip_point=99.0, call_wall=112.0, max_abs_strike=112.0, max_net_strike=112.0)
    strikes = [_strike(96.0, 0.0, -6.0e9), _strike(112.0, 8.0e9, 0.0)]
    result = decide(_regime(levels=levels, by_strike=strikes, return_5d=0.02))
    assert result.verdict == "continuation"
    opp = result.opportunities[0]
    assert opp.key == "CONTINUATION_UP"
    assert "gamma flip" in opp.stop_label
    assert opp.stop == pytest.approx(99.0 - LEVEL_STOP_BUFFER_ATR * 5.0)
    assert opp.target == 112.0
    assert any("long gamma in aggregate" in t for t in opp.thesis)


def test_continuation_with_no_direction_is_a_named_no_trade():
    result = decide(_regime(levels=_short_gamma_levels(), by_strike=_SHORT_GAMMA_STRIKES))
    assert result.opportunities == ()
    assert any("no direction to follow" in r for r in result.no_trade_reasons)


def test_continuation_falls_back_to_an_open_breakout_for_direction():
    result = decide(
        _regime(levels=_short_gamma_levels(), by_strike=_SHORT_GAMMA_STRIKES),
        breakouts=_summary(0.7, pending=Direction.DOWN),
    )
    assert result.opportunities[0].key == "CONTINUATION_DOWN"


def test_continuation_score_reads_trend_and_breakout_rate():
    strong = decide(
        _regime(levels=_short_gamma_levels(), by_strike=_SHORT_GAMMA_STRIKES, return_5d=0.03),
        trend_composite=1.0,
        breakouts=_summary(1.0),
    )
    weak = decide(
        _regime(levels=_short_gamma_levels(), by_strike=_SHORT_GAMMA_STRIKES, return_5d=0.03),
        trend_composite=0.0,
        breakouts=_summary(0.0),
    )
    unknown = decide(
        _regime(levels=_short_gamma_levels(), by_strike=_SHORT_GAMMA_STRIKES, return_5d=0.03)
    )
    s, w, u = (r.opportunities[0] for r in (strong, weak, unknown))
    assert s.score - w.score == 25  # 15 trend + 10 breakout
    assert u.score == w.score  # unavailable scores exactly like adverse: 0 points
    unavailable = [c for c in u.score_breakdown if "unavailable" in c.note]
    assert {c.name for c in unavailable} == {"trend context", "breakout base rate"}
    assert s.grade in {"A", "B"}


# --------------------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------------------


def test_stale_chain_yields_no_trade():
    result = decide(_regime(stale=True, chain_age_minutes=95.0))
    assert result.opportunities == ()
    assert any("stale" in r for r in result.no_trade_reasons)


def test_noise_dominated_positioning_yields_no_trade():
    levels = _levels(net_gex=0.1e9)  # 0.5 % of gross, well under the floor
    result = decide(_regime(levels=levels))
    assert result.opportunities == ()
    assert any("noise-dominated" in r for r in result.no_trade_reasons)


def test_missing_atr_yields_no_trade():
    result = decide(_regime(atr14=None))
    assert result.opportunities == ()
    assert any("ATR" in r for r in result.no_trade_reasons)


def test_rejected_geometry_is_emitted_last_with_its_reason():
    """Put wall 3 points below the call wall: fading either wall aims at the other, 3 points
    of reward against a 2.5-point stop -- pays. Shrink the gap to 2 and it no longer does."""
    levels = _levels(call_wall=101.0, put_wall=99.0, max_net_strike=101.0, min_net_strike=99.0,
                     max_abs_strike=101.0)
    strikes = [_strike(99.0, 0.0, -6.0e9), _strike(101.0, 8.0e9, 0.0)]
    result = decide(_regime(levels=levels, by_strike=strikes))
    assert result.opportunities
    for opp in result.opportunities:
        assert opp.status == "rejected"
        assert opp.rr is not None and opp.rr < MIN_REWARD_RISK
        assert "below" in (opp.rejection_reason or "")


# ---------------------------------------------------------------------------------------
# T99: a wall is named by its gamma, never by its position relative to spot.
#
# The six rows below are the real mislabeled decisions found in `gex.decisions` on
# 2026-09-21, reproduced as literals. They are literals on purpose: the query that found
# them joins `gex.decisions` to `gex.gex_levels`, and T99 changes the levels side of that
# join, so a fixture that re-derived its expectations from live data would quietly stop
# finding them. See plans/desk-integrity/00-wall-identity.md, decision 4.
# ---------------------------------------------------------------------------------------

#: `(decision id, underlying, spot, strike, net gamma at strike, expected key)`.
#: `None` means the setup must not be emitted at all.
_T99_MISLABELED = [
    (23, "SLV", 57.44, 58.0, -7.5e6, None),
    (32, "XLE", 65.12, 65.0, +51.5e6, "GAMMA_PIN"),
    (50, "QQQ", 722.0, 720.0, +715e6, "GAMMA_PIN"),
    (77, "SPY", 773.2, 772.0, +1.39e9, "GAMMA_PIN"),
    (78, "QQQ", 741.0, 740.0, +662e6, "GAMMA_PIN"),
    (80, "DIA", 519.9, 516.0, +41.4e6, "GAMMA_PIN"),
]


def _decide_one_wall(*, spot, strike, net_gex, atr, opposite_strike, opposite_net_gex,
                     extra=()):
    """A long-gamma book positioned as the real row was.

    `extra` adds `(strike, net_gex)` pairs. A two-strike book is enough to decide a setup's
    *name*, but not always to give it a target: `_fade` walks the by-strike ladder looking for
    somewhere to aim, and returns `None` when nothing qualifies. The real QQQ book that
    produced decision 78 has strikes in between; a synthetic one needs them supplied.
    """
    def as_strike(k, net):
        return _strike(k, net, 0.0) if net >= 0 else _strike(k, 0.0, net)

    strikes = sorted(
        [as_strike(strike, net_gex), as_strike(opposite_strike, opposite_net_gex)]
        + [as_strike(k, n) for k, n in extra],
        key=lambda s: s.strike,
    )
    hi = max(strikes, key=lambda s: s.net_gex)
    lo = min(strikes, key=lambda s: s.net_gex)
    levels = _levels(
        net_gex=abs(net_gex) + abs(opposite_net_gex),
        abs_gex=abs(net_gex) + abs(opposite_net_gex),
        call_wall=hi.strike if hi.net_gex > 0 else None,
        call_wall_gex=hi.net_gex if hi.net_gex > 0 else None,
        put_wall=lo.strike if lo.net_gex < 0 else None,
        put_wall_gex=lo.net_gex if lo.net_gex < 0 else None,
        max_net_strike=hi.strike,
        min_net_strike=lo.strike,
        max_abs_strike=max(strikes, key=lambda s: s.abs_gex).strike,
        max_abs_gex=max(s.abs_gex for s in strikes),
        flip_point=spot - 4 * atr,
        spot=spot,
    )
    regime = _regime(levels=levels, by_strike=strikes, spot=spot, atr14=atr)
    # `decide` takes the by-strike rows separately; without them the target ladder is empty
    # and a setup with no opposite wall has nowhere to aim.
    return decide(regime, by_strike=strikes)


def _decide_id78():
    """Decision 78 (QQQ, 2026-09-21) as captured: spot 741.0, call wall 740 at +662mn, put wall
    700 at -425mn -- **both below spot**, which is the whole reason the row was mislabeled."""
    return _decide_one_wall(
        spot=741.0, strike=740.0, net_gex=662e6, atr=7.4,
        opposite_strike=700.0, opposite_net_gex=-425e6,
        extra=[(760.0, 400e6)],
    )


@pytest.mark.parametrize(
    ("decision_id", "symbol", "spot", "strike", "net_gex", "expected"),
    _T99_MISLABELED,
    ids=[f"id{row[0]}-{row[1]}" for row in _T99_MISLABELED],
)
def test_t99_mislabeled_decisions_are_named_from_gamma(
    decision_id, symbol, spot, strike, net_gex, expected
):
    """Each of the six historically mislabeled rows now gets the right key, or none at all.

    Every one of them was emitted as a fade whose name came from the wall's position. Five
    were sound trades with the wrong noun; SLV id 23 shorted into the most negative-gamma
    strike in the book while claiming hedging would sell strength there, and is suppressed.
    """
    atr = max(spot * 0.01, 0.05)
    # The opposite wall sits far enough away not to be emitted itself.
    opposite_strike = strike + (6 * atr if strike < spot else -6 * atr)
    keys = {
        o.key
        for o in _decide_one_wall(
            spot=spot, strike=strike, net_gex=net_gex, atr=atr,
            opposite_strike=opposite_strike, opposite_net_gex=-net_gex,
        ).opportunities
    }
    if expected is None:
        assert "FADE_CALL_WALL" not in keys and "FADE_PUT_WALL" not in keys, (
            f"decision {decision_id} ({symbol}): a put wall above spot was emitted as a fade; "
            "dealer hedging amplifies there, so the long-gamma rationale is inverted"
        )
        assert "GAMMA_PIN" not in keys
    else:
        assert expected in keys, (
            f"decision {decision_id} ({symbol}): expected {expected}, got {sorted(keys) or 'none'}"
        )
        assert "FADE_PUT_WALL" not in keys, (
            f"decision {decision_id} ({symbol}): a +{net_gex:.3g} strike was named a put wall"
        )


def test_t99_classic_fades_are_unchanged():
    """The thirteen correctly-resolved rows are the regression risk. Default geometry is the
    classic shape -- call wall above spot, put wall below -- and must be untouched."""
    result = decide(_regime())
    keys = {o.key for o in result.opportunities}
    assert keys == {"FADE_CALL_WALL", "FADE_PUT_WALL"}
    for opp in result.opportunities:
        if opp.key == "FADE_CALL_WALL":
            assert opp.side == "SHORT" and opp.entry == 104.0
        else:
            assert opp.side == "LONG" and opp.entry == 96.0


def test_t99_pin_prose_describes_a_magnet_not_a_fade():
    """F2: the thesis interpolated the wall's name, so the prose asserted the wrong structural
    fact. It must now name the call wall and describe the pin."""
    pin = next(o for o in _decide_id78().opportunities if o.key == "GAMMA_PIN")
    blob = " ".join(pin.thesis).lower()
    assert "put wall at 740" not in blob
    assert "magnet" in blob and "largest positive-gamma strike" in blob
    assert "call wall" in " ".join(pin.thesis[1:]).lower()
    # Nothing anywhere in the row may still call this a put wall.
    everything = json.dumps(pin.to_dict()).lower()
    assert "put wall" not in everything


def test_t99_put_wall_above_spot_is_suppressed_not_renamed():
    """The SLV id 23 shape in isolation: both walls above spot, so the put wall is the nearer
    one. Emitting a short there asserts resistance at the strike where hedging amplifies."""
    strikes = [_strike(96.0, 0.0, -6.0e9), _strike(104.0, 8.0e9, 0.0)]
    levels = _levels(call_wall=104.0, call_wall_gex=8.0e9, put_wall=96.0, put_wall_gex=-6.0e9,
                     max_net_strike=104.0, min_net_strike=96.0)
    # Spot below both walls: `_nearest_walls` hands the put wall up as `wall_above`.
    result = decide(_regime(levels=levels, by_strike=strikes, spot=95.0))
    for opp in result.opportunities:
        assert not (opp.side == "SHORT" and opp.key.startswith("FADE")), (
            "a short fade was emitted at a negative-gamma strike above spot"
        )


def test_t99_straddled_walls_still_select_by_position():
    """The case the review's proposed fix would have broken: both walls on the same side of
    spot. `_nearest_walls` must keep picking by position -- this must not crash or go empty."""
    strikes = [_strike(96.0, 0.0, -6.0e9), _strike(98.0, 0.0, -4.0e9)]
    levels = _levels(call_wall=None, call_wall_gex=None, put_wall=96.0, put_wall_gex=-6.0e9,
                     max_net_strike=98.0, min_net_strike=96.0, max_abs_strike=96.0,
                     max_abs_gex=6.0e9, net_gex=5.0e9)
    result = decide(_regime(levels=levels, by_strike=strikes, spot=100.0))
    assert result.no_trade_reasons or result.opportunities  # answered either way, never crashed


def test_t99_pin_requires_proximity():
    """A positive-gamma strike well below spot is a level price left behind, not a magnet."""
    far = _decide_one_wall(
        spot=741.0, strike=700.0, net_gex=662e6, atr=7.4,
        opposite_strike=780.0, opposite_net_gex=-425e6,
    )
    assert "GAMMA_PIN" not in {o.key for o in far.opportunities}


def test_t99_gamma_pin_reaches_the_track_record():
    """A new key that is emitted but never scored is half a decision. Nothing downstream may
    enumerate keys -- the pin must survive serialisation like any other row."""
    pin = next(o for o in _decide_id78().opportunities if o.key == "GAMMA_PIN")
    row = pin.to_dict()
    assert row["key"] == "GAMMA_PIN"
    assert row["setup"] == "pin"
    assert row["side"] == "LONG"
    assert row["entry"] == 740.0
    assert row["stop"] is not None and row["stop"] < row["entry"]
    json.dumps(row)  # the decisions job persists this verbatim


def test_t100_null_aggregate_yields_no_positioning_and_no_verdict():
    """The consumer that mattered: `compute_regime_row` passes the aggregates straight into
    `dealer_positioning`. With a null net it must report NO DATA and decline a verdict, rather
    than deriving "flat" from a zero that was never measured (T100)."""
    levels = _levels(
        net_gex=None, call_gex=None, put_gex=None, abs_gex=None,
        call_wall=None, call_wall_gex=None, put_wall=None, put_wall_gex=None,
        max_abs_strike=None, max_net_strike=None, min_net_strike=None, flip_point=None,
    )
    row = _regime(levels=levels, by_strike=[])
    assert row.positioning.label == "NO DATA"
    assert row.positioning.direction is None
    assert row.positioning.ratio is None
    assert row.positioning.net_gex is None
    assert row.verdict is None
    result = decide(row)
    assert result.opportunities == ()
    assert result.no_trade_reasons  # says why, rather than silently emitting nothing


def test_t103_neutral_vol_is_not_reported_as_missing():
    """T103: "no implied-versus-realized view is available" used to fire whenever the ratio sat
    *between* the rich and cheap thresholds -- reporting a real, neutral measurement as a
    missing one. The 2026-09-21 review read that string as evidence the desk had no implied
    vol at all; it had it and was using it (decision 50 carried IV/RV 1.27).

    The same distinction T100 enforces on a null aggregate: unmeasured is not flat."""
    from app.modules.gex.scan.decisions import _structure

    missing = _structure("fade", "SHORT", None)
    neutral = _structure("fade", "SHORT", 1.00)

    assert "no implied-versus-realized view is available" in missing
    assert "no implied-versus-realized view is available" not in neutral
    assert "1.00" in neutral and "in line with realized" in neutral

    # Both continuation and pin fallbacks share the clause, so they cannot drift apart.
    for setup, side in (("continuation", "LONG"), ("pin", "LONG")):
        assert "in line with realized" in _structure(setup, side, 1.00)
        assert "no implied-versus-realized view is available" in _structure(setup, side, None)


def test_t103_rich_and_cheap_readings_are_unchanged():
    """The two branches that already worked must not move: they are what the structure hint is
    for, and decision 50's IV/RV 1.27 read is the evidence they work."""
    from app.modules.gex.scan.decisions import _structure

    rich = _structure("fade", "SHORT", 1.27)
    cheap = _structure("fade", "SHORT", 0.80)
    assert "rich versus realized" in rich and "credit spread" in rich
    assert "cheap versus realized" in cheap and "debit spread" in cheap
