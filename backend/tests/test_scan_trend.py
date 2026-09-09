"""Tests for `app/scan/trend.py` (T45, plans/continuation/02-trend-chop-scorer.md). Fully
offline: every test builds a small synthetic `pd.DataFrame` or hand-built `TrendComponents` --
no database, no filesystem, no network, in keeping with the module's purity contract.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.scan.trend import TrendComponents, rank_universe, score_symbol


def _bars(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    start: dt.date = dt.date(2024, 1, 2),
) -> pd.DataFrame:
    n = len(closes)
    dates = [start + dt.timedelta(days=i) for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": list(closes),
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000] * n,
            "source": ["test"] * n,
        }
    )


def _trending_bars(n: int = 150) -> pd.DataFrame:
    closes = [100.0 + i for i in range(n)]
    return _bars([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)


# --- score_symbol -----------------------------------------------------------------------------


def test_score_symbol_iv30_none_flows_through_to_null_score_not_a_default():
    """CLAUDE.md non-negotiable, verbatim: "iv30 = None must flow through to a null score, not
    a default." A symbol with no option chain is most of `SCAN_UNIVERSE` (19 of 47 symbols)."""
    components = score_symbol(_trending_bars(), None)
    assert components.iv30 is None
    assert components.iv_rv_ratio is None
    # Every non-IV component should still compute normally -- the missing chain narrows only
    # the IV-dependent fields, nothing else.
    assert components.adx14 is not None
    assert components.rv20 is not None


def test_score_symbol_with_iv30_computes_iv_rv_ratio():
    components = score_symbol(_trending_bars(), 0.25)
    assert components.iv30 == pytest.approx(0.25)
    assert components.rv20 is not None
    assert components.iv_rv_ratio == pytest.approx(0.25 / components.rv20)


def test_score_symbol_insufficient_history_returns_none_components_not_zeros():
    """Acceptance item 4: "A symbol with insufficient history returns `None` components, not
    zeros." Five bars is nowhere near any indicator's warm-up window."""
    closes = [100.0, 101.0, 99.0, 102.0, 98.0]
    bars = _bars([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)
    components = score_symbol(bars, 0.25)
    assert components.adx14 is None
    assert components.er20 is None
    assert components.chop14 is None
    assert components.rv20 is None
    assert components.vr is None
    assert components.vr_z is None
    # IV itself is independent of bars history -- still passed through.
    assert components.iv30 == pytest.approx(0.25)
    # rv20 is None, so the ratio cannot be computed even though iv30 is present.
    assert components.iv_rv_ratio is None


def test_score_symbol_empty_bars_returns_all_none():
    bars = _bars([], [], [])
    components = score_symbol(bars, None)
    assert components == TrendComponents(
        adx14=None,
        er20=None,
        chop14=None,
        vr=None,
        vr_z=None,
        rv20=None,
        iv30=None,
        iv_rv_ratio=None,
    )


def test_score_symbol_trending_series_scores_high_on_every_composite_component():
    """Plan's own property: "a trending series scores high"."""
    components = score_symbol(_trending_bars(), None)
    assert components.adx14 > 50.0  # strong trend strength
    assert components.er20 == pytest.approx(1.0, abs=1e-9)
    assert components.chop14 < 30.0  # far from the 100 "maximally choppy" end
    assert components.vr_z > 5.0  # strongly positive: momentum


def test_score_symbol_mean_reverting_series_scores_low_on_vr_and_er():
    """Plan's own property: "a mean-reverting series scores low" -- a pure alternating
    sawtooth has zero net efficiency and negative serial correlation in its returns."""
    n = 150
    cum = [100.0]
    for i in range(1, n):
        cum.append(cum[-1] + (1.0 if i % 2 == 0 else -1.0))
    bars = _bars([c + 0.1 for c in cum], [c - 0.1 for c in cum], cum)
    components = score_symbol(bars, None)
    assert components.er20 == pytest.approx(0.0, abs=1e-9)
    assert components.vr_z is not None
    assert components.vr_z < 0.0  # mean reversion, not momentum


# --- rank_universe ------------------------------------------------------------------------------


def _components(adx14, er20, chop14, vr_z, rv20=0.2, iv30=None) -> TrendComponents:
    return TrendComponents(
        adx14=adx14,
        er20=er20,
        chop14=chop14,
        vr=1.0,
        vr_z=vr_z,
        rv20=rv20,
        iv30=iv30,
        iv_rv_ratio=None,
    )


def test_rank_universe_three_symbol_fixture_percentiles_are_exactly_0_half_1():
    """Plan's acceptance criterion, verbatim: "percentiles across a 3-symbol fixture are
    exactly {0, 0.5, 1}"."""
    components_by_symbol = {
        "LOW": _components(10.0, 0.2, 80.0, -3.0),
        "MID": _components(30.0, 0.5, 50.0, 0.0),
        "HIGH": _components(50.0, 0.9, 20.0, 3.0),
    }
    rows = rank_universe(components_by_symbol)
    by_symbol = {row.symbol: row for row in rows}

    assert by_symbol["LOW"].adx_pct == 0.0
    assert by_symbol["MID"].adx_pct == 0.5
    assert by_symbol["HIGH"].adx_pct == 1.0

    # chop14 is inverted (lower chop = more trending), so the *lowest* CHOP symbol (HIGH, 20.0)
    # gets the top percentile.
    assert by_symbol["HIGH"].chop_pct == 1.0
    assert by_symbol["LOW"].chop_pct == 0.0

    assert by_symbol["LOW"].composite == 0.0
    assert by_symbol["MID"].composite == 0.5
    assert by_symbol["HIGH"].composite == 1.0


def test_rank_universe_iv_rv_and_rv_excluded_from_composite():
    """Plan's design decision, verbatim: "IV/RV is a hint, not a component" -- and rv20/iv30
    are excluded from the composite for the same underlying reason (see `app.scan.trend`'s
    module docstring). Two symbols identical on every composite-feeding component but wildly
    different on rv20/iv30 must land on the identical composite.
    """
    components_by_symbol = {
        "A": _components(30.0, 0.5, 50.0, 0.0, rv20=0.10, iv30=0.05),
        "B": _components(30.0, 0.5, 50.0, 0.0, rv20=0.90, iv30=0.95),
    }
    rows = rank_universe(components_by_symbol)
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["A"].composite == by_symbol["B"].composite == 0.5


def test_rank_universe_missing_component_narrows_composite_not_none():
    """A symbol missing exactly one of the four composite inputs still gets a composite -- the
    mean of the ones it does have -- rather than `None`, per `rank_universe`'s own documented
    policy (a partial set of real percentiles is not a fabricated default).
    """
    components_by_symbol = {
        "FULL": _components(30.0, 0.5, 50.0, 0.0),
        "PARTIAL": TrendComponents(
            adx14=None,  # e.g. insufficient history for ADX specifically
            er20=0.5,
            chop14=50.0,
            vr=1.0,
            vr_z=0.0,
            rv20=0.2,
            iv30=None,
            iv_rv_ratio=None,
        ),
    }
    rows = rank_universe(components_by_symbol)
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["PARTIAL"].adx_pct is None
    assert by_symbol["PARTIAL"].er_pct is not None
    assert by_symbol["PARTIAL"].composite is not None
    # Only two symbols and PARTIAL has no adx14 at all, so it never enters the ADX ranking --
    # FULL is alone there and gets the single-observation 0.5.
    assert by_symbol["FULL"].adx_pct == 0.5


def test_rank_universe_zero_finite_components_gives_none_composite():
    components_by_symbol = {
        "EMPTY": TrendComponents(
            adx14=None,
            er20=None,
            chop14=None,
            vr=None,
            vr_z=None,
            rv20=None,
            iv30=None,
            iv_rv_ratio=None,
        ),
    }
    rows = rank_universe(components_by_symbol)
    assert rows[0].composite is None
    assert rows[0].adx_pct is None
