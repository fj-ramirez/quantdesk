"""Tiingo daily-prices provider skeleton (T42,
plans/continuation/00-foundation-daily-bars.md).

Optional second bars provider, selected by ``BARS_PROVIDER=tiingo`` (or routed to specific
symbols via ``BAR_PROVIDER_GROUPS``, see `app.modules.gex.providers.bars`), mirroring how
``app.modules.gex.providers.marketdata.MarketDataProvider`` is Cboe's break-glass fallback: gated behind a
token, exercised rarely, and — like that module — reviewed against Tiingo's own published docs
rather than integration-tested, because **there is no Tiingo account or token available in this
environment.** The task brief only requires this skeleton (credential guard, request shape,
response parsing reviewed against docs); a fuller implementation is welcome later but must be
verified against a live token before being trusted the way `app.modules.gex.providers.yahoo` now is.

Endpoint (https://www.tiingo.com/documentation/end-of-day, reviewed 2026-09-09):
``GET https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate=YYYY-MM-DD&token=...``.
Response is a JSON **list** of objects, one per trading day, each carrying both unadjusted
(``open``/``high``/``low``/``close``/``volume``) and adjusted
(``adjOpen``/``adjHigh``/``adjLow``/``adjClose``/``adjVolume``) fields, plus ``date`` as an ISO
string with a trailing ``00:00:00.000Z``.

**Known limitation, not resolved here: Tiingo has no split-only-adjusted series.** This app's
adjustment policy (set by the Yahoo provider's live verification, see its module docstring) is
"split-adjusted, never dividend-adjusted, to match a user's chart and not silently change past
highs/lows when a dividend is later declared." Tiingo's unadjusted OHLC is *not*
split-adjusted (a real historical stock split leaves a visible price jump), and its ``adj*``
fields are split **and** dividend adjusted — Tiingo does not offer the third option. Rather
than silently picking one and mislabeling it, this module stores the unadjusted series verbatim
under ``source="tiingo-raw"`` — a distinct, honestly-named value from ``"yahoo-splitadj"`` — so
a caller mixing sources for one symbol is visibly comparing two different adjustment policies,
not silently corrupting one series. If Tiingo ever needs to become primary, resolving this
(e.g. reconstructing a split-only series from ``splitFactor``) is separate work, not a T42
scope item — see the plan's "Out of scope" section.

Symbol handling: Tiingo tickers are the plain ticker with no known ``^``-prefixed index form in
the free EOD product (index data is a separate, paid Tiingo product this app does not use), so
``^VIX``/``SPX`` routing to this provider is unverified and likely wrong; `BAR_PROVIDER_GROUPS`
should not route those symbols here until someone checks with a real token.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Self

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.modules.gex.models.bars import DailyBar
from app.modules.gex.providers.bars import (
    BarProvider,
    ProviderError,
    SymbolNotSupported,
    UpstreamUnavailable,
)

__all__ = ["MissingCredential", "TiingoBarProvider"]

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0


class MissingCredential(ProviderError):
    """No ``TIINGO_TOKEN`` configured. Raised eagerly in `__init__`, same rationale as
    `app.modules.gex.providers.marketdata.MissingCredential`: a blank token header is a confusing vendor
    401 to debug, this names the exact setting to fix instead.
    """


def _parse_date(raw: str) -> dt.date:
    """Tiingo's ``date`` is an ISO datetime string (``"2019-01-02T00:00:00.000Z"``); only the
    calendar date matters for a daily bar."""
    return dt.date.fromisoformat(raw[:10])


class TiingoBarProvider(BarProvider):
    """Fetches unadjusted daily OHLCV bars from Tiingo's EOD prices endpoint.

    Unverified against the live API — see the module docstring's provenance note. 50
    symbols/hour on the free tier (per Tiingo's published limits page), which this module does
    not itself rate-limit; a caller driving many symbols through this provider (the bars
    backfill CLI, T42) is responsible for pacing requests — see its ``--sleep`` option.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        """
        Args:
            token: Tiingo API token. Defaults to `settings.TIINGO_TOKEN`.
            client: An existing `httpx.AsyncClient` to use instead of creating one lazily.
                Tests inject a client built on `httpx.MockTransport`.
            timeout: Per-request timeout in seconds.
            max_retries: Total attempts before raising `UpstreamUnavailable`.
            backoff_seconds: Base for the linear backoff between attempts.

        Raises:
            MissingCredential: no token was passed and `settings.TIINGO_TOKEN` is unset.
        """
        resolved_token = token if token is not None else settings.TIINGO_TOKEN
        if not resolved_token:
            raise MissingCredential(
                "TIINGO_TOKEN is not set. Add it to backend/.env or pass token= explicitly. "
                "Without it every request would go out unauthenticated and fail as a 401."
            )
        self._token = resolved_token
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    @property
    def name(self) -> str:
        return "tiingo-raw"

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _fetch_json(self, url: str, *, requested_symbol: str) -> Any:
        """GET `url` with retries and linear backoff, returning the parsed JSON body.

        Raises:
            SymbolNotSupported: a 404, which Tiingo's docs describe as "ticker not found."
                Unlike Yahoo's 404 (module docstring, `app.modules.gex.providers.yahoo`), this has not been
                confirmed against a live response — treated the same way on the strength of the
                published docs alone, flagged here so a future verification pass knows to check
                the body shape too.
            UpstreamUnavailable: every attempt failed, or a non-404 non-2xx status persisted.
        """
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(url)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "tiingo: attempt %d/%d transport error for %s: %s",
                    attempt,
                    self._max_retries,
                    requested_symbol,
                    exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 404:
                raise SymbolNotSupported(f"tiingo: no data for {requested_symbol!r} (404)")
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    last_exc = exc
                    if attempt < self._max_retries:
                        await asyncio.sleep(self._backoff_seconds * attempt)
                    continue

            last_exc = httpx.HTTPStatusError(
                f"unexpected status {response.status_code}",
                request=response.request,
                response=response,
            )
            logger.warning(
                "tiingo: attempt %d/%d status %d for %s",
                attempt,
                self._max_retries,
                response.status_code,
                requested_symbol,
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * attempt)

        raise UpstreamUnavailable(
            f"tiingo request failed after {self._max_retries} attempts: {requested_symbol}"
        ) from last_exc

    async def fetch_daily_bars(
        self, symbol: str, *, start: dt.date, end: dt.date | None = None
    ) -> list[DailyBar]:
        requested_symbol = symbol.strip().upper()
        params: dict[str, str] = {"startDate": start.isoformat(), "token": self._token}
        if end is not None:
            params["endDate"] = end.isoformat()
        url = httpx.URL(_BASE_URL.format(ticker=requested_symbol), params=params)
        payload = await self._fetch_json(str(url), requested_symbol=requested_symbol)
        return self._parse_payload(requested_symbol, payload)

    def _parse_payload(self, requested_symbol: str, payload: Any) -> list[DailyBar]:
        if not isinstance(payload, list):
            raise UpstreamUnavailable(
                f"tiingo: expected a JSON list for {requested_symbol!r}, got "
                f"{type(payload).__name__}"
            )
        bars: list[DailyBar] = []
        skipped = 0
        for row in payload:
            try:
                date = _parse_date(row["date"])
                open_, high, low, close = row["open"], row["high"], row["low"], row["close"]
                if open_ is None or high is None or low is None or close is None:
                    skipped += 1
                    continue
                volume = row.get("volume")
                bars.append(
                    DailyBar(
                        symbol=requested_symbol,
                        date=date,
                        open=float(open_),
                        high=float(high),
                        low=float(low),
                        close=float(close),
                        volume=None if volume is None else round(float(volume)),
                        source=self.name,
                    )
                )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                skipped += 1
                logger.warning(
                    "tiingo: skipping a %s row: %s: %s",
                    requested_symbol,
                    type(exc).__name__,
                    exc,
                )
        if skipped:
            logger.warning(
                "tiingo: skipped %d/%d row(s) for %s", skipped, len(payload), requested_symbol
            )
        bars.sort(key=lambda b: b.date)
        return bars


async def _run_cli(symbol: str, years: int) -> None:
    today = dt.datetime.now(dt.UTC).date()
    async with TiingoBarProvider() as provider:
        bars = await provider.fetch_daily_bars(
            symbol, start=today - dt.timedelta(days=365 * years)
        )
    print(f"{symbol}: {len(bars)} bars")
    if bars:
        print(f"  first={bars[0].date} last={bars[-1].date}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) not in (2, 3):
        print("usage: python -m app.modules.gex.providers.tiingo SYMBOL [years]", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_run_cli(sys.argv[1], int(sys.argv[2]) if len(sys.argv) == 3 else 5))
