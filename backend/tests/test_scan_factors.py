"""Tests for `app/modules/gex/scan/factors.py` (T93,
plans/decision-inputs/03-factor-cap.md).

Fully offline: every test builds returns by hand, matching the purity contract the module
shares with `scan/indicators.py` and `scan/decisions.py`.

The module exists because the decision path reasoned about one symbol at a time, so a day on
which many correlated names printed the same setup produced many confident rows that were one
trade. The tests below are organised around the three ways that could go wrong in the other
direction: suppressing something that is not a duplicate, suppressing on missing data, and
suppressing without saying why.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.modules.gex.scan.factors import (
    CORR_THRESHOLD,
    MIN_OVERLAP,
    cap_candidates,
    correlation_matrix,
    effective_bets,
    mean_correlation,
    return_frame,
    side_sign,
    summarize,
)


def _bars(closes: list[float], *, start: dt.date = dt.date(2024, 1, 2)) -> pd.DataFrame:
    """Minimal `read_bars`-shaped frame; only `date` and `close` matter here."""
    dates = [start + dt.timedelta(days=i) for i in range(len(closes))]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1_000.0] * len(closes),
            "source": ["test"] * len(closes),
        }
    )


def _walk(steps: list[float], *, base: float = 100.0) -> list[float]:
    """Closes whose successive returns are driven by `steps`."""
    closes = [base]
    for step in steps:
        closes.append(closes[-1] * (1.0 + step))
    return closes


_RNG = np.random.default_rng(20260921)


def _noise(n: int) -> list[float]:
    return list(_RNG.normal(0.0, 0.01, n))


# --- returns, not prices -----------------------------------------------------------------


def test_return_frame_differences_rather_than_correlating_levels():
    """The design decision that is easiest to get silently wrong.

    Two series that both trend up but whose *returns* are unrelated have price correlation
    near 1 and return correlation near 0. This asserts the module measures the second.
    """
    a_steps = _noise(120)
    b_steps = _noise(120)
    trend = 0.004
    bars = {
        "A": _bars(_walk([s + trend for s in a_steps])),
        "B": _bars(_walk([s + trend for s in b_steps])),
    }

    price_corr = float(
        pd.Series(bars["A"]["close"]).corr(pd.Series(bars["B"]["close"]))
    )
    assert price_corr > 0.9, "fixture should trend together in levels"

    corr = correlation_matrix(bars)
    assert abs(corr.at["A", "B"]) < 0.4, "returns must not inherit the shared trend"


def test_return_frame_aligns_on_date_not_position():
    """Two symbols with different start dates must be compared on the days they share."""
    shared = _noise(80)
    a = _bars(_walk(shared), start=dt.date(2024, 1, 2))
    b = _bars(_walk(shared), start=dt.date(2024, 3, 1))
    frame = return_frame({"A": a, "B": b}, window=200)
    both = frame.dropna()
    assert not both.empty
    assert len(both) < min(len(a), len(b)), "only the overlapping dates should be complete"


def test_return_frame_is_empty_for_no_usable_input():
    assert return_frame({}).empty
    assert return_frame({"A": pd.DataFrame()}).empty


# --- the three cases the plan names ------------------------------------------------------


def test_identical_series_correlate_one_and_the_second_is_suppressed():
    steps = _noise(120)
    bars = {"A": _bars(_walk(steps)), "B": _bars(_walk(steps))}
    corr = correlation_matrix(bars)
    assert corr.at["A", "B"] == pytest.approx(1.0, abs=1e-9)

    accepted, suppressions = cap_candidates(
        [("K1", "A", "LONG"), ("K2", "B", "LONG")], corr
    )
    assert accepted == ["K1"]
    assert [s.key for s in suppressions] == ["K2"]
    assert suppressions[0].duplicates_symbol == "A"
    assert suppressions[0].correlation == pytest.approx(1.0, abs=1e-9)


def test_independent_series_both_survive():
    bars = {"A": _bars(_walk(_noise(120))), "B": _bars(_walk(_noise(120)))}
    corr = correlation_matrix(bars)
    accepted, suppressions = cap_candidates(
        [("K1", "A", "LONG"), ("K2", "B", "LONG")], corr
    )
    assert accepted == ["K1", "K2"]
    assert suppressions == []


def test_insufficient_overlap_is_nan_and_never_suppresses():
    """A candidate whose history does not overlap another's has an *undefined* correlation,
    not a high one. Suppressing on missing data would thin the book on exactly the names the
    desk knows least about."""
    steps = _noise(120)
    bars = {
        "A": _bars(_walk(steps), start=dt.date(2024, 1, 2)),
        # Identical returns, but only a handful of bars -- far below MIN_OVERLAP.
        "B": _bars(_walk(steps[:5]), start=dt.date(2024, 1, 2)),
    }
    corr = correlation_matrix(bars)
    assert not np.isfinite(corr.at["A", "B"])

    accepted, suppressions = cap_candidates(
        [("K1", "A", "LONG"), ("K2", "B", "LONG")], corr
    )
    assert accepted == ["K1", "K2"]
    assert suppressions == []


# --- side, and the same-symbol rule ------------------------------------------------------


def test_side_sign_vocabulary():
    assert side_sign("LONG") == 1
    assert side_sign("SHORT") == -1
    assert side_sign("long") == 1
    # Unknown fails towards keeping the row: a 0 makes every adjusted correlation 0.
    assert side_sign("") == 0
    assert side_sign("SIDEWAYS") == 0


def test_opposite_sides_in_correlated_names_are_a_hedge_not_a_duplicate():
    """The decision this module adds beyond its plan file. Long A against short B at +1.0
    correlation is close to a hedge; suppressing a leg of it would be exactly wrong."""
    steps = _noise(120)
    bars = {"A": _bars(_walk(steps)), "B": _bars(_walk(steps))}
    corr = correlation_matrix(bars)

    accepted, suppressions = cap_candidates(
        [("K1", "A", "LONG"), ("K2", "B", "SHORT")], corr
    )
    assert accepted == ["K1", "K2"]
    assert suppressions == []


def test_two_shorts_in_correlated_names_still_duplicate():
    steps = _noise(120)
    bars = {"A": _bars(_walk(steps)), "B": _bars(_walk(steps))}
    corr = correlation_matrix(bars)
    accepted, suppressions = cap_candidates(
        [("K1", "A", "SHORT"), ("K2", "B", "SHORT")], corr
    )
    assert accepted == ["K1"]
    assert [s.key for s in suppressions] == ["K2"]


def test_unknown_side_is_never_suppressed():
    steps = _noise(120)
    bars = {"A": _bars(_walk(steps)), "B": _bars(_walk(steps))}
    corr = correlation_matrix(bars)
    accepted, _ = cap_candidates([("K1", "A", "LONG"), ("K2", "B", "")], corr)
    assert accepted == ["K1", "K2"]


def test_two_opportunities_on_one_symbol_never_duplicate_each_other():
    """A symbol between its two walls carries a fade-the-call-wall and a fade-the-put-wall row
    -- the same range read from both ends. Self-correlation is 1.0 and must not suppress."""
    bars = {"A": _bars(_walk(_noise(120)))}
    corr = correlation_matrix(bars)
    accepted, suppressions = cap_candidates(
        [("FADE_CALL_WALL", "A", "SHORT"), ("FADE_PUT_WALL", "A", "LONG")], corr
    )
    assert accepted == ["FADE_CALL_WALL", "FADE_PUT_WALL"]
    assert suppressions == []


# --- order, explanation, completeness ----------------------------------------------------


def test_the_cap_never_reorders_and_keeps_the_higher_ranked_candidate():
    steps = _noise(120)
    bars = {"A": _bars(_walk(steps)), "B": _bars(_walk(steps))}
    corr = correlation_matrix(bars)

    # Whichever is presented first survives -- the caller owns the ranking.
    accepted, _ = cap_candidates([("K2", "B", "LONG"), ("K1", "A", "LONG")], corr)
    assert accepted == ["K2"]


def test_every_candidate_appears_in_exactly_one_bucket():
    steps = _noise(120)
    bars = {
        "A": _bars(_walk(steps)),
        "B": _bars(_walk(steps)),
        "C": _bars(_walk(_noise(120))),
    }
    corr = correlation_matrix(bars)
    candidates = [("K1", "A", "LONG"), ("K2", "B", "LONG"), ("K3", "C", "LONG")]
    accepted, suppressions = cap_candidates(candidates, corr)
    assert sorted(accepted + [s.key for s in suppressions]) == ["K1", "K2", "K3"]


def test_a_suppression_names_what_it_duplicates():
    """Nothing is silently dropped: the reason has to survive to the point of reading."""
    steps = _noise(120)
    bars = {"A": _bars(_walk(steps)), "B": _bars(_walk(steps))}
    corr = correlation_matrix(bars)
    _, suppressions = cap_candidates([("K1", "A", "LONG"), ("K2", "B", "LONG")], corr)
    payload = suppressions[0].to_dict()
    assert payload["duplicates_symbol"] == "A"
    assert payload["duplicates_key"] == "K1"
    assert "correlation with A" in payload["reason"]
    assert isinstance(payload["correlation"], float)


# --- effective bets ------------------------------------------------------------------------


def test_effective_bets_all_correlated_is_one():
    """n identical bets are one bet held n times -- the situation the module exists to find."""
    corr = pd.DataFrame(np.ones((4, 4)), index=list("ABCD"), columns=list("ABCD"))
    assert effective_bets(corr) == pytest.approx(1.0, abs=1e-12)


def test_effective_bets_all_independent_is_n():
    corr = pd.DataFrame(np.eye(4), index=list("ABCD"), columns=list("ABCD"))
    assert effective_bets(corr) == pytest.approx(4.0, abs=1e-12)


def test_effective_bets_hand_computed_intermediate_case():
    # Four names at a uniform 0.5: n_eff = 4 / (1 + 3*0.5) = 1.6
    values = np.full((4, 4), 0.5)
    np.fill_diagonal(values, 1.0)
    corr = pd.DataFrame(values, index=list("ABCD"), columns=list("ABCD"))
    assert effective_bets(corr) == pytest.approx(1.6, abs=1e-12)


def test_effective_bets_is_none_when_not_measurable():
    assert effective_bets(pd.DataFrame()) is None
    single = pd.DataFrame([[1.0]], index=["A"], columns=["A"])
    assert effective_bets(single) is None
    unmeasurable = pd.DataFrame(
        np.full((2, 2), np.nan), index=list("AB"), columns=list("AB")
    )
    assert effective_bets(unmeasurable) is None


def test_mean_correlation_ignores_the_diagonal():
    values = np.full((3, 3), 0.25)
    np.fill_diagonal(values, 1.0)
    corr = pd.DataFrame(values, index=list("ABC"), columns=list("ABC"))
    assert mean_correlation(corr) == pytest.approx(0.25, abs=1e-12)


# --- the composed entry point ----------------------------------------------------------------


def test_summarize_reports_the_set_it_was_given():
    steps = _noise(120)
    bars = {
        "A": _bars(_walk(steps)),
        "B": _bars(_walk(steps)),
        "C": _bars(_walk(_noise(120))),
        # Present in the correlation matrix but never suggested: must not enter the summary.
        "D": _bars(_walk(_noise(120))),
    }
    corr = correlation_matrix(bars)
    candidates = [("K1", "A", "LONG"), ("K2", "B", "LONG"), ("K3", "C", "LONG")]
    accepted, suppressions, summary = summarize(candidates, corr)

    assert summary.candidates == 3
    assert summary.accepted == len(accepted) == 2
    assert summary.suppressed == len(suppressions) == 1
    assert summary.threshold == CORR_THRESHOLD
    assert summary.independent_bets is not None
    assert 1.0 <= summary.independent_bets <= 3.0
    payload = summary.to_dict()
    assert payload["independent_bets"] is not None
    assert payload["mean_correlation"] is not None


def test_summarize_on_an_empty_candidate_set():
    accepted, suppressions, summary = summarize([], pd.DataFrame())
    assert accepted == []
    assert suppressions == []
    assert summary.candidates == 0
    assert summary.independent_bets is None
    assert summary.mean_correlation is None


def test_min_overlap_constant_is_below_the_window():
    """A `min_overlap` above `CORR_WINDOW` would make every correlation `NaN` forever."""
    from app.modules.gex.scan.factors import CORR_WINDOW

    assert MIN_OVERLAP < CORR_WINDOW
