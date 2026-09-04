"""US market holiday calendar for the EOD capture scheduler (T05).

MAINTENANCE — READ THIS BEFORE THE LIST RUNS OUT
--------------------------------------------------
``_HOLIDAYS_BY_YEAR`` below covers **2026 and 2027 only**. It must be extended with the
following year's NYSE/Cboe holiday dates well before December 31, 2027 (nyse.com publishes
each year's calendar more than a year in advance, so there is no reason to wait). This is a
hardcoded list by design (T05's brief) rather than a `holidays`-style package dependency —
simple, auditable, zero extra dependency — but that means it goes stale silently unless this
module is deliberately loud about it.

Why "loud, and biased toward capturing" rather than "loud, and refuse to guess"
--------------------------------------------------------------------------------
Two ways this can go wrong once the list is stale (i.e. asked about a year with no entry):

1. Treat the unknown day as a holiday (skip the capture) -> if the market was actually open,
   that day's data is lost **forever**. The free Cboe endpoint has no history; there is no
   backfill.
2. Treat the unknown day as open (attempt the capture) -> if the market was actually closed,
   the capture just makes one wasted HTTP call and stores a snapshot with a "closed day" flavor
   (typically the prior close echoed back, or a thin/odd chain) with no worse consequence than
   a cosmetic blemish in the data.

Outcome (1) is unrecoverable; outcome (2) is a rounding error. So :func:`is_market_holiday`
resolves "I don't know" as **not a holiday** (never skip on ignorance) while logging at
``ERROR`` every single time it happens, so a stale calendar shows up in logs/alerting long
before it costs a real trading day.
"""

from __future__ import annotations

import datetime as dt
import logging

__all__ = ["is_market_holiday", "is_trading_day"]

logger = logging.getLogger("app.jobs.calendar")

# NYSE/Cboe full-market-closure holidays (options and equities both closed). Early-close days
# (e.g. the day after Thanksgiving, Christmas Eve in some years) are deliberately NOT listed
# here: the market still opens and prints a real close, just earlier, so the 16:20 ET capture
# is not skipped on those days -- it just captures a chain that went quiet ~1-3 hours earlier
# than usual, which is a data-quality footnote, not a missed day.
#
# Dates below were derived from the standard NYSE holiday observance rules (fixed-date holidays
# move to the nearest weekday when they fall on a weekend; MLK/Washington's Birthday/Labor
# Day/Thanksgiving are the Nth weekday-of-month per the usual federal rule; Good Friday from
# the Easter date) — verify against nyse.com/markets/hours-calendars before trusting blindly in
# a year not shown here.
_HOLIDAYS_BY_YEAR: dict[int, frozenset[dt.date]] = {
    2026: frozenset(
        {
            dt.date(2026, 1, 1),  # New Year's Day
            dt.date(2026, 1, 19),  # Martin Luther King, Jr. Day
            dt.date(2026, 2, 16),  # Washington's Birthday
            dt.date(2026, 4, 3),  # Good Friday
            dt.date(2026, 5, 25),  # Memorial Day
            dt.date(2026, 6, 19),  # Juneteenth National Independence Day
            dt.date(2026, 7, 3),  # Independence Day (observed; July 4 is a Saturday)
            dt.date(2026, 9, 7),  # Labor Day
            dt.date(2026, 11, 26),  # Thanksgiving Day
            dt.date(2026, 12, 25),  # Christmas Day
        }
    ),
    2027: frozenset(
        {
            dt.date(2027, 1, 1),  # New Year's Day
            dt.date(2027, 1, 18),  # Martin Luther King, Jr. Day
            dt.date(2027, 2, 15),  # Washington's Birthday
            dt.date(2027, 3, 26),  # Good Friday
            dt.date(2027, 5, 31),  # Memorial Day
            dt.date(2027, 6, 18),  # Juneteenth (observed; June 19 is a Saturday)
            dt.date(2027, 7, 5),  # Independence Day (observed; July 4 is a Sunday)
            dt.date(2027, 9, 6),  # Labor Day
            dt.date(2027, 11, 25),  # Thanksgiving Day
            dt.date(2027, 12, 24),  # Christmas Day (observed; Dec 25 is a Saturday)
        }
    ),
}


def is_market_holiday(day: dt.date) -> bool:
    """Whether ``day`` is a full US market closure.

    Returns ``False`` (not a holiday) for any year not present in ``_HOLIDAYS_BY_YEAR``, but
    logs at ``ERROR`` when that happens — see the module docstring for why "assume open" is
    the safer default than "assume closed" for this application. This is the one function in
    the module a caller should use; :data:`_HOLIDAYS_BY_YEAR` is intentionally not exported.
    """
    year_holidays = _HOLIDAYS_BY_YEAR.get(day.year)
    if year_holidays is None:
        logger.error(
            "jobs.calendar: STALE HOLIDAY CALENDAR — no data for year %d (covers %s). "
            "Update _HOLIDAYS_BY_YEAR in app/jobs/calendar.py now. Treating %s as a trading "
            "day so the capture is not silently skipped.",
            day.year,
            sorted(_HOLIDAYS_BY_YEAR),
            day.isoformat(),
        )
        return False
    return day in year_holidays


def is_trading_day(day: dt.date) -> bool:
    """Whether US equity/options markets are open on ``day``: a weekday and not a holiday."""
    return day.weekday() < 5 and not is_market_holiday(day)
