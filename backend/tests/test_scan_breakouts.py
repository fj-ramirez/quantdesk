"""Tests for `app/modules/gex/scan/breakouts.py` and `app/modules/gex/scan/indicators.py`. Fully offline: every test
builds a small synthetic `pd.DataFrame` by hand -- no database, no filesystem, no network, in
keeping with the modules' purity contract (T43, plans/continuation/01-breakout-ledger.md).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.modules.gex.scan.breakouts import (
    MIN_EVENTS_FOR_RATE,
    BreakoutEvent,
    Direction,
    Outcome,
    detect_events,
    summarize,
)
from app.modules.gex.scan.indicators import atr, true_range


def _bars(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    opens: list[float] | None = None,
    start: dt.date = dt.date(2024, 1, 2),
) -> pd.DataFrame:
    """Build a minimal `read_bars`-shaped frame. Dates are plain consecutive calendar days --
    `detect_events` operates on row order, not calendar arithmetic, so weekends/holidays are
    irrelevant to every test in this file except the `missing_bar_fraction` ones, which set
    their own dates explicitly.
    """
    n = len(closes)
    assert len(highs) == n and len(lows) == n
    dates = [start + dt.timedelta(days=i) for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": opens if opens is not None else list(closes),
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000] * n,
            "source": ["test"] * n,
        }
    )


# --- indicators.true_range / atr ----------------------------------------------------------


def test_true_range_first_bar_is_high_minus_low():
    bars = _bars(highs=[102.0, 105.0], lows=[98.0, 101.0], closes=[100.0, 104.0])
    tr = true_range(bars)
    assert tr.iloc[0] == pytest.approx(4.0)  # no prior close: plain high - low


def test_true_range_uses_prior_close_when_it_widens_the_range():
    # Bar 1: high=105, low=101, prev close=100 -> |105-100|=5 is the widest of the three.
    bars = _bars(highs=[102.0, 105.0], lows=[98.0, 101.0], closes=[100.0, 104.0])
    tr = true_range(bars)
    assert tr.iloc[1] == pytest.approx(5.0)


def test_atr_insufficient_history_is_nan_not_zero():
    bars = _bars(
        highs=[101.0] * 10, lows=[99.0] * 10, closes=[100.0] * 10
    )
    result = atr(bars, period=14)
    assert result.isna().all()  # only 10 bars, period=14: never enough history


def test_atr_first_period_minus_one_values_are_nan_rest_are_not():
    bars = _bars(highs=[101.0] * 20, lows=[99.0] * 20, closes=[100.0] * 20)
    result = atr(bars, period=5)
    assert result.iloc[:4].isna().all()
    assert not result.iloc[4:].isna().any()
    # Every true range here is exactly high-low = 2.0 (flat close, so |high-prev_close| and
    # |low-prev_close| never exceed it), so the rolling mean is exactly 2.0 throughout.
    assert result.iloc[4:].to_numpy() == pytest.approx(2.0)


def test_atr_rejects_nonpositive_period():
    bars = _bars(highs=[101.0], lows=[99.0], closes=[100.0])
    with pytest.raises(ValueError, match="period"):
        atr(bars, period=0)


# --- detect_events: monotone series -> all resolved events continue -----------------------


def test_monotone_series_resolved_events_all_continue():
    n, k = 5, 3
    closes = [100.0 + i for i in range(50)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    bars = _bars(highs, lows, closes)

    events = detect_events(bars, n=n, k=k)
    assert events  # off-by-one guard: a broken range window collapses this to zero

    resolved = [e for e in events if e.outcome is not Outcome.PENDING]
    assert resolved
    assert all(e.direction is Direction.UP for e in resolved)
    assert all(e.outcome is Outcome.CONTINUED for e in resolved)
    # ATR14 needs 14 bars of its own history, so the earliest events (bar index < 13) have no
    # measurable ATR yet and correctly carry `follow_through_atr=None` rather than a fabricated
    # number -- only assert positivity where ATR was actually available, and require that at
    # least one such event exists so the check is not vacuous.
    with_atr = [e for e in resolved if e.follow_through_atr is not None]
    assert with_atr
    assert all(e.follow_through_atr > 0 for e in with_atr)


def test_range_window_excludes_the_current_bar_off_by_one_guard():
    """The plan's named first-contact failure: including bar t's own high/low in its own range
    makes a breakout mathematically impossible (a close can never exceed its own high), which
    would silently collapse every event count to zero. n+1 bars is the minimum where exactly
    one breakout decision is even possible; assert it fires.
    """
    n, k = 5, 3
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0]  # bar 5 jumps well past the n=5 range
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    bars = _bars(highs, lows, closes)

    events = detect_events(bars, n=n, k=k)
    assert len(events) == 1
    assert events[0].direction is Direction.UP
    assert events[0].date == bars["date"].iloc[5]


# --- detect_events: sawtooth pokes -> all resolved events fail -----------------------------


def test_sawtooth_pokes_resolved_events_all_fail():
    n, k = 5, 3
    base_high, base_low, base_close = 100.5, 99.5, 100.0
    gap = n + k + 2  # generous spacing: lets the range fully "forget" the previous poke
    # 20 (not just n) flat baseline bars up front so ATR14 has a full window by the time the
    # first poke happens -- otherwise the earliest event's follow-through is legitimately
    # unmeasurable (None) rather than negative, which is a different, weaker assertion.
    seed = 20

    highs = [base_high] * seed
    lows = [base_low] * seed
    closes = [base_close] * seed
    for _ in range(6):
        # Poke day: closes above the established range, then reverts and closes back inside
        # it well within k bars.
        highs.append(102.5)
        lows.append(101.5)
        closes.append(102.0)
        for _ in range(gap):
            highs.append(base_high)
            lows.append(base_low)
            closes.append(base_close)
    bars = _bars(highs, lows, closes)

    events = detect_events(bars, n=n, k=k)
    resolved = [e for e in events if e.outcome is not Outcome.PENDING]
    assert len(resolved) >= 5  # several independent pokes, not clustered into one
    assert all(e.direction is Direction.UP for e in resolved)
    assert all(e.outcome is Outcome.FAILED for e in resolved)
    assert all(e.follow_through_atr is not None and e.follow_through_atr < 0 for e in resolved)


# --- detect_events: clustering --------------------------------------------------------------


def test_two_consecutive_up_closes_above_range_produce_one_event():
    n, k = 5, 3
    # 5 flat baseline bars establish the range, then two consecutive days both individually
    # close above the (shifting) range -- clustering must record only the first.
    highs = [100.5] * n + [110.5, 120.5] + [105.5] * 5
    lows = [99.5] * n + [109.5, 119.5] + [104.5] * 5
    closes = [100.0] * n + [110.0, 120.0] + [105.0] * 5
    bars = _bars(highs, lows, closes)

    events = detect_events(bars, n=n, k=k)
    assert len(events) == 1
    assert events[0].date == bars["date"].iloc[n]  # the first poke, not the second
    assert events[0].direction is Direction.UP


def test_opposite_direction_event_is_never_suppressed_by_clustering():
    n, k = 5, 10  # a long clustering window that would suppress a same-direction repeat
    highs = [100.5] * n + [110.5] + [100.5] * 3
    lows = [99.5] * n + [109.5] + [89.5] * 3  # down-break right after the up-break
    closes = [100.0] * n + [110.0] + [90.0] * 3
    bars = _bars(highs, lows, closes)

    events = detect_events(bars, n=n, k=k)
    directions = [e.direction for e in events]
    assert Direction.UP in directions
    assert Direction.DOWN in directions


# --- detect_events: edge cases ---------------------------------------------------------------


def test_insufficient_bars_returns_no_events():
    n, k = 20, 5
    bars = _bars(highs=[101.0] * 10, lows=[99.0] * 10, closes=[100.0] * 10)
    assert detect_events(bars, n=n, k=k) == []


def test_empty_bars_returns_no_events():
    bars = _bars(highs=[], lows=[], closes=[])
    assert detect_events(bars) == []


def test_detect_events_rejects_nonpositive_n_or_k():
    bars = _bars(highs=[101.0], lows=[99.0], closes=[100.0])
    with pytest.raises(ValueError, match="n and k"):
        detect_events(bars, n=0, k=5)
    with pytest.raises(ValueError, match="n and k"):
        detect_events(bars, n=5, k=0)


def test_pending_event_has_no_final_outcome_but_has_a_current_excursion():
    n, k = 5, 10
    # 20 flat baseline bars (>= ATR14's own window) so the breakout bar has a measurable ATR,
    # then a breakout with only 2 trailing bars -- fewer than k=10, so it stays pending.
    closes = [100.0] * 20 + [110.0, 111.0, 112.0]
    highs = [c + 0.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    bars = _bars(highs, lows, closes)

    events = detect_events(bars, n=n, k=k)
    assert len(events) == 1
    event = events[0]
    assert event.outcome is Outcome.PENDING
    assert event.resolved_at is None
    assert event.follow_through_atr is None
    assert event.excursion_atr is not None  # current-state excursion is still available
    assert event.bars_elapsed == 2  # only 2 bars have elapsed since the breakout


# --- summarize --------------------------------------------------------------------------------


def _event(
    date: dt.date,
    outcome: Outcome,
    follow_through: float | None = 1.0,
    direction: Direction = Direction.UP,
) -> BreakoutEvent:
    return BreakoutEvent(
        date=date,
        direction=direction,
        level=100.0,
        close=101.0,
        outcome=outcome,
        resolved_at=None if outcome is Outcome.PENDING else date + dt.timedelta(days=5),
        bars_elapsed=5,
        follow_through_atr=None if outcome is Outcome.PENDING else follow_through,
        excursion_atr=follow_through,
        mfe_atr=follow_through,
        mae_atr=follow_through,
    )


def test_summarize_below_five_events_reports_rate_none():
    events = [
        _event(dt.date(2026, 1, 1), Outcome.CONTINUED),
        _event(dt.date(2026, 1, 2), Outcome.FAILED),
        _event(dt.date(2026, 1, 3), Outcome.CONTINUED),
    ]
    assert len(events) < MIN_EVENTS_FOR_RATE
    summary = summarize(events, lookback=126)
    assert summary.events == 3
    assert summary.rate is None


def test_summarize_five_or_more_events_reports_a_rate():
    events = [_event(dt.date(2026, 1, i + 1), Outcome.CONTINUED) for i in range(4)]
    events.append(_event(dt.date(2026, 1, 5), Outcome.FAILED))
    summary = summarize(events, lookback=126)
    assert summary.events == 5
    assert summary.rate == pytest.approx(4 / 5)


def test_summarize_pending_events_excluded_from_rate_denominator():
    events = [_event(dt.date(2026, 1, i + 1), Outcome.CONTINUED) for i in range(4)]
    events.append(_event(dt.date(2026, 1, 5), Outcome.PENDING))
    summary = summarize(events, lookback=126)
    assert summary.events == 5  # pending counts toward the n>=5 floor
    assert summary.pending == 1
    assert summary.rate == pytest.approx(1.0)  # but not toward the rate's own denominator


def test_summarize_empty_events():
    summary = summarize([], lookback=126)
    assert summary.events == 0
    assert summary.rate is None
    assert summary.mean_follow_through_atr is None
    assert summary.last_event is None


def test_summarize_last_event_is_most_recent_by_date():
    events = [
        _event(dt.date(2026, 1, 1), Outcome.CONTINUED),
        _event(dt.date(2026, 1, 10), Outcome.FAILED),
        _event(dt.date(2026, 1, 5), Outcome.CONTINUED),
    ]
    summary = summarize(events, lookback=126)
    assert summary.last_event is not None
    assert summary.last_event.date == dt.date(2026, 1, 10)


def test_summarize_mean_follow_through_ignores_pending_and_unmeasurable():
    events = [
        _event(dt.date(2026, 1, 1), Outcome.CONTINUED, follow_through=2.0),
        _event(dt.date(2026, 1, 2), Outcome.CONTINUED, follow_through=4.0),
        _event(dt.date(2026, 1, 3), Outcome.PENDING),
    ]
    summary = summarize(events, lookback=126)
    assert summary.mean_follow_through_atr == pytest.approx(3.0)
