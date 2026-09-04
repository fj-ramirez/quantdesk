"""MarketData.app fallback provider.

PLAN.md §1 names this the fallback for the Cboe delayed-quotes feed (``app.providers.cboe``):
Cboe's endpoint is free but undocumented and unofficial, so if it ever changes shape or starts
blocking requests, this module is what keeps EOD capture running while a fix goes in. It is
exercised rarely by design — which is exactly why it needs to be right the first time it is
actually needed, not merely plausible.

Endpoint: ``GET https://api.marketdata.app/v1/options/chain/{underlyingSymbol}/``.

IMPORTANT — provenance of everything below
--------------------------------------------
**There is no MarketData.app account or token available in this environment.** Nothing here
has been exercised against the live API. Every claim about response shape, units and
authentication below was pulled from MarketData.app's own published docs on 2026-09-04 (URLs
in each section) and the fixtures in ``tests/fixtures/marketdata/`` are **hand-built from that
documentation**, not recorded live traffic — contrast with ``tests/fixtures/cboe/``, which are
trimmed real responses. Treat this module as reviewed-against-docs, not integration-tested.
Run ``uv run python -m app.providers.marketdata SPX`` (with ``MARKETDATA_TOKEN`` set) to get
the first real signal.

Response shape (https://www.marketdata.app/docs/api/options/chain/, fetched 2026-09-04)
-----------------------------------------------------------------------------------------
The response is **columnar** (confirms the brief): one JSON object whose relevant keys —
``optionSymbol``, ``strike``, ``bid``, ``ask``, ``last``, ``openInterest``, ``volume``, ``iv``,
``delta``, ``gamma``, ``theta``, ``vega``, ``underlyingPrice``, ``updated`` — are each a JSON
array, all the same length, with index ``i`` across every array describing one contract. A
top-level ``"s"`` field is ``"ok"`` on success, ``"no_data"`` when nothing matched the filters,
or ``"error"`` (with an ``"errmsg"`` string) on a request-level failure. ``optionSymbol`` is a
standard OCC symbol (e.g. ``"AAPL271217C00100000"``), so :func:`app.models.chain.parse_occ_symbol`
applies unchanged from the Cboe adapter.

There is **no per-contract "last trade time" field** — only ``updated`` (an epoch-seconds
quote-refresh timestamp) and no snapshot-level timestamp at all. This module therefore:

* leaves :attr:`OptionContract.last_trade_time` ``None`` for every contract (mapping
  "quote last updated" to "trade last happened" would fabricate a field the vendor doesn't
  report), and
* derives :attr:`ChainSnapshot.captured_at` as the **max** of the per-contract ``updated``
  values, since there is nothing else to derive it from.

**Expiration defaults to one month unless overridden.** The docs are explicit: "If omitted the
next monthly expiration ... will be returned. Use the keyword ``all`` to return the complete
option chain." ``base.py`` forbids a provider from filtering the chain, so this module always
sends ``expiration=all``.

IV units (https://www.marketdata.app/docs/api/options/chain/, fetched 2026-09-04)
------------------------------------------------------------------------------------
**Corrects the task brief, which expected the opposite of Cboe (percent, needing /100).** The
documented example response has ``"iv": [0.6071]`` and MarketData's own prose reads "Decimal
format (e.g., 0.2106 = 21.06%)". That is already the schema's convention — **do not** divide by
100 here either. A vendor IV of exactly ``0`` is treated as the same "could not invert" sentinel
Cboe uses and mapped to ``None`` per this project's schema rule; this is not separately
confirmed for MarketData (no free-tier chain has been pulled to check), so treat it as a
defensive default rather than a verified vendor behavior.

Index symbology (https://www.marketdata.app/education/options/spx-vs-spxw-options/ and
https://www.marketdata.app/docs/api/options/chain/, fetched 2026-09-04)
------------------------------------------------------------------------------------
**Corrects the task brief's suggestion that SPX might need special handling.** Unlike Cboe
(which needs an underscore-prefixed ``_SPX`` in the URL), MarketData.app takes the underlying
plain — ``.../chain/SPX/`` — for both the AM-settled ``SPX`` root and the PM-settled ``SPXW``
root; both come back in the same call, distinguished only by the root embedded in each
``optionSymbol``, exactly as Cboe already returns them. So no per-symbol URL mangling table is
needed here (contrast :data:`app.providers.cboe._VENDOR_SYMBOL`); the canonical
:class:`Underlying` value is used verbatim as the URL path segment.

Cached mode, credits and the free-tier discrepancy the task brief did not anticipate
------------------------------------------------------------------------------------
The task instructs "use cached mode (1 credit per call)". The chain endpoint's own pricing
table (same URL as above) does say a cached call costs 1 credit "regardless of the number of
symbols queried" — matching the brief. **What the brief does not mention, and what live
doc pages independently surfaced while researching this** (``.../docs/api/universal-parameters/
mode/`` and ``.../docs/account/plan-limits/``, fetched 2026-09-04): cached mode is gated to
paid plans — "Free and trial plans cannot change the data mode... `mode=cached` requests
return `402 Payment Required`" on Free Forever, whose default is `mode=historical` (the last
fully-closed session) rather than a 15-minute delayed quote. This is consistent with — and
probably why — PLAN.md's own source table already lists this provider's latency as **"24 h"**,
not Cboe's 15 minutes; :attr:`MarketDataProvider.delayed_minutes` follows PLAN.md's number
(1440) rather than Cboe's, for that reason.

**T06 review change:** this module used to hardcode ``mode=cached`` on every request. On a
Free Forever token that is a documented, deterministic ``402`` — so the break-glass fallback
would have failed on the only plan it is configured for, and it would have failed at the worst
possible moment (Cboe already broken). ``mode`` is now an optional constructor argument,
omitted from the query string by default, so each plan's own default applies: ``historical``
on free (matching ``delayed_minutes=1440``), ``live`` on paid. A paid user who wants the
1-credit cached chain constructs ``MarketDataProvider(mode="cached")``.

Authentication (https://www.marketdata.app/docs/api/authentication/, fetched 2026-09-04)
------------------------------------------------------------------------------------------
Header form is documented as preferred ("recommended to ensure your token is not stored or
cached"): ``Authorization: Bearer {token}``. This module uses that form exclusively. The token
comes from ``MARKETDATA_TOKEN`` in ``app.config.settings``; a missing token raises
:class:`MissingCredential` — naming the setting — before any HTTP request is built, rather than
leaving a blank ``Bearer `` header for the vendor to reject with an opaque 401.

Unknown-root policy
--------------------
Same as ``app.providers.cboe``: skip and log the single contract rather than failing the whole
snapshot. See that module's docstring for the reasoning; T06 reviews both providers for this
exact consistency.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Self

import httpx
from pydantic import ValidationError

from app.config import settings
from app.providers.base import (
    ChainSnapshot,
    OptionChainProvider,
    OptionContract,
    ProviderError,
    SymbolNotSupported,
    Underlying,
    UpstreamUnavailable,
)

__all__ = ["MarketDataProvider", "MissingCredential"]

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.marketdata.app/v1/options/chain/{symbol}/"

# PLAN.md's source table lists this provider's latency as "24 h", not Cboe's 15 minutes — see
# the "Cached mode, credits and the free-tier discrepancy" section of the module docstring for
# why a Free Forever token is likely to see day-old (mode=historical) data rather than a 15-min
# delayed quote even though this module requests mode=cached.
_DELAYED_MINUTES = 24 * 60

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0


class MissingCredential(ProviderError):
    """Raised when no MarketData.app token is configured.

    Deliberately raised eagerly, in :meth:`MarketDataProvider.__init__`, rather than left to
    surface as a vendor 401 on the first request — a blank ``Authorization: Bearer`` header is
    a confusing failure to debug, while this names the exact setting to fix.
    """


def _epoch_to_utc(seconds: float) -> dt.datetime:
    """Convert a Unix epoch (seconds) to a tz-aware UTC datetime.

    Unix epochs are an unambiguous instant by construction — unlike Cboe's naive local-time
    strings, there is no zone-attachment judgment call to make here, just the conversion.
    """
    return dt.datetime.fromtimestamp(seconds, tz=dt.UTC)


def _at(arr: list[Any] | None, i: int) -> Any:
    """Index into one column of the columnar response, tolerating a short or absent array."""
    if arr is None or i >= len(arr):
        return None
    return arr[i]


def _at_int(arr: list[Any] | None, i: int) -> int | None:
    value = _at(arr, i)
    return None if value is None else int(value)


def _at_float(arr: list[Any] | None, i: int) -> float | None:
    value = _at(arr, i)
    return None if value is None else float(value)


def _contract_from_row(payload: dict[str, Any], i: int) -> OptionContract:
    """Map row ``i`` of the columnar response to an :class:`OptionContract`.

    Raises:
        ValueError: the OCC symbol does not parse, or its root is unmapped. Callers apply the
            skip-and-log unknown-root policy documented at module level, same as Cboe.
    """
    iv = _at_float(payload.get("iv"), i)
    if iv == 0.0:
        iv = None  # treated as the same "could not invert" sentinel Cboe uses; see docstring

    return OptionContract.from_occ(
        payload["optionSymbol"][i],
        bid=_at_float(payload.get("bid"), i),
        ask=_at_float(payload.get("ask"), i),
        last=_at_float(payload.get("last"), i),
        volume=_at_int(payload.get("volume"), i),
        open_interest=_at_int(payload.get("openInterest"), i),
        iv=iv,
        delta=_at_float(payload.get("delta"), i),
        gamma=_at_float(payload.get("gamma"), i),
        vega=_at_float(payload.get("vega"), i),
        theta=_at_float(payload.get("theta"), i),
        # No per-contract "last trade time" field exists in this response — see module
        # docstring. `updated` is a quote-refresh timestamp, not a trade timestamp, and
        # conflating the two would fabricate data the vendor never reported.
        last_trade_time=None,
    )


class MarketDataProvider(OptionChainProvider):
    """Fetches SPX, SPY and QQQ option chains from the MarketData.app cached chain endpoint.

    Unverified against the live API — see the module docstring's provenance note.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
        mode: str | None = None,
    ) -> None:
        """
        Args:
            token: MarketData.app API token. Defaults to ``settings.MARKETDATA_TOKEN``.
            mode: Optional ``mode`` query parameter. Left unset by default so each plan's own
                default applies — ``historical`` on Free Forever (which is what
                :attr:`delayed_minutes` = 1440 already describes), ``live`` on a paid plan.
                A paid user who wants the 1-credit cached chain passes ``mode="cached"``;
                sending it on a free token is a guaranteed 402, which is why it is no longer
                hardcoded.
            client: An existing ``httpx.AsyncClient`` to use instead of creating one lazily.
                Tests inject a client built on ``httpx.MockTransport``, so the suite never
                touches the network; the caller who injects a client also owns closing it.
            timeout: Per-request timeout in seconds.
            max_retries: Total attempts (not additional retries) before raising
                :class:`UpstreamUnavailable`.
            backoff_seconds: Base for the linear backoff between attempts.

        Raises:
            MissingCredential: no token was passed and ``settings.MARKETDATA_TOKEN`` is unset.
        """
        resolved_token = token if token is not None else settings.MARKETDATA_TOKEN
        if not resolved_token:
            raise MissingCredential(
                "MARKETDATA_TOKEN is not set. Add it to backend/.env (see .env.example) or "
                "pass token= explicitly. Without it every request would go out with a blank "
                "Authorization header and fail as a confusing 401."
            )
        self._token = resolved_token
        self._mode = mode
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    @property
    def name(self) -> str:
        return "marketdata"

    @property
    def delayed_minutes(self) -> int:
        return _DELAYED_MINUTES

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
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        """GET ``url`` with retries and linear backoff, returning the parsed JSON body.

        The ``Authorization`` header is attached per-request rather than baked into the
        client at construction time, so that a caller-injected ``httpx.AsyncClient`` (as every
        test here does) still gets an authenticated request — the provider owns the token, not
        necessarily the client.

        Raises:
            UpstreamUnavailable: every attempt failed (timeout, transport error, a non-2xx
                status, or a 200 whose body is not JSON). A 401/402 (bad or plan-restricted
                token) is not specially distinguished here — it still exhausts retries and
                surfaces as UpstreamUnavailable, since retrying will not help but the caller
                (the scheduler) is only required to survive ProviderError, not diagnose it.
        """
        client = await self._get_client()
        headers = {"Authorization": f"Bearer {self._token}"}
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.TransportError, ValueError) as exc:
                last_exc = exc
                logger.warning(
                    "marketdata: attempt %d/%d failed for %s: %s",
                    attempt,
                    self._max_retries,
                    url,
                    exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
        raise UpstreamUnavailable(
            f"MarketData.app request failed after {self._max_retries} attempts: {url}"
        ) from last_exc

    async def fetch_chain(self, underlying: str) -> ChainSnapshot:
        try:
            canonical = Underlying(underlying.strip().upper())
        except (AttributeError, ValueError):
            raise SymbolNotSupported(
                f"marketdata provider does not support {underlying!r}"
            ) from None

        # expiration=all: without it the vendor defaults to a single expiration (the next
        # monthly), which would silently filter the chain — forbidden by base.py.
        # `mode` is omitted by default (see __init__): a Free Forever token cannot set it and
        # answers `mode=cached` with 402, so hardcoding it made this fallback provider fail on
        # the only plan it is actually configured for.
        params: dict[str, str] = {"expiration": "all"}
        if self._mode is not None:
            params["mode"] = self._mode
        url = httpx.URL(_BASE_URL.format(symbol=canonical.value), params=params)
        payload = await self._fetch_json(str(url))
        return self._parse_payload(canonical, payload)

    def _parse_payload(self, underlying: Underlying, payload: dict[str, Any]) -> ChainSnapshot:
        status = payload.get("s")
        if status == "error":
            raise UpstreamUnavailable(
                f"MarketData.app returned an error for {underlying.value}: "
                f"{payload.get('errmsg', '(no errmsg)')}"
            )
        if status != "ok":
            # "no_data" or any other unrecognized status: nothing usable came back. Treated as
            # retryable rather than SymbolNotSupported — SPX/SPY/QQQ are always valid
            # underlyings for this vendor, so an empty result here looks like a transient or
            # plan-related hiccup, not a permanently unsupported symbol.
            raise UpstreamUnavailable(
                f"MarketData.app returned status {status!r} for {underlying.value} "
                f"(expected 'ok')"
            )

        try:
            symbols = payload["optionSymbol"]
            updated = payload["updated"]
            spot = float(payload["underlyingPrice"][0])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise UpstreamUnavailable(
                f"MarketData.app response for {underlying.value} is missing an expected field"
            ) from exc

        if not symbols:
            raise UpstreamUnavailable(
                f"MarketData.app returned zero contracts for {underlying.value}"
            )

        contracts: list[OptionContract] = []
        skipped = 0
        for i in range(len(symbols)):
            try:
                contracts.append(_contract_from_row(payload, i))
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                # Unknown root, an OCC symbol that does not parse, or a field the schema
                # rejects. Same skip-and-log policy as app.providers.cboe, and deliberately
                # the same (wide) exception set: anything narrower escapes `fetch_chain` as a
                # non-ProviderError and aborts the whole multi-symbol capture run.
                skipped += 1
                logger.warning(
                    "marketdata: skipping contract %r: %s: %s",
                    symbols[i],
                    type(exc).__name__,
                    exc,
                )
        if skipped:
            logger.warning(
                "marketdata: skipped %d/%d contracts for %s (unparseable symbol, unmapped "
                "root, or a field the schema rejected)",
                skipped,
                len(symbols),
                underlying.value,
            )

        if not contracts:
            # Same reasoning as app.providers.cboe: a zero-contract snapshot stored as a
            # success is a silent, permanent hole, so fail loudly instead.
            raise UpstreamUnavailable(
                f"MarketData.app returned no usable contracts for {underlying.value} "
                f"({len(symbols)} listed, {skipped} skipped)"
            )

        try:
            captured_at = _epoch_to_utc(max(updated))
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            # `updated` is the only source for `captured_at`; an empty, non-numeric or
            # out-of-range column has to be a ProviderError, not a bare ValueError escaping
            # into the scheduler.
            raise UpstreamUnavailable(
                f"MarketData.app 'updated' column for {underlying.value} is unusable "
                f"({type(exc).__name__}: {exc})"
            ) from exc

        try:
            return ChainSnapshot(
                underlying=underlying,
                spot=spot,
                captured_at=captured_at,
                source=self.name,
                delayed_minutes=self.delayed_minutes,
                contracts=tuple(contracts),
            )
        except ValidationError as exc:
            raise UpstreamUnavailable(
                f"MarketData.app response for {underlying.value} failed schema validation"
            ) from exc


async def _run_cli(symbol: str) -> None:
    async with MarketDataProvider() as provider:
        snapshot = await provider.fetch_chain(symbol)
    print(
        f"{snapshot.underlying.value}: spot={snapshot.spot:.2f} "
        f"contracts={len(snapshot)} expiries={len(snapshot.expiries)} "
        f"captured_at={snapshot.captured_at.isoformat()}"
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m app.providers.marketdata SPX|SPY|QQQ", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_run_cli(sys.argv[1]))
