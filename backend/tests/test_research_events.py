"""The M1 R evaluator's fill and exit rules (T147, `app.modules.research.events`)."""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.research.events import LONG, SHORT, Intent, clustered_mean_se, replay


def bars(rows: list[tuple[float, float, float, float]], spread: float = 0.0):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.full(len(a), spread)


def run(rows, intents, spread=0.0):
    return replay(*bars(rows, spread), intents)


def test_long_hits_target_at_2r():
    rows = [(100, 100.5, 99.5, 100), (100, 101, 99.5, 101), (101, 102.5, 100.5, 102)]
    (t,), _ = run(rows, [Intent(1, LONG, 99.0, 102.0, 3)])
    assert t.how == "target" and t.gross_r == pytest.approx(2.0) and t.exit == 2


def test_ambiguous_bar_is_a_stop():
    rows = [(100, 100, 100, 100), (100, 100.2, 99.8, 100), (100, 103, 98, 100)]
    (t,), _ = run(rows, [Intent(1, LONG, 99.0, 102.0, 3)])
    assert t.how == "stop" and t.gross_r == pytest.approx(-1.0)


def test_fill_bar_never_pays():
    rows = [(100, 100, 100, 100), (100, 103, 99.5, 100.5), (100.5, 100.6, 100.4, 100.5)]
    (t,), _ = run(rows, [Intent(1, LONG, 99.0, 102.0, 3)])
    assert t.how == "time" and t.gross_r == pytest.approx(0.5)


def test_fill_bar_can_stop():
    rows = [(100, 100, 100, 100), (100, 100.2, 98.5, 99), (99, 103, 99, 103)]
    (t,), _ = run(rows, [Intent(1, LONG, 99.0, 102.0, 3)])
    assert t.how == "stop" and t.exit == 1


def test_gap_through_stop_exits_at_open():
    rows = [(100, 100, 100, 100), (100, 100.2, 99.8, 100), (98, 98.5, 97.5, 98)]
    (t,), _ = run(rows, [Intent(1, LONG, 99.0, 102.0, 3)])
    assert t.how == "stop_gap" and t.gross_r == pytest.approx(-2.0)


def test_gap_through_target_exits_at_open():
    rows = [(100, 100, 100, 100), (100, 100.2, 99.8, 100), (103, 104, 102.5, 103)]
    (t,), _ = run(rows, [Intent(1, LONG, 99.0, 102.0, 3)])
    assert t.how == "target_gap" and t.gross_r == pytest.approx(3.0)


def test_short_mirror_and_time_exit_at_last_close_before_deadline():
    rows = [(100, 100, 100, 100), (100, 100.5, 99.5, 99.5), (99.5, 99.6, 99, 99.2),
            (99.2, 99.3, 90, 90)]
    (t,), _ = run(rows, [Intent(1, SHORT, 101.0, None, 3)])
    assert t.how == "time" and t.exit == 2 and t.gross_r == pytest.approx(0.8)


def test_fill_open_beyond_stop_is_skipped():
    rows = [(100, 100, 100, 100), (98, 98, 98, 98)]
    trades, skipped = run(rows, [Intent(1, LONG, 99.0, None, 2)])
    assert trades == [] and skipped["gapped"] == 1


def test_one_position_at_a_time():
    rows = [(100, 100.1, 99.9, 100)] * 6
    trades, skipped = run(rows, [Intent(1, LONG, 99.0, None, 4), Intent(2, LONG, 99.0, None, 6),
                                 Intent(4, LONG, 99.0, None, 6)])
    assert [t.fill for t in trades] == [1, 4] and skipped["busy"] == 1


def test_cost_is_the_entry_bars_spread_in_r():
    rows = [(100, 100, 100, 100), (100, 100.1, 99.9, 100), (100, 100.1, 99.9, 100)]
    (t,), _ = run(rows, [Intent(1, LONG, 98.0, None, 3)], spread=0.5)
    assert t.cost_r == pytest.approx(0.25)


def test_clustered_se_counts_a_session_once():
    r = np.array([1.0, 1.0, -1.0, -1.0])
    _, se_iid = clustered_mean_se(r, np.array([0, 1, 2, 3]))
    _, se_cl = clustered_mean_se(r, np.array([0, 0, 1, 1]))
    assert se_cl > se_iid
    assert clustered_mean_se(np.array([1.0]), np.array([0]))[1] != clustered_mean_se(
        np.array([1.0]), np.array([0]))[1]  # nan: one cluster gives no SE
