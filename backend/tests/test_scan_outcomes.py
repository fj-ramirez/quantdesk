"""Tests for `app/scan/outcomes.py` (T61). Fully offline, hand-built bars.

Geometry throughout: LONG fade entry 100, stop 98 (risk 2), target 106 (3R); SHORT mirrors.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.scan.outcomes import (
    MAX_HOLD_BARS,
    MIN_RESOLVED_FOR_RATE,
    TRIGGER_WINDOW_BARS,
    TradeSpec,
    evaluate,
    summarize_outcomes,
)

LONG_FADE = TradeSpec(setup="fade", side="LONG", entry=100.0, stop=98.0, target=106.0)
SHORT_FADE = TradeSpec(setup="fade", side="SHORT", entry=100.0, stop=102.0, target=94.0)
LONG_CONT = TradeSpec(setup="continuation", side="LONG", entry=100.0, stop=98.0, target=106.0)


def _bars(rows: list[tuple[float, float, float, float]], start=dt.date(2026, 9, 11)) -> pd.DataFrame:
    """`rows` are (open, high, low, close) per successive weekday."""
    dates = []
    day = start
    while len(dates) < len(rows):
        if day.weekday() < 5:
            dates.append(day)
        day += dt.timedelta(days=1)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        }
    )


def test_empty_bars_is_pending():
    out = evaluate(LONG_FADE, _bars([]))
    assert out.outcome == "pending"
    assert out.evaluated_through is None


def test_invalid_spec_when_stop_equals_entry():
    out = evaluate(TradeSpec("fade", "LONG", 100.0, 100.0, 105.0), _bars([(100, 101, 99, 100)]))
    assert out.outcome == "invalid"


def test_long_fade_fills_at_the_wall_then_hits_the_target():
    bars = _bars([
        (101, 102, 99.5, 101),  # low 99.5 <= 100: filled at 100 (open above the wall)
        (101, 103, 100.5, 102),
        (102, 106.5, 101, 105),  # high >= 106: target
    ])
    out = evaluate(LONG_FADE, bars)
    assert out.outcome == "target"
    assert out.fill == 100.0
    assert out.triggered_on == bars["date"].iloc[0]
    assert out.resolved_on == bars["date"].iloc[2]
    assert out.bars_held == 3
    assert out.result_r == pytest.approx(3.0)
    assert out.mfe_r == pytest.approx(3.25)  # (106.5 - 100) / 2
    assert out.mae_r == pytest.approx(-0.25)  # (99.5 - 100) / 2
    assert "filled at the wall" in out.note and "target hit" in out.note


def test_long_fade_gap_through_the_wall_fills_at_the_open():
    bars = _bars([(99.0, 100.5, 98.5, 100.0), (100, 107, 99, 106)])
    out = evaluate(LONG_FADE, bars)
    assert out.fill == 99.0
    assert "gapped through the wall" in out.note
    assert out.outcome == "target"
    assert out.result_r == pytest.approx((106.0 - 99.0) / 2.0)


def test_stop_is_checked_before_target_on_an_ambiguous_bar():
    bars = _bars([(100, 107, 97, 104)])  # touches entry, stop and target in one bar
    out = evaluate(LONG_FADE, bars)
    assert out.outcome == "stop"
    assert out.result_r == pytest.approx(-1.0)
    assert "also touched the target" in out.note


def test_gap_through_the_stop_exits_at_the_open_worse_than_one_r():
    bars = _bars([(100, 101, 99.5, 100), (96, 97, 95, 96)])
    out = evaluate(LONG_FADE, bars)
    assert out.outcome == "stop"
    assert out.result_r == pytest.approx(-2.0)
    assert "gapped through the stop" in out.note


def test_short_fade_mirrors():
    bars = _bars([(99, 100.5, 98, 99), (99, 100, 93.5, 94)])
    out = evaluate(SHORT_FADE, bars)
    assert out.outcome == "target"
    assert out.fill == 100.0
    assert out.result_r == pytest.approx(3.0)


def test_fade_untriggered_after_the_window():
    bars = _bars([(103, 104, 101, 103)] * TRIGGER_WINDOW_BARS)
    out = evaluate(LONG_FADE, bars)
    assert out.outcome == "untriggered"
    assert out.evaluated_through == bars["date"].iloc[TRIGGER_WINDOW_BARS - 1]


def test_fade_still_pending_inside_the_window():
    bars = _bars([(103, 104, 101, 103)] * (TRIGGER_WINDOW_BARS - 1))
    out = evaluate(LONG_FADE, bars)
    assert out.outcome == "pending"
    assert out.fill is None


def test_continuation_fills_at_the_next_open_and_marks_while_pending():
    bars = _bars([(101, 102, 100.5, 101.5), (101.5, 103, 101, 102)])
    out = evaluate(LONG_CONT, bars)
    assert out.outcome == "pending"
    assert out.fill == 101.0
    assert out.bars_held == 2
    assert out.mark_r == pytest.approx((102.0 - 101.0) / 2.0)
    assert out.result_r is None
    assert "next session's open" in out.note


def test_expires_at_the_close_after_max_hold():
    bars = _bars([(101, 102, 100.5, 101.5)] + [(101, 102, 100.5, 101.0)] * (MAX_HOLD_BARS - 1))
    out = evaluate(LONG_CONT, bars)
    assert out.outcome == "expired"
    assert out.bars_held == MAX_HOLD_BARS
    assert out.result_r == pytest.approx(0.0)
    assert out.resolved_on == bars["date"].iloc[MAX_HOLD_BARS - 1]


def test_summary_groups_and_withholds_rates_below_the_floor():
    rows = [
        ("fade", "A", "target", 3.0),
        ("fade", "A", "stop", -1.0),
        ("fade", "B", "expired", 0.5),
        ("continuation", "B", "pending", None),
        ("continuation", "C", "untriggered", None),
    ]
    tr = summarize_outcomes(rows)
    assert tr.overall.n == 5
    assert tr.overall.resolved == 3
    assert tr.overall.pending == 1 and tr.overall.untriggered == 1
    assert tr.overall.hit_rate is None  # 3 < MIN_RESOLVED_FOR_RATE
    assert tr.overall.avg_r == pytest.approx(2.5 / 3)
    assert tr.overall.total_r == pytest.approx(2.5)
    assert set(tr.by_setup) == {"fade", "continuation"}
    assert tr.by_grade["A"].targets == 1 and tr.by_grade["A"].stops == 1
    assert tr.to_dict()["by_setup"]["fade"]["resolved"] == 3

    enough = [("fade", "A", "target", 2.0)] * 4 + [("fade", "A", "stop", -1.0)] * 1
    assert len(enough) >= MIN_RESOLVED_FOR_RATE
    tr2 = summarize_outcomes(enough)
    assert tr2.overall.hit_rate == pytest.approx(0.8)
    assert tr2.overall.win_rate == pytest.approx(0.8)
