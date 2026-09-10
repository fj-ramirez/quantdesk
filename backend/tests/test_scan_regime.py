"""Tests for `app/scan/regime.py` (T48, plans/continuation/03-regime-board.md). Fully offline:
every test builds a hand-constructed `KeyLevels`/`StrikeGex` -- no database, no filesystem, no
network, no Parquet, in keeping with the module's purity contract.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.gex.engine import KeyLevels, StrikeGex
from app.gex.report import POSITIONING_RATIO_FLOOR
from app.scan.regime import (
    CONTINUATION_WALL_ATR,
    ROOM_BEYOND_FRACTION,
    ZERO_DTE_SHARE_FLOOR,
    compute_regime_row,
    zero_dte_share,
)

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


def _row(
    *,
    levels: KeyLevels,
    by_strike: list[StrikeGex],
    zero_dte_by_strike: list[StrikeGex] = (),
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
        levels=levels,
        by_strike=by_strike,
        zero_dte_by_strike=list(zero_dte_by_strike),
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


# --- zero_dte_share -----------------------------------------------------------------------------


def test_zero_dte_share_empty_zero_dte_is_none_not_a_fabricated_zero():
    """Supervisor's live measurement (2026-09-09, SPY snapshot id 38): `ZERO_DTE`'s by-strike
    rows are empty on an ordinary EOD capture because that day's own 0DTE series has already
    left the payload by capture time, not because 0DTE gamma is genuinely zero -- so an empty
    input must read as "unmeasurable" (`None`), never a fabricated `0.0` claiming "no 0DTE
    gamma today."
    """
    by_strike = [_strike(100.0, 5.0, -2.0)]
    assert zero_dte_share(by_strike, []) is None


def test_zero_dte_share_ratio_at_matching_strikes():
    # ALL: strike 100 carries 10 abs total; strike 200 carries 40 abs total (not touched by
    # 0DTE at all, and must NOT dilute the denominator -- "at the same strike set").
    by_strike = [_strike(100.0, 10.0, 0.0), _strike(200.0, 40.0, 0.0)]
    zero_dte = [_strike(100.0, 4.0, 0.0)]
    # 4 / 10 == 0.4, not 4 / 50.
    assert zero_dte_share(by_strike, zero_dte) == pytest.approx(0.4)


def test_zero_dte_share_none_when_zero_dte_strikes_absent_from_all():
    by_strike = [_strike(100.0, 10.0, 0.0)]
    zero_dte = [_strike(999.0, 4.0, 0.0)]  # not in `by_strike` at all -- should not happen live
    assert zero_dte_share(by_strike, zero_dte) is None


# --- room beyond (hand-checked strike ladder, T48 acceptance item) -----------------------------


def test_room_beyond_hand_checked_strike_ladder():
    """Strike ladder built so the 25% threshold (`ROOM_BEYOND_FRACTION`) is cleared or missed
    by hand-picked margins on both sides of spot.

    Above the call wall (104, abs_gex=8.0, threshold=2.0): 106 (abs=1.0, misses), 108
    (abs=1.5, misses), 110 (abs=3.0, clears) -> room_beyond = 110 - 104 = 6.0.

    Below the put wall (96, abs_gex=8.0, threshold=2.0): 94 (abs=1.0, misses), 92 (abs=1.8,
    misses), 90 (abs=3.0, clears) -> room_beyond = 96 - 90 = 6.0.
    """
    levels = _levels(call_wall=104.0, call_wall_gex=8.0e9, put_wall=96.0, put_wall_gex=-8.0e9)
    by_strike = [
        _strike(90.0, 0.0, -3.0),
        _strike(92.0, 0.0, -1.8),
        _strike(94.0, 0.0, -1.0),
        _strike(96.0, 0.0, -8.0),  # put wall
        _strike(100.0, 1.0, -1.0),  # spot strike, not touched by either scan
        _strike(104.0, 8.0, 0.0),  # call wall
        _strike(106.0, 1.0, 0.0),
        _strike(108.0, 1.5, 0.0),
        _strike(110.0, 3.0, 0.0),
    ]
    row = _row(levels=levels, by_strike=by_strike, atr14=5.0)

    assert ROOM_BEYOND_FRACTION == 0.25  # the constant this hand-check assumes
    assert row.wall_above is not None
    assert row.wall_above.strike == pytest.approx(104.0)
    assert row.wall_above.room_beyond == pytest.approx(6.0)
    assert row.wall_above.room_beyond_strike == pytest.approx(110.0)

    assert row.wall_below is not None
    assert row.wall_below.strike == pytest.approx(96.0)
    assert row.wall_below.room_beyond == pytest.approx(6.0)
    assert row.wall_below.room_beyond_strike == pytest.approx(90.0)


def test_room_beyond_none_when_no_strike_beyond_clears_the_threshold():
    levels = _levels(call_wall=104.0, call_wall_gex=8.0e9, put_wall=None, put_wall_gex=None)
    by_strike = [
        _strike(100.0, 1.0, -1.0),
        _strike(104.0, 8.0, 0.0),
        _strike(106.0, 1.0, 0.0),  # abs=1.0, never clears 25% of 8.0 = 2.0
        _strike(108.0, 1.9, 0.0),  # abs=1.9, still short
    ]
    row = _row(levels=levels, by_strike=by_strike)
    assert row.wall_above.room_beyond is None
    assert row.wall_above.room_beyond_strike is None


# --- verdict branches (T48 acceptance item: each branch pinned, plus noise-dominated) ----------


def test_verdict_fade():
    # LONG gamma, flip 10 below spot on ATR=5 (2.0 ATR, > FADE_FLIP_ATR), nearest wall 4 away on
    # ATR=5 (0.8 ATR, <= FADE_WALL_ATR), 0DTE share well above the floor.
    levels = _levels(flip_point=90.0, call_wall=104.0, put_wall=96.0)
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(100.0, 1.0, -1.0), _strike(104.0, 8.0, 0.0)]
    zero_dte = [_strike(96.0, 3.0, 0.0), _strike(104.0, 3.0, 0.0)]  # high share at these strikes
    row = _row(levels=levels, by_strike=by_strike, zero_dte_by_strike=zero_dte, atr14=5.0)

    assert row.positioning.direction == "LONG"
    assert row.flip_distance_atr == pytest.approx(2.0)
    assert row.zero_dte_share > ZERO_DTE_SHARE_FLOOR
    assert row.verdict == "fade"
    assert row.reasons  # every branch must explain itself


def test_verdict_fade_with_zero_dte_share_unavailable_drops_the_clause():
    """The ordinary case on real EOD data (supervisor's live measurement, 2026-09-09): the
    `ZERO_DTE` filter admits nothing, so `zero_dte_share` is `None`. Fade must still be
    reachable on flip/wall distance alone -- the 0DTE clause is dropped, not treated as a
    failing condition -- and the reason string must say so explicitly rather than let a reader
    assume 0DTE evidence stood behind the verdict.
    """
    levels = _levels(flip_point=90.0, call_wall=104.0, put_wall=96.0)
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(100.0, 1.0, -1.0), _strike(104.0, 8.0, 0.0)]
    row = _row(levels=levels, by_strike=by_strike, zero_dte_by_strike=[], atr14=5.0)

    assert row.zero_dte_share is None
    assert row.verdict == "fade"
    assert any("unavailable" in reason and "dropped" in reason for reason in row.reasons)


def test_verdict_mixed_reports_known_zero_dte_share_below_floor():
    # Same flip/wall setup as `test_verdict_fade` (flip and wall conditions both clear), but
    # with a *known*, low 0DTE share -- this must block `fade` (a known-and-failing input is
    # not the same as an unavailable one) and the mixed reason for 0DTE must name the actual
    # number, not "unavailable".
    levels = _levels(flip_point=90.0, call_wall=104.0, put_wall=96.0)
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(100.0, 1.0, -1.0), _strike(104.0, 8.0, 0.0)]
    zero_dte = [_strike(96.0, 0.05, 0.0)]  # tiny share, well under the floor
    row = _row(levels=levels, by_strike=by_strike, zero_dte_by_strike=zero_dte, atr14=5.0)

    assert row.zero_dte_share is not None
    assert row.zero_dte_share < ZERO_DTE_SHARE_FLOOR
    assert row.verdict == "mixed"
    assert any("0DTE share is" in reason for reason in row.reasons)


def test_verdict_continuation_from_short_gamma():
    levels = _levels(net_gex=-5.0e9, abs_gex=19.0e9, call_wall=104.0, put_wall=96.0)
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(104.0, 8.0, 0.0)]
    row = _row(levels=levels, by_strike=by_strike)

    assert row.positioning.direction == "SHORT"
    assert row.verdict == "continuation"


def test_verdict_continuation_from_long_gamma_near_flip_with_far_wall():
    # LONG gamma, spot within 0.5 ATR of the flip, and the wall in the 5-day move's direction
    # (up, since return_5d > 0) more than 2 ATR away.
    levels = _levels(flip_point=99.0, call_wall=115.0, put_wall=96.0)
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(100.0, 1.0, -1.0), _strike(115.0, 8.0, 0.0)]
    row = _row(levels=levels, by_strike=by_strike, atr14=5.0, return_5d=0.02)

    assert abs(row.flip_distance_atr) <= 0.5
    assert row.wall_above.distance_atr > CONTINUATION_WALL_ATR
    assert row.verdict == "continuation"


def test_verdict_mixed_when_neither_rule_clears():
    # LONG gamma, flip far below spot but *both* walls are also far (fails fade's "nearest
    # wall within 1 ATR" regardless of the 0DTE gate), and spot is not near the flip either
    # (fails continuation's own gate).
    levels = _levels(flip_point=50.0, call_wall=150.0, put_wall=70.0)
    by_strike = [_strike(70.0, 0.0, -6.0), _strike(150.0, 8.0, 0.0)]
    row = _row(levels=levels, by_strike=by_strike, atr14=5.0, return_5d=0.01)

    assert row.verdict == "mixed"
    assert len(row.reasons) >= 3  # explains what did and did not clear


def test_verdict_none_when_noise_dominated_dia_like_fixture():
    """T48's hard acceptance item, verbatim: "a DIA fixture must never yield a verdict." Ratio
    is `docs/validation.md`'s own measured DIA figure, 0.9% of gross -- well under
    `POSITIONING_RATIO_FLOOR` (3%).
    """
    net = 0.9
    abs_gross = 100.0
    assert net / abs_gross < POSITIONING_RATIO_FLOOR  # sanity check on the fixture itself
    levels = _levels(net_gex=net, abs_gex=abs_gross, call_wall=104.0, put_wall=96.0)
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(104.0, 8.0, 0.0)]
    row = _row(levels=levels, by_strike=by_strike)

    assert row.positioning.noise_dominated is True
    assert row.verdict is None
    assert row.reasons


def test_verdict_none_when_stale_even_if_otherwise_fade():
    """Staleness suppresses the verdict on top of (not instead of) the noise-dominated gate --
    this fixture would otherwise pin `fade` (same inputs as `test_verdict_fade`).
    """
    levels = _levels(flip_point=90.0, call_wall=104.0, put_wall=96.0)
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(104.0, 8.0, 0.0)]
    zero_dte = [_strike(96.0, 3.0, 0.0), _strike(104.0, 3.0, 0.0)]
    row = _row(
        levels=levels,
        by_strike=by_strike,
        zero_dte_by_strike=zero_dte,
        atr14=5.0,
        stale=True,
        chain_age_minutes=261.0,  # T47's XBI figure: 4h21m
    )

    assert row.verdict is None
    assert row.stale is True
    assert row.chain_age_minutes == pytest.approx(261.0)
    assert "stale" in row.reasons[0]


# --- echoed inputs (T48's "echoes every input" requirement) -------------------------------------


def test_row_echoes_every_scalar_input():
    levels = _levels()
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(104.0, 8.0, 0.0)]
    row = _row(
        levels=levels,
        by_strike=by_strike,
        spot=100.0,
        atr14=5.0,
        iv30=0.22,
        rv20=0.18,
        return_5d=0.015,
    )

    assert row.underlying == "TEST"
    assert row.filter == "ALL"
    assert row.spot == pytest.approx(100.0)
    assert row.atr14 == pytest.approx(5.0)
    assert row.iv30 == pytest.approx(0.22)
    assert row.rv20 == pytest.approx(0.18)
    assert row.iv_rv_ratio == pytest.approx(0.22 / 0.18)
    assert row.return_5d == pytest.approx(0.015)
    assert row.as_of == _NOW
    assert row.effective_at == _NOW


def test_row_to_dict_is_json_safe():
    levels = _levels()
    by_strike = [_strike(96.0, 0.0, -6.0), _strike(104.0, 8.0, 0.0)]
    row = _row(levels=levels, by_strike=by_strike)
    payload = row.to_dict()
    assert payload["underlying"] == "TEST"
    assert isinstance(payload["reasons"], list)
    assert "positioning" in payload
