"""Yahoo Finance chart-endpoint daily-bar provider (T42,
plans/continuation/00-foundation-daily-bars.md).

Default bars provider (``BARS_PROVIDER=yahoo``). Free, keyless, JSON:
``https://query1.finance.yahoo.com/v8/finance/chart/{symbol}``, queried here with
``period1``/``period2`` (epoch-second bounds) rather than the ``range=`` shorthand, so that
`BarProvider.fetch_daily_bars`'s `start`/`end` dates map directly onto the request instead of
this module reverse-engineering a `range` string from a date span. ``period1``/``period2`` were
independently re-confirmed live against this same endpoint on 2026-09-09 (three rows returned
for a ~4.5-day window); the endpoint's other documented quirks below were confirmed the same
day using the ``range=`` form the supervisor originally probed, and behave identically.

Live verification, 2026-09-09 (supervisor, reproduced here per T42's brief)
-----------------------------------------------------------------------------
Yahoo was verified live against SPY, ``^GSPC``, ``^VIX``, XLK, GDX, EWZ, XLRE, IWM, UNG, FXY
and ITB:

* **Success shape.** HTTP 200, ``application/json``. ``chart.result[0]`` holds ``meta``,
  ``timestamp`` (a list of epoch seconds) and ``indicators.quote[0]`` with parallel
  ``open/high/low/close/volume`` lists, plus ``indicators.adjclose[0].adjclose`` when
  ``includeAdjustedClose=true``. ``range=5y&interval=1d`` returned **1255 rows** for SPY.
* **Adjustment.** ``close`` is **split-adjusted, not dividend-adjusted**; ``adjclose`` is both.
  SPY 2021-09-09 read ``close`` 448.98 against ``adjclose`` 419.63. This module stores the
  OHLCV block as served — the split-adjusted series the plan wants — and sets ``source`` to
  ``yahoo-splitadj``. It does not store ``adjclose``, and never mixes the two for one symbol.
* **Unknown symbol.** HTTP **404** with a structured body: ``chart.result`` is ``null`` and
  ``chart.error`` is ``{"code": "Not Found", "description": "No data found, symbol may be
  delisted"}``. Mapped to :class:`~app.modules.gex.providers.bars.SymbolNotSupported`, not to a rate-limit
  error. A non-200 whose body is not that shape, and any 429, maps to
  :class:`~app.modules.gex.providers.bars.UpstreamUnavailable`. Re-confirmed live 2026-09-09 against a
  nonexistent symbol: identical body, byte for byte.
* **The last row is today's bar, and it is partial -- while the session is still open.**
  Probed intraday on 2026-09-09, SPY's final row carried ``volume`` 3,792,889 against a normal
  full day of ~44,000,000, and ``^GSPC`` 371,359,355 against ~4,966,930,000. Re-confirmed
  independently the same day via a fresh ``range=5d`` pull (fixture
  ``tests/fixtures/yahoo/spy_5d.json``): the final row's date is today (America/New_York) and
  its volume, 6,326,845, is again far below the ~44M full-day figure the four prior rows in the
  same fixture show. **The provider drops a bar dated strictly after today outright, and drops
  today's own bar only while today's session has not yet settled** (NY-local now before
  `MARKET_CLOSE` plus a short settle buffer, or today is not a trading day at all) — see
  :func:`YahooBarProvider._parse_payload`. The naive rule this replaced ("drop today's bar,
  unconditionally") looked right against an intraday probe but is wrong for the actual job:
  `bars_update_job` (T42) runs at 17:30 ET, 90 minutes *after* the close, when today's row is
  the complete, final bar -- dropping it unconditionally would have left `daily_bars`
  permanently one trading day stale on every single run, forever, silently mispairing every
  later join against same-day option data (T48) and every same-day breakout check (T43) with
  yesterday's price action. This is the single most damaging failure mode in the task and has
  its own dedicated tests in ``tests/test_yahoo.py``
  (``test_partial_today_bar_is_dropped_before_the_close``,
  ``test_todays_settled_bar_is_kept_after_the_close``,
  ``test_todays_bar_is_dropped_inside_the_settle_buffer``,
  ``test_todays_bar_is_dropped_on_a_non_trading_day_regardless_of_time``).
* **Dates come from the exchange timezone, not from UTC.** Timestamps are session-open
  instants: SPY rows are stamped 13:30 UTC (09:30 ET) and ``^VIX`` rows 07:00 UTC under
  ``exchangeTimezoneName: America/Chicago``. Converting an epoch to a UTC date happens to work
  for the ET names today and silently breaks on any row stamped after 19:00 ET. This module
  derives ``date`` by converting the epoch into ``meta.exchangeTimezoneName`` and taking
  ``.date()`` (:func:`YahooBarProvider._parse_payload`), never from a bare UTC conversion.
* ``^VIX`` **volume is always 0.** Treated as nullable throughout this app
  (``app.modules.gex.models.bars.DailyBar.volume``), and a zero here is passed through unchanged rather
  than read as a data error — CLAUDE.md invariant 3's None-vs-zero discipline, applied to
  volume instead of open interest.
* **SPX is** ``^GSPC`` **on Yahoo**, not ``^spx``. ``^SPX`` is not a Yahoo symbol. See
  :data:`_VENDOR_SYMBOL_OVERRIDES` — the only entry needed, since every other symbol in
  ``SCAN_UNIVERSE`` (ETF tickers, and ``^VIX`` which Yahoo already spells correctly) passes
  through unchanged. The mapping is purely a request-time detail: every `DailyBar` this module
  returns carries the plain ticker the caller asked for (``symbol="SPX"``), never ``^GSPC`` —
  see `app.modules.gex.providers.bars.BarProvider.fetch_daily_bars`'s own docstring for why that boundary
  matters (a vendor symbol leaking into stored data would break every later join on
  `daily_bars.symbol`).

Yahoo's chart endpoint is undocumented and carries no ToS blessing for programmatic use; the
user accepted that trade-off on 2026-09-09 over requiring a Tiingo signup (see
``app.modules.gex.providers.tiingo``, the token-guarded fallback). It is exactly why
`app.modules.gex.providers.bars.BarProvider` is not optional: when Yahoo goes the way Stooq just did (dead
as a keyless source — see the plan's "Live verification" section for the proof-of-work wall
Stooq now serves), the replacement is a new module plus a ``BARS_PROVIDER`` config change, and
nothing else.

Other failure modes this module guards against
--------------------------------------------------
* **Parallel nulls.** A halted or untraded day can leave `null` in the same index position
  across `open`/`high`/`low`/`close` inside `indicators.quote[0]`. A row where any of the four
  is `null` is skipped entirely, never stored as `0.0` — `app.modules.gex.models.bars.DailyBar`'s price
  fields have no `None` case to accidentally accept one through.
* **The ``^`` in ``^VIX``/``^GSPC`` breaking URL construction.** The vendor symbol is
  percent-encoded explicitly (`urllib.parse.quote`) before being formatted into the request
  path, rather than relying on whatever escaping `httpx.URL` happens to apply to a raw f-string
  — the vendor URL path segment is deliberately never handed to `httpx.URL`'s constructor
  unescaped.
* **A 200 whose body is not JSON** (a CDN error page or bot-challenge HTML, the same failure
  mode `app.modules.gex.providers.cboe` guards against) raises `UpstreamUnavailable` rather than letting a
  `json.JSONDecodeError` escape as a non-`ProviderError`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Self
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.modules.gex.jobs.calendar import MARKET_CLOSE, is_trading_day
from app.modules.gex.models.bars import DailyBar, IntradayBar, LiveQuote
from app.modules.gex.providers.bars import BarProvider, SymbolNotSupported, UpstreamUnavailable

__all__ = ["YahooBarProvider"]

logger = logging.getLogger(__name__)

_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo's endpoint is undocumented and, like Cboe's, occasionally rejects a bare httpx default
# user agent -- a browser-like one is cheap insurance, same rationale as app.modules.gex.providers.cboe.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# The only vendor symbol mapping this module needs -- see the module docstring's "SPX is
# ^GSPC" verification note. Every other SCAN_UNIVERSE ticker (ETFs, ^VIX) is already the Yahoo
# symbol verbatim.
_VENDOR_SYMBOL_OVERRIDES: dict[str, str] = {"SPX": "^GSPC"}

_NY = ZoneInfo("America/New_York")

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0

# Grace period after MARKET_CLOSE (16:00 ET) before today's own bar is trusted as final and
# admitted -- mirrors the spirit of app.modules.gex.jobs.catchup.EOD_CUTOFF's 16:20 grace. Exists so a
# fetch that happens to land right at 16:00:30 (clock skew, a slow request queued exactly at
# the close) cannot grab a not-quite-final row; the bars job itself runs at 17:30 ET, 90
# minutes past this cutoff, so it never depends on the buffer being tight.
_SETTLE_BUFFER_MINUTES = 20


def _vendor_symbol(requested_symbol: str) -> str:
    """Map a plain ticker to the symbol Yahoo's endpoint expects. See module docstring."""
    return _VENDOR_SYMBOL_OVERRIDES.get(requested_symbol, requested_symbol)


def _epoch_seconds(date: dt.date) -> int:
    """UTC-midnight epoch seconds for `date`. Used only as a `period1`/`period2` boundary, not
    as a data value -- session-open timestamps for every zone this app sees (America/New_York,
    America/Chicago) fall well inside a UTC-midnight-bounded day, so this never clips a real
    row.
    """
    return int(dt.datetime.combine(date, dt.time.min, tzinfo=dt.UTC).timestamp())


def _extract_chart_error(body: Any) -> dict[str, Any] | None:
    """`body["chart"]["error"]` if `body` has the shape Yahoo's 404 uses (`chart.result` is
    `null`, `chart.error` is a dict), else `None`. See the module docstring's "Unknown symbol"
    verification note for the exact shape.
    """
    if not isinstance(body, dict):
        return None
    chart = body.get("chart")
    if not isinstance(chart, dict) or chart.get("result") is not None:
        return None
    error = chart.get("error")
    return error if isinstance(error, dict) else None


#: Vendor interval string -> seconds. Used to tell an aligned bucket from the trailing
#: live-quote row: bucket epochs are exact multiples of the interval (verified 2026-09-11 --
#: 09:30:00, 09:35:00 ... 15:10:00) while the quote row carries the wall-clock instant it was
#: read (15:14:17). Only the intervals this app actually polls are listed; an unknown one is a
#: configuration error worth failing on rather than guessing a divisor for.
_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "90m": 5400,
}


def _interval_seconds(interval: str) -> int:
    try:
        return _INTERVAL_SECONDS[interval]
    except KeyError:
        raise ValueError(
            f"yahoo: unsupported intraday interval {interval!r}; "
            f"known: {sorted(_INTERVAL_SECONDS)}"
        ) from None


def _at(values: list[Any] | None, i: int) -> Any:
    """Index into one parallel OHLCV array, tolerating a short or absent array (same defensive
    shape as `app.modules.gex.providers.marketdata._at`)."""
    if values is None or i >= len(values):
        return None
    return values[i]


class YahooBarProvider(BarProvider):
    """Fetches daily OHLCV bars from Yahoo Finance's undocumented chart endpoint."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
        now_fn: Any = None,
    ) -> None:
        """
        Args:
            client: An existing `httpx.AsyncClient` to use instead of creating one lazily.
                Tests inject a client built on `httpx.MockTransport` so the suite never touches
                the network; the caller who injects a client also owns closing it.
            timeout: Per-request timeout in seconds.
            max_retries: Total attempts (not additional retries) before raising
                `UpstreamUnavailable`. A 404 is never retried (see `_fetch_chart`) -- retrying
                "no such symbol" cannot help.
            backoff_seconds: Base for the linear backoff between attempts (`0` for instant
                retries in tests).
            now_fn: Zero-arg callable returning the tz-aware "now" used to evaluate the
                settled-session drop rule (see module docstring): both today's NY-local
                *trading date* and the NY-local *time of day* matter, since today's own bar is
                admitted only once its session has settled. Defaults to
                `dt.datetime.now(dt.UTC)`. Tests inject a fixed value -- at various times of
                day, not just various dates -- so the drop/admit tests are deterministic
                regardless of when they actually run -- the live fixtures under
                `tests/fixtures/yahoo/` were captured on a specific date, and a test that relied
                on the real wall clock matching that date would eventually start failing for a
                reason that has nothing to do with the code.
        """
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._now_fn = now_fn or (lambda: dt.datetime.now(dt.UTC))

    @property
    def name(self) -> str:
        return "yahoo-splitadj"

    async def close(self) -> None:
        """Close the client this provider created. A no-op for an injected client."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, headers={"User-Agent": _USER_AGENT}
            )
        return self._client

    async def _fetch_chart(self, url: str, *, requested_symbol: str) -> dict[str, Any]:
        """GET `url` with retries and linear backoff, returning the parsed JSON body.

        A 404 is handled specially and never retried: per the module docstring's live
        verification, Yahoo's 404 for an unknown symbol is a structured, deterministic body
        (`chart.error` present, `chart.result` null) -- retrying cannot turn a nonexistent
        symbol into an existing one. Every other non-2xx (429 included, per the verification
        note) and a 200 with a non-JSON body both retry through the normal budget and then
        raise `UpstreamUnavailable`, same shape as `app.modules.gex.providers.cboe`'s `_fetch_json`.
        """
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(url)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "yahoo: attempt %d/%d transport error for %s: %s",
                    attempt,
                    self._max_retries,
                    requested_symbol,
                    exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 404:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise UpstreamUnavailable(
                        f"yahoo: 404 for {requested_symbol!r} with a non-JSON body"
                    ) from exc
                error = _extract_chart_error(body)
                if error is not None:
                    raise SymbolNotSupported(
                        f"yahoo: no data for {requested_symbol!r}: "
                        f"{error.get('description', error)}"
                    )
                raise UpstreamUnavailable(
                    f"yahoo: 404 for {requested_symbol!r} with an unrecognized body shape "
                    f"(not the documented chart.error 404)"
                )

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    last_exc = exc
                    logger.warning(
                        "yahoo: attempt %d/%d non-JSON 200 body for %s: %s",
                        attempt,
                        self._max_retries,
                        requested_symbol,
                        exc,
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(self._backoff_seconds * attempt)
                    continue

            # Any other status -- 429 explicitly included per the verification note, plus
            # every 5xx and any other unexpected 4xx -- is retryable, not a symbol-not-found.
            last_exc = httpx.HTTPStatusError(
                f"unexpected status {response.status_code}",
                request=response.request,
                response=response,
            )
            logger.warning(
                "yahoo: attempt %d/%d status %d for %s",
                attempt,
                self._max_retries,
                response.status_code,
                requested_symbol,
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * attempt)

        raise UpstreamUnavailable(
            f"yahoo request failed after {self._max_retries} attempts: {requested_symbol}"
        ) from last_exc

    async def fetch_daily_bars(
        self, symbol: str, *, start: dt.date, end: dt.date | None = None
    ) -> list[DailyBar]:
        requested_symbol = symbol.strip().upper()
        vendor_symbol = _vendor_symbol(requested_symbol)

        now_ny = self._now_fn().astimezone(_NY)
        effective_end = end if end is not None else now_ny.date()

        params = {
            "interval": "1d",
            "includeAdjustedClose": "true",
            # +1 day on the upper bound: period2 is compared against each row's session-open
            # timestamp, which sits *after* midnight on its own date (13:30 UTC for NY names,
            # 07:00 UTC for Chicago) -- an exact midnight-of-`effective_end` bound would exclude
            # that date's own row. Verified live 2026-09-09 with a narrow period1/period2 window.
            "period1": str(_epoch_seconds(start)),
            "period2": str(_epoch_seconds(effective_end + dt.timedelta(days=1))),
        }
        # The vendor symbol is percent-encoded explicitly rather than left for httpx.URL to
        # escape from an f-string -- see module docstring's "^ breaking URL construction" note.
        url = httpx.URL(
            _BASE_URL.format(symbol=quote(vendor_symbol, safe="")), params=params
        )
        payload = await self._fetch_chart(str(url), requested_symbol=requested_symbol)
        return self._parse_payload(requested_symbol, payload, now_ny=now_ny)

    async def fetch_intraday_bars(
        self, symbol: str, *, interval: str = "5m", lookback: str = "1d"
    ) -> tuple[list[IntradayBar], LiveQuote | None]:
        """Fetch interval-aligned intraday buckets plus the vendor's trailing live quote (T74).

        Uses the `range=` shorthand rather than `period1`/`period2`, unlike `fetch_daily_bars`:
        the vendor caps intraday history by interval (roughly 7 days at `1m`, 60 at `5m`) and
        rejects or silently truncates an epoch window that exceeds it, so asking in the vendor's
        own units is the honest request. `lookback="1d"` returns the current session, which is
        what the five-minute poll wants -- the whole session every time, so a missed poll
        self-heals on the next one rather than leaving a hole.

        Returns:
            `(bars, quote)`. `bars` is ascending and contains **only** rows whose timestamp
            falls on an `interval` boundary. `quote` is the unaligned trailing row when the
            vendor appended one (it does so during a session), else `None`.

            The split is the whole point of this method. Verified live 2026-09-11 at 15:14 ET:
            a `5m`/`1d` pull returned aligned buckets at 09:30, 09:35 ... 15:10 and then one row
            stamped 15:14:17 with `volume = 0` carrying the current quote. That row is not a
            bucket; persisting it would drop a zero-volume bar at a ragged timestamp into the
            middle of the series. See `plans/continuous-feed/05-intraday-bars.md`.

        Raises:
            SymbolNotSupported, UpstreamUnavailable: exactly as `fetch_daily_bars`.
        """
        requested_symbol = symbol.strip().upper()
        vendor_symbol = _vendor_symbol(requested_symbol)
        url = httpx.URL(
            _BASE_URL.format(symbol=quote(vendor_symbol, safe="")),
            params={"interval": interval, "range": lookback},
        )
        payload = await self._fetch_chart(str(url), requested_symbol=requested_symbol)
        return self._parse_intraday_payload(requested_symbol, payload, interval=interval)

    def _parse_intraday_payload(
        self, requested_symbol: str, payload: dict[str, Any], *, interval: str
    ) -> tuple[list[IntradayBar], LiveQuote | None]:
        """Split one intraday chart body into aligned buckets and the trailing live quote."""
        try:
            results = payload["chart"]["result"]
            if not results:
                raise UpstreamUnavailable(
                    f"yahoo: empty chart.result for {requested_symbol!r}"
                )
            result = results[0]
            tz_name = result["meta"]["exchangeTimezoneName"]
            timestamps = result.get("timestamp") or []
            quote_block = result["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise UpstreamUnavailable(
                f"yahoo: intraday response for {requested_symbol!r} is missing an expected field"
            ) from exc

        try:
            exchange_tz = ZoneInfo(tz_name)
        except Exception as exc:
            raise UpstreamUnavailable(
                f"yahoo: unrecognized exchangeTimezoneName {tz_name!r} for {requested_symbol!r}"
            ) from exc

        interval_seconds = _interval_seconds(interval)
        opens = quote_block.get("open") or []
        highs = quote_block.get("high") or []
        lows = quote_block.get("low") or []
        closes = quote_block.get("close") or []
        volumes = quote_block.get("volume") or []

        bars: list[IntradayBar] = []
        live: LiveQuote | None = None

        for i, epoch in enumerate(timestamps):
            close = _at(closes, i)
            if close is None or close <= 0:
                # A null row is a gap the vendor published, not a bar. Same policy as the
                # daily parser: skip rather than interpolate.
                continue
            moment = dt.datetime.fromtimestamp(epoch, dt.UTC)

            if epoch % interval_seconds != 0:
                # The trailing live-quote row. Only ever the last one in practice, but the
                # check is per row rather than positional so a vendor that ever inserts one
                # mid-payload cannot smuggle it into the series.
                live = LiveQuote(
                    symbol=requested_symbol, ts=moment, price=close, source=self.name
                )
                continue

            open_, high, low = _at(opens, i), _at(highs, i), _at(lows, i)
            if open_ is None or high is None or low is None or min(open_, high, low) <= 0:
                continue
            volume = _at(volumes, i)
            bars.append(
                IntradayBar(
                    symbol=requested_symbol,
                    interval=interval,
                    ts=moment,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=int(volume) if volume is not None else None,
                    source=self.name,
                )
            )

        # `exchange_tz` is resolved above and deliberately not used to derive `ts`: unlike a
        # daily bar, whose identity is an exchange-local *date*, a bucket's identity is an
        # instant, and an instant is the same instant in every zone. Resolving the zone anyway
        # keeps the "unrecognized timezone means the payload is wrong" check that the daily
        # path relies on -- ^VIX arrives as America/Chicago and must not be assumed otherwise.
        del exchange_tz
        bars.sort(key=lambda b: b.ts)
        return bars, live

    def _parse_payload(
        self, requested_symbol: str, payload: dict[str, Any], *, now_ny: dt.datetime
    ) -> list[DailyBar]:
        """Turn one chart-endpoint success body into ascending `DailyBar`s for
        `requested_symbol`, dropping a not-yet-settled bar (or any future-dated one) and any
        row with a null OHLC field. See the module docstring for both failure modes.

        `now_ny` is a full tz-aware instant, not just a date: whether *today's* bar is admitted
        depends on the time of day, not merely the date -- see the settled-session rule below.
        """
        try:
            results = payload["chart"]["result"]
            if not results:
                raise UpstreamUnavailable(
                    f"yahoo: empty chart.result for {requested_symbol!r}"
                )
            result = results[0]
            tz_name = result["meta"]["exchangeTimezoneName"]
            timestamps = result["timestamp"]
            quote_block = result["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise UpstreamUnavailable(
                f"yahoo: response for {requested_symbol!r} is missing an expected field"
            ) from exc

        try:
            exchange_tz = ZoneInfo(tz_name)
        except Exception as exc:  # any zoneinfo failure (bad tz name) is an upstream problem
            raise UpstreamUnavailable(
                f"yahoo: unrecognized exchangeTimezoneName {tz_name!r} for {requested_symbol!r}"
            ) from exc

        opens = quote_block.get("open")
        highs = quote_block.get("high")
        lows = quote_block.get("low")
        closes = quote_block.get("close")
        volumes = quote_block.get("volume")

        # Settled-session rule (see module docstring's "today's bar is admitted once the
        # session has settled" note -- this is the corrected version of the original "drop
        # today's bar unconditionally" rule, which left the store permanently one trading day
        # stale because the 17:30 ET job runs *after* the close, not during it). Today's bar
        # is trusted only once NY-local now is on a trading day and at or past
        # MARKET_CLOSE + _SETTLE_BUFFER_MINUTES; on a non-trading day (weekend/holiday) it is
        # never trusted, regardless of the time of day, since there is no real close to settle
        # against.
        today_ny = now_ny.date()
        settle_cutoff = (
            dt.datetime.combine(today_ny, MARKET_CLOSE, tzinfo=_NY)
            + dt.timedelta(minutes=_SETTLE_BUFFER_MINUTES)
        )
        today_is_settled = is_trading_day(today_ny) and now_ny >= settle_cutoff

        bars: list[DailyBar] = []
        dropped_partial = 0
        skipped_null = 0
        for i, ts in enumerate(timestamps):
            o, h, low, c = _at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i)
            if o is None or h is None or low is None or c is None:
                # A halted or untraded day: the module docstring's "parallel nulls" failure
                # mode. Skipped, never stored as a fabricated 0.0.
                skipped_null += 1
                continue

            # Session-open epoch -> exchange-local date, NOT a bare UTC .date() -- the module
            # docstring's "dates come from the exchange timezone" verification note. This is
            # the one line that must use `exchange_tz`, not `_NY`: `^VIX` rows are stamped
            # under America/Chicago and a UTC or NY conversion would misdate them.
            bar_date = dt.datetime.fromtimestamp(ts, tz=dt.UTC).astimezone(exchange_tz).date()

            if bar_date > today_ny or (bar_date == today_ny and not today_is_settled):
                # A future-dated row (clock skew, a bad vendor row) is always dropped. Today's
                # own row is dropped only while its session has not yet settled -- see the
                # settled-session rule computed above. `today_ny`/`today_is_settled` are
                # NY-based regardless of `exchange_tz` deliberately: the cutoff is about *this
                # app's* trading calendar (the 17:30 ET job, MARKET_CLOSE), not about whichever
                # zone the vendor happens to stamp this particular symbol in.
                dropped_partial += 1
                continue

            v = _at(volumes, i)
            try:
                bars.append(
                    DailyBar(
                        symbol=requested_symbol,
                        date=bar_date,
                        open=float(o),
                        high=float(h),
                        low=float(low),
                        close=float(c),
                        volume=None if v is None else round(float(v)),
                        source=self.name,
                    )
                )
            except ValidationError as exc:
                # A schema-rejected row (e.g. a non-positive price) is logged and skipped, same
                # unknown-root-style policy as app.modules.gex.providers.cboe: one bad row must not blank
                # out the whole fetch.
                skipped_null += 1
                logger.warning(
                    "yahoo: skipping %s row dated %s: %s", requested_symbol, bar_date, exc
                )

        if dropped_partial:
            logger.info(
                "yahoo: dropped %d in-progress/future-dated row(s) for %s (today_ny=%s)",
                dropped_partial,
                requested_symbol,
                today_ny.isoformat(),
            )
        if skipped_null:
            logger.warning(
                "yahoo: skipped %d row(s) for %s with a null OHLC field or a schema rejection",
                skipped_null,
                requested_symbol,
            )

        bars.sort(key=lambda b: b.date)
        return bars


async def _run_cli(symbol: str, years: int) -> None:
    today = dt.datetime.now(dt.UTC).date()
    async with YahooBarProvider() as provider:
        bars = await provider.fetch_daily_bars(
            symbol, start=today - dt.timedelta(days=365 * years)
        )
    print(f"{symbol}: {len(bars)} bars")
    if bars:
        print(f"  first={bars[0].date} last={bars[-1].date}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) not in (2, 3):
        print("usage: python -m app.modules.gex.providers.yahoo SYMBOL [years]", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_run_cli(sys.argv[1], int(sys.argv[2]) if len(sys.argv) == 3 else 5))
