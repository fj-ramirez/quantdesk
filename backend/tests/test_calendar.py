"""Tests for `app/jobs/calendar.py`.

Covers the actual 2026/2027 holiday dates (T05 acceptance depends on the EOD job correctly
skipping these), the weekday rule, and -- most important per the T05 brief -- that a year
with no data resolves as "not a holiday" (so a capture is attempted rather than silently
skipped) while logging loudly at ERROR.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from app.jobs.calendar import (
    effective_data_time,
    is_market_holiday,
    is_regular_session,
    is_trading_day,
)

_NY = dt.timezone(dt.timedelta(hours=-4))  # EDT, correct for the September dates used below


@pytest.mark.parametrize(
    "holiday",
    [
        dt.date(2026, 1, 1),  # New Year's Day
        dt.date(2026, 1, 19),  # MLK Day
        dt.date(2026, 2, 16),  # Washington's Birthday
        dt.date(2026, 4, 3),  # Good Friday
        dt.date(2026, 5, 25),  # Memorial Day
        dt.date(2026, 6, 19),  # Juneteenth
        dt.date(2026, 7, 3),  # Independence Day (observed)
        dt.date(2026, 9, 7),  # Labor Day
        dt.date(2026, 11, 26),  # Thanksgiving
        dt.date(2026, 12, 25),  # Christmas
        dt.date(2027, 1, 1),
        dt.date(2027, 1, 18),
        dt.date(2027, 2, 15),
        dt.date(2027, 3, 26),
        dt.date(2027, 5, 31),
        dt.date(2027, 6, 18),
        dt.date(2027, 7, 5),
        dt.date(2027, 9, 6),
        dt.date(2027, 11, 25),
        dt.date(2027, 12, 24),
    ],
)
def test_known_holidays_are_market_holidays(holiday):
    assert is_market_holiday(holiday) is True
    assert is_trading_day(holiday) is False


def test_ordinary_weekday_is_a_trading_day():
    # 2026-09-04 is a Friday, not in the holiday list.
    assert is_trading_day(dt.date(2026, 9, 4)) is True


@pytest.mark.parametrize(
    "weekend_day",
    [dt.date(2026, 9, 5), dt.date(2026, 9, 6)],  # Saturday, Sunday
)
def test_weekend_is_not_a_trading_day(weekend_day):
    assert is_trading_day(weekend_day) is False
    # A weekend is not a "holiday" in the market-closure sense -- is_market_holiday is only
    # about the fixed-date closures, weekday-ness is handled separately by is_trading_day.
    assert is_market_holiday(weekend_day) is False


def test_stale_calendar_year_treated_as_open_but_logs_error(caplog):
    """The core T05 requirement: an unmapped year must never silently skip a capture."""
    unmapped = dt.date(2030, 7, 4)
    with caplog.at_level(logging.ERROR, logger="app.jobs.calendar"):
        result = is_market_holiday(unmapped)
    assert result is False
    assert any(
        "STALE HOLIDAY CALENDAR" in record.message and record.levelno == logging.ERROR
        for record in caplog.records
    )


def test_stale_calendar_year_is_trading_day_if_weekday(caplog):
    # 2030-07-04 is a Thursday -- a stale calendar must not stop the capture from firing.
    with caplog.at_level(logging.ERROR, logger="app.jobs.calendar"):
        assert is_trading_day(dt.date(2030, 7, 4)) is True


# --- is_regular_session / effective_data_time (T34) -------------------------------------------
#
# The supervisor's own repro (2026-09-04, 17:55 ET, ~2h after the close): the vendor timestamp
# keeps advancing while the chain itself is frozen at 16:00 ET. These pin the fix with a frozen
# clock on both sides of 16:00 ET, plus a weekend and a holiday, per the T34 acceptance.


def _ny(*args) -> dt.datetime:
    """A 2026-09-04-week NY-local instant. `_NY` (fixed UTC-4) is correct for every date these
    tests use -- all are in September, well inside EDT -- so this avoids pulling in `zoneinfo`
    DST edge cases the fix itself does not touch.
    """
    return dt.datetime(*args, tzinfo=_NY)


def test_mid_session_is_regular_session_and_effective_at_equals_captured_at():
    # Friday 2026-09-04, 14:00 ET: well inside the 09:30-16:00 session.
    captured_at = _ny(2026, 9, 4, 14, 0)
    assert is_regular_session(captured_at) is True
    assert effective_data_time(captured_at, 15) == captured_at.astimezone(dt.UTC)


@pytest.mark.parametrize("boundary", [(9, 30, 0), (16, 0, 0)])
def test_session_boundaries_are_inclusive(boundary):
    hour, minute, second = boundary
    assert is_regular_session(_ny(2026, 9, 4, hour, minute, second)) is True


def test_just_after_close_is_not_a_regular_session_same_day_close_used():
    # The supervisor's own scenario: 17:55 ET, well after the 16:00 close, same trading day.
    captured_at = _ny(2026, 9, 4, 17, 55)
    assert is_regular_session(captured_at) is False
    assert effective_data_time(captured_at, 15) == _ny(2026, 9, 4, 16, 15).astimezone(dt.UTC)


def test_just_before_open_uses_previous_trading_days_close():
    # 06:00 ET on a trading day, before the 09:30 open -- today has not printed anything yet.
    captured_at = _ny(2026, 9, 4, 6, 0)
    assert is_regular_session(captured_at) is False
    # 2026-09-03 (Thursday) is the prior trading day.
    assert effective_data_time(captured_at, 15) == _ny(2026, 9, 3, 16, 15).astimezone(dt.UTC)


def test_weekend_clamps_to_fridays_close():
    # Sunday 2026-09-06, any time -- the market has been shut since Friday's close.
    captured_at = _ny(2026, 9, 6, 12, 0)
    assert is_regular_session(captured_at) is False
    assert effective_data_time(captured_at, 15) == _ny(2026, 9, 4, 16, 15).astimezone(dt.UTC)


def test_holiday_clamps_to_the_prior_trading_days_close():
    # Labor Day 2026-09-07 (Monday) -- walks back over the weekend to Friday 2026-09-04.
    captured_at = _ny(2026, 9, 7, 9, 0)
    assert is_regular_session(captured_at) is False
    assert effective_data_time(captured_at, 15) == _ny(2026, 9, 4, 16, 15).astimezone(dt.UTC)


def test_zero_delay_clamps_to_the_close_exactly():
    # A future real-time feed (delayed_minutes=0): the honest reading after the close is the
    # close itself, not close+15.
    captured_at = _ny(2026, 9, 4, 20, 0)
    assert effective_data_time(captured_at, 0) == _ny(2026, 9, 4, 16, 0).astimezone(dt.UTC)


def test_effective_data_time_never_mutates_input():
    """`captured_at` (the stored field) must stay exactly the vendor's raw value -- this
    function is read-time-only and must not be mistaken for a place that also rewrites
    storage.
    """
    captured_at = _ny(2026, 9, 4, 20, 0)
    before = captured_at
    effective_data_time(captured_at, 15)
    assert captured_at == before
