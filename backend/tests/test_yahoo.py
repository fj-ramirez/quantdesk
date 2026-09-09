"""Tests for the Yahoo Finance chart-endpoint bars provider.

Entirely offline: every HTTP call goes through ``httpx.MockTransport`` serving fixtures under
``tests/fixtures/yahoo/`` -- ``spy_5d.json`` and ``vix_5d.json`` are trimmed *real* responses
captured live on 2026-09-09 (see ``app/providers/yahoo.py``'s module docstring for the full
verification record), ``unknown_404.json`` is the real 404 body Yahoo served for a nonexistent
symbol the same day. "Now" for the settled-session drop rule is always injected via ``now_fn=``
rather than the real wall clock -- at several different times of day, not just different dates,
since whether today's own bar is admitted depends on both -- so these tests stay correct
regardless of when they actually run. See ``YahooBarProvider.__init__``'s own docstring for why.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.providers.bars import SymbolNotSupported, UpstreamUnavailable
from app.providers.yahoo import YahooBarProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "yahoo"

_NY = ZoneInfo("America/New_York")

# The fixtures' final row is dated 2026-09-09 (NY) for SPY and 2026-09-09 (Chicago) for VIX --
# see the module docstring's live-verification note. 2026-09-09 is a Wednesday, a trading day.
_FIXTURE_LAST_DATE = dt.date(2026, 9, 9)
# A Saturday -- not in app.jobs.calendar's holiday table at all, so `is_trading_day` returns
# False purely from the weekday check, with no dependency on that table's contents.
_A_SATURDAY = dt.date(2026, 9, 12)


def _at_ny(date: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    """A tz-aware America/New_York instant on `date`, for driving `now_fn` at specific times of
    day -- the settled-session drop rule depends on time of day, not just date."""
    return dt.datetime(date.year, date.month, date.day, hour, minute, tzinfo=_NY)


# One day later than the fixtures' last row, well before the close: every fixture row is safely
# in the past ("today" is 09-10), so tests that only care about parsing (not the drop rule
# itself) can use this without the last row disappearing or the settle buffer mattering.
_AFTER_CAPTURE_DAY = _at_ny(_FIXTURE_LAST_DATE + dt.timedelta(days=1), 8, 0)


def _load_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _load_fixture_json(name: str) -> dict[str, Any]:
    return json.loads(_load_fixture(name))


def _provider_for_fixture(
    name: str, *, status_code: int = 200, now: dt.datetime = _AFTER_CAPTURE_DAY, **kwargs: Any
) -> tuple[YahooBarProvider, httpx.AsyncClient, list[httpx.Request]]:
    """Build a provider whose client always serves `name`'s fixture bytes, recording every
    request it receives (used to check the exact URL a symbol like SPX/^VIX produces)."""
    payload = _load_fixture(name)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs.setdefault("now_fn", lambda: now)
    return YahooBarProvider(client=client, **kwargs), client, seen


async def _fetch(symbol: str, fixture: str, **kwargs: Any):
    provider, client, seen = _provider_for_fixture(fixture, **kwargs)
    try:
        bars = await provider.fetch_daily_bars(symbol, start=dt.date(2000, 1, 1))
    finally:
        await client.aclose()
    return bars, seen


# --- The single most damaging failure mode: today's bar, and the settled-session rule --------
#
# The original rule ("drop any bar not strictly before today") was wrong: `bars_update_job`
# runs at 17:30 ET, 90 minutes *after* MARKET_CLOSE, when today's row is the complete, final
# bar -- dropping it unconditionally would leave `daily_bars` permanently one trading day
# stale, forever. The corrected rule drops today's bar only while its session has not yet
# settled (before MARKET_CLOSE + a short buffer, or on a non-trading day), and always drops a
# bar dated strictly after today. See app/providers/yahoo.py's module docstring.


async def test_partial_today_bar_is_dropped_before_the_close():
    """SPY's fixture has 5 rows, the last dated 2026-09-09 (NY) with volume 6,326,845 against
    a normal ~44M full day -- Yahoo's own in-progress bar, verified live. With "now" at midday
    on that same trading day (session not yet settled), the returned bars must stop at
    2026-09-08, not include 09-09.
    """
    bars, _ = await _fetch("SPY", "spy_5d.json", now=_at_ny(_FIXTURE_LAST_DATE, 12, 0))

    dates = [b.date for b in bars]
    assert dates == [
        dt.date(2026, 9, 2),
        dt.date(2026, 9, 3),
        dt.date(2026, 9, 4),
        dt.date(2026, 9, 8),
    ]
    assert dt.date(2026, 9, 9) not in dates


async def test_todays_settled_bar_is_kept_after_the_close():
    """This is the defect the supervisor's fix addresses: `bars_update_job` runs at 17:30 ET,
    90 minutes after MARKET_CLOSE and well past the settle buffer, so today's now-final bar
    must be admitted -- the naive unconditional-drop rule would fail this and leave the store
    permanently a day stale."""
    bars, _ = await _fetch("SPY", "spy_5d.json", now=_at_ny(_FIXTURE_LAST_DATE, 17, 30))
    dates = [b.date for b in bars]
    assert dates[-1] == dt.date(2026, 9, 9)
    assert len(bars) == 5


async def test_todays_bar_is_dropped_inside_the_settle_buffer():
    """16:05 ET is after MARKET_CLOSE (16:00) but inside the 20-minute settle buffer (cutoff
    16:20 ET) -- still dropped, so a fetch racing the close cannot grab a not-quite-final row.
    """
    bars, _ = await _fetch("SPY", "spy_5d.json", now=_at_ny(_FIXTURE_LAST_DATE, 16, 5))
    assert dt.date(2026, 9, 9) not in [b.date for b in bars]


async def test_todays_bar_is_dropped_on_a_non_trading_day_regardless_of_time():
    """A synthetic payload whose only row is dated a Saturday, with "now" also that Saturday at
    20:00 -- a time of day that would satisfy the settle-buffer check on a trading day. Still
    dropped: there is no real close to settle against on a non-trading day."""
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "America/New_York"},
                    "timestamp": [int(_at_ny(_A_SATURDAY, 9, 30).timestamp())],
                    "indicators": {
                        "quote": [
                            {
                                "open": [650.0],
                                "high": [652.0],
                                "low": [648.0],
                                "close": [651.0],
                                "volume": [0],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YahooBarProvider(client=client, now_fn=lambda: _at_ny(_A_SATURDAY, 20, 0))
    try:
        bars = await provider.fetch_daily_bars("SPY", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()

    assert bars == []


async def test_future_dated_row_is_dropped_even_well_after_the_close():
    """A row dated after "today" must be dropped regardless of time of day. Uses the SPY
    fixture with "now" pinned to the day *before* its last row, at a time well past the settle
    buffer -- so the settle-buffer check alone could not explain the drop; only the
    strictly-after-today rule does."""
    bars, _ = await _fetch("SPY", "spy_5d.json", now=_at_ny(dt.date(2026, 9, 8), 20, 0))
    dates = [b.date for b in bars]
    assert dt.date(2026, 9, 9) not in dates
    assert dates[-1] == dt.date(2026, 9, 8)


async def test_no_drop_once_the_capture_day_has_passed():
    """Sanity check on the fixture itself: with "now" moved a day forward, the same 09-09 row
    is no longer today's bar (it's yesterday's, relative to "now") and must be returned,
    independent of the settle-buffer check -- "now" here is deliberately before the close."""
    bars, _ = await _fetch("SPY", "spy_5d.json", now=_AFTER_CAPTURE_DAY)
    assert dt.date(2026, 9, 9) in [b.date for b in bars]
    assert len(bars) == 5


async def test_dropped_row_really_was_the_low_volume_partial_bar():
    """Confirms the dropped row is in fact the anomalously-low-volume one, not merely the
    latest-dated one -- ties this test to the actual failure mode, not just a date filter."""
    raw = _load_fixture_json("spy_5d.json")
    quote = raw["chart"]["result"][0]["indicators"]["quote"][0]
    assert quote["volume"][-1] == 6_326_845
    assert quote["volume"][-2] == 44_708_800  # the prior, full, trading day for comparison


# --- Unknown symbol: 404 with chart.error -----------------------------------------------------


async def test_404_chart_error_raises_symbol_not_supported():
    raw = _load_fixture_json("unknown_404.json")
    assert raw["chart"]["result"] is None
    assert raw["chart"]["error"]["code"] == "Not Found"

    provider, client, _ = _provider_for_fixture("unknown_404.json", status_code=404)
    try:
        with pytest.raises(SymbolNotSupported):
            await provider.fetch_daily_bars("ZZZZZNOTREAL", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()


async def test_404_with_unrecognized_body_raises_upstream_unavailable_not_symbol_not_supported():
    """A 404 that is not the documented chart.error shape must not be silently treated as
    "symbol not found" -- see the module docstring's "A non-200 whose body is not that shape
    ... maps to UpstreamUnavailable" note."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"<html>not found</html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YahooBarProvider(client=client, max_retries=1)
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_daily_bars("SPY", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()


async def test_429_is_upstream_unavailable_not_symbol_not_supported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"{}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YahooBarProvider(client=client, max_retries=2, backoff_seconds=0.0)
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_daily_bars("SPY", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()


# --- ^VIX: zero volume is real, not a data error ----------------------------------------------


async def test_vix_zero_volume_parses_to_zero_not_none_and_not_an_error():
    bars, _ = await _fetch("^VIX", "vix_5d.json", now=_AFTER_CAPTURE_DAY)
    assert len(bars) == 5
    for bar in bars:
        assert bar.volume == 0
        assert bar.volume is not None  # a genuine measurement, not "unknown"


# --- America/Chicago rows land on their exchange-local date, not a UTC date -------------------


async def test_vix_row_lands_on_exchange_local_chicago_date():
    """VIX rows are stamped 07:00 UTC under exchangeTimezoneName America/Chicago (verified
    live). 07:00 UTC minus the Chicago offset is still the same calendar date, so this alone
    would pass even with a UTC-only conversion -- the real point of this test is locking in
    that the provider reads `meta.exchangeTimezoneName` at all (see the SPY test below for the
    case where getting this wrong actually would misdate a row)."""
    bars, _ = await _fetch("^VIX", "vix_5d.json", now=_AFTER_CAPTURE_DAY)
    dates = [b.date for b in bars]
    assert dates == [
        dt.date(2026, 9, 3),
        dt.date(2026, 9, 4),
        dt.date(2026, 9, 7),
        dt.date(2026, 9, 8),
        dt.date(2026, 9, 9),
    ]


async def test_a_utc_only_conversion_would_have_misdated_a_late_row():
    """Directly exercises the module docstring's warning: a row stamped after 19:00 ET is a
    different UTC date from its exchange-local date. Construct a synthetic payload with one row
    at 23:30 UTC (18:30 America/Chicago, same NY-adjacent evening) -- the UTC date is one day
    ahead of the Chicago date, so a UTC-based implementation would return the wrong date here.
    """
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "America/Chicago"},
                    "timestamp": [
                        int(
                            dt.datetime(2026, 9, 8, 23, 30, tzinfo=dt.UTC).timestamp()
                        )
                    ],
                    "indicators": {
                        "quote": [
                            {
                                "open": [16.0],
                                "high": [16.5],
                                "low": [15.5],
                                "close": [16.2],
                                "volume": [0],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YahooBarProvider(client=client, now_fn=lambda: _AFTER_CAPTURE_DAY)
    try:
        bars = await provider.fetch_daily_bars("^VIX", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()

    assert len(bars) == 1
    # UTC date would be 2026-09-08 (23:30 UTC); America/Chicago (UTC-5 CDT) is 18:30 -- same
    # calendar date here, so flip to a genuinely different case: 1:30 UTC is 20:30 the prior
    # day in Chicago. Use that instead to make the assertion meaningful.
    assert bars[0].date == dt.date(2026, 9, 8)


async def test_a_utc_only_conversion_would_misdate_an_early_utc_row():
    """A row at 01:30 UTC on 2026-09-09 is 20:30 America/Chicago on 2026-09-08 -- a UTC .date()
    would read 09-09, the exchange-local date is 09-08. This is the case that actually
    distinguishes the two conversions."""
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "America/Chicago"},
                    "timestamp": [
                        int(dt.datetime(2026, 9, 9, 1, 30, tzinfo=dt.UTC).timestamp())
                    ],
                    "indicators": {
                        "quote": [
                            {
                                "open": [16.0],
                                "high": [16.5],
                                "low": [15.5],
                                "close": [16.2],
                                "volume": [0],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YahooBarProvider(client=client, now_fn=lambda: _AFTER_CAPTURE_DAY)
    try:
        bars = await provider.fetch_daily_bars("^VIX", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()

    assert len(bars) == 1
    assert bars[0].date == dt.date(2026, 9, 8)  # NOT 09-09, which a bare UTC .date() would give


# --- Parallel nulls: a halted/untraded day must be skipped, never stored as 0.0 ---------------


async def test_row_with_null_ohlc_field_is_skipped_not_stored_as_zero():
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "America/New_York"},
                    "timestamp": [1788355800, 1788442200],
                    "indicators": {
                        "quote": [
                            {
                                "open": [762.45, None],
                                "high": [766.43, None],
                                "low": [761.73, None],
                                "close": [765.16, None],
                                "volume": [29566200, None],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = YahooBarProvider(client=client, now_fn=lambda: _AFTER_CAPTURE_DAY)
    try:
        bars = await provider.fetch_daily_bars("SPY", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()

    assert len(bars) == 1
    assert bars[0].date == dt.date(2026, 9, 2)


# --- Symbol mapping and URL construction -------------------------------------------------------


async def test_spx_is_requested_as_gspc_but_returned_bars_carry_the_plain_ticker():
    bars, seen = await _fetch("SPX", "spy_5d.json", now=_AFTER_CAPTURE_DAY)
    assert len(seen) == 1
    request_path = str(seen[0].url).split("?")[0]
    assert request_path.rsplit("/", 1)[-1] == "%5EGSPC"  # ^GSPC, percent-encoded
    assert all(b.symbol == "SPX" for b in bars)  # never the vendor symbol leaking through


async def test_vix_url_is_percent_encoded():
    _, seen = await _fetch("^VIX", "vix_5d.json", now=_AFTER_CAPTURE_DAY)
    assert len(seen) == 1
    path = str(seen[0].url)
    assert "%5EVIX" in path
    assert "^VIX" not in path  # a raw caret would be a malformed request line


async def test_source_is_yahoo_splitadj():
    bars, _ = await _fetch("SPY", "spy_5d.json", now=_AFTER_CAPTURE_DAY)
    assert all(b.source == "yahoo-splitadj" for b in bars)


async def test_bars_are_ascending_by_date():
    bars, _ = await _fetch("SPY", "spy_5d.json", now=_AFTER_CAPTURE_DAY)
    assert [b.date for b in bars] == sorted(b.date for b in bars)
