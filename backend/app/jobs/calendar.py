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

Regular-session helpers (T34)
------------------------------
:func:`is_regular_session` and :func:`effective_data_time` exist because Cboe's own
``timestamp`` field is *payload-generation* time, not *data-effective* time (verified
2026-09-04: at 17:55 ET, nearly two hours after the 16:00 close, the vendor timestamp read
17:54:46 ET and kept advancing on every request while ``data.current_price`` stayed frozen at
the close). ``app.providers.cboe`` still records that vendor timestamp verbatim as
``ChainSnapshot.captured_at`` -- changing its value would ripple into the NY-calendar-day
bucketing several callers depend on (the duplicate-capture check in `app.jobs.capture`, T29's
`has_eod_snapshot_today`, the `levels/history` date-range query) -- but anything that wants an
*honest* "as of" instant for a staleness badge should call :func:`effective_data_time` instead
of displaying `captured_at` directly. This module is the one place that decision belongs,
per T34's brief: the frontend must not reimplement market-hours logic that the backend already
owns here.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from app.config import settings

__all__ = [
    "MARKET_CLOSE",
    "MARKET_OPEN",
    "effective_data_time",
    "is_market_holiday",
    "is_regular_session",
    "is_trading_day",
]

logger = logging.getLogger("app.jobs.calendar")

# `settings.TZ` (default "America/New_York") -- same rationale as `app/jobs/catchup.py`'s own
# `_TZ`: the open/close times below are NY-local by definition and must move with wherever this
# app considers "NY local", not stay pinned to America/New_York if that setting ever changes.
_TZ = ZoneInfo(settings.TZ)

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


#: Regular session bounds, NY local. Deliberately the exchange's actual open/close (09:30 /
#: 16:00), not ``app.jobs.catchup.EOD_CUTOFF`` (16:20) -- that constant is a scheduling grace
#: period for *when the EOD job is allowed to run*, a different concept from *when the market
#: itself was open*, and conflating them would clamp a legitimate 16:05 intraday capture (T18)
#: down to the previous day's close.
MARKET_OPEN = dt.time(9, 30)
MARKET_CLOSE = dt.time(16, 0)


def _previous_trading_day(day: dt.date) -> dt.date:
    """The most recent trading day strictly before ``day``.

    Private mirror of ``app.jobs.catchup.previous_trading_day`` -- duplicated rather than
    imported to avoid a circular import (``catchup`` already imports ``is_trading_day`` from
    this module) and because the walk is a five-line loop, cheaper to repeat than to justify a
    module reshuffle that would also have to move ``catchup.last_completed_trading_day`` and
    its existing test coverage.
    """
    candidate = day - dt.timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= dt.timedelta(days=1)
    return candidate


def is_regular_session(moment: dt.datetime) -> bool:
    """Whether NY markets are in their 09:30-16:00 regular session at ``moment`` (tz-aware,
    any zone -- converted to NY local here).
    """
    local = moment.astimezone(_TZ)
    return is_trading_day(local.date()) and MARKET_OPEN <= local.time() <= MARKET_CLOSE


def effective_data_time(captured_at: dt.datetime, delayed_minutes: int) -> dt.datetime:
    """The honest instant a captured chain reflects -- what a staleness badge should show,
    as opposed to ``captured_at`` itself (see the module docstring's T34 section).

    During a regular session, a delayed vendor timestamp *is* the honest reading (data really
    is ``delayed_minutes`` old relative to ``captured_at``), so this returns ``captured_at``
    unchanged -- the T18 intraday case this must not break.

    Outside a regular session (after today's close, before today's open, or on a weekend or
    holiday), the underlying chain has been frozen since the last close no matter how much
    later ``captured_at`` claims to be, so this clamps to that close instant plus
    ``delayed_minutes`` -- 16:15 ET for the free Cboe feed. ``captured_at`` itself is never
    mutated or restored from this; storage keeps recording the raw vendor value (see
    ``app.models.chain.ChainSnapshot.captured_at``) so this is purely a derived, read-time
    value.

    Args:
        captured_at: Tz-aware vendor timestamp, any zone.
        delayed_minutes: The feed's entitlement delay (0 for real-time).

    Returns:
        Tz-aware, normalized to UTC.
    """
    if is_regular_session(captured_at):
        return captured_at.astimezone(dt.UTC)

    local = captured_at.astimezone(_TZ)
    day = local.date()
    close_day = day if is_trading_day(day) and local.time() > MARKET_CLOSE else _previous_trading_day(day)
    close_ny = dt.datetime.combine(close_day, MARKET_CLOSE, tzinfo=_TZ)
    return (close_ny + dt.timedelta(minutes=delayed_minutes)).astimezone(dt.UTC)
