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

from app.jobs.calendar import is_market_holiday, is_trading_day


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
