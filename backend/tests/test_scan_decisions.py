"""Tests for `app/scan/decisions.py` (T60). Fully offline: every test hand-builds a
`RegimeRow` through `compute_regime_row` from a `KeyLevels`/`StrikeGex` fixture -- no database,
no filesystem, no network -- in keeping with the module's purity contract.

Geometry used throughout unless a test says otherwise: spot 100, ATR 5, so one ATR is 5.00 and
every distance below reads directly in ATR by dividing by five.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.gex.engine import KeyLevels, StrikeGex
from app.scan.breakouts import BreakoutEvent, BreakoutSummary, Direction, Outcome
from app.scan.decisions import (
    FADE_REACH_ATR,
    FADE_STOP_BUFFER_ATR,
    LEVEL_STOP_BUFFER_ATR,
    MIN_REWARD_RISK,
    TARGET_FALLBACK_ATR,
    VOLATILITY_STOP_ATR,
    WATCH_REACH_ATR,
    decide,
)
from app.scan.regime import compute_regime_row

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
    """Net negative: `app.scan.regime` reads this as SHORT positioning -> continuation."""
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
