"""Cboe delayed-quotes provider.

The free, unofficial Cboe endpoint (``cdn.cboe.com/api/global/delayed_quotes/options``) is the
primary data source for phases 1-4 of this project (PLAN.md §1): no key, 15-minute delay, and it
carries every field the GEX engine needs (OI, IV, Greeks). Every downstream task — storage
(T04), the scheduler (T05), the GEX engine (T08) and the dashboard — sees only what this module
returns as a :class:`~app.models.chain.ChainSnapshot`, so correctness here matters more than
speed.

Endpoint quirks handled here (verified against the live feed 2026-09-04; see ``docs/schema.md``
for the full writeup)
--------------------------------------------------------------------------------------------

* **URL symbol.** Index underlyings take an underscore prefix (``_SPX.json``); ETF underlyings
  do not (``SPY.json``, ``QQQ.json``). Neither matches the contract ``root`` — Cboe echoes the
  index symbol back as ``data.symbol == "^SPX"``, which this module ignores.
* **A browser ``User-Agent``.** The endpoint 403s some default HTTP-client user agents.
* **Two timezones in one payload.** The top-level ``timestamp`` is naive **UTC**; every
  ``last_trade_time`` inside ``data.options[]`` is naive **America/New_York**. Attaching the
  wrong zone to either would silently corrupt every trade timestamp (or the snapshot's own
  ``captured_at``) by the EDT/EST offset. See :func:`_parse_utc_timestamp` and
  :func:`_contract_from_vendor`.
* **``timestamp`` is payload-generation time, not data-effective time (T34).** Verified
  2026-09-04: at 17:55 ET, nearly two hours after the 16:00 close, ``timestamp`` read
  17:54:46 ET and kept advancing on every request, while ``data.current_price`` (and the rest
  of the chain) stayed frozen at the close. This module still parses it into ``captured_at``
  verbatim -- an earlier version of this docstring and of
  :class:`~app.models.chain.ChainSnapshot`'s called that field "effective time of the data",
  which was false, and has been corrected. Storing the raw vendor value is still correct: it
  keeps the NY calendar date right (what the duplicate-capture check and T29's per-day
  ``is_eod`` guard actually need) without this module guessing at market hours itself. A
  caller that wants an honest "as of" instant for a staleness badge should derive one via
  :func:`app.jobs.calendar.effective_data_time` rather than display ``captured_at`` directly.
* **``iv: 0.0`` is a "could not invert" sentinel, not a measurement** (roughly 3-4% of
  contracts on the live SPX chain). Mapped to ``None`` — the schema's ``iv > 0`` constraint
  exists precisely to make forgetting this loud rather than silent.
* **``gamma: 0.0`` is a genuine, vendor-rounded value** (common on far-dated, deep ITM/OTM
  contracts) and is passed through unchanged. Do not confuse it with the IV sentinel above.
* **Vendor fields not in the schema** (``rho``, ``theo``, ``tick``, ``prev_day_close``, size
  fields, OHLC, ...) are simply not read; ``OptionContract`` is ``extra="forbid"`` so this
  module selects fields explicitly rather than splatting the vendor dict.

Unknown-root policy
--------------------

``underlying_for_root`` (in ``app.models.chain``) raises ``ValueError`` for any root this
application has not mapped (a new quarterly series, say). This module's choice is
**skip-and-log the single contract, not fail the whole snapshot**: a 16:20 ET capture run pulls
tens of thousands of contracts across three symbols, and one unrecognized root should not blank
out every other correct contract for the day. The alternative (propagate and fail the snapshot)
turns a cosmetic vendor listing into a full outage of the only free data source this project has
for its first four phases. The trade-off is a silent gap in *that one root's* contracts until a
human adds it to ``_ROOT_TO_UNDERLYING`` — mitigated by logging every skip at WARNING with the
raw OCC symbol, so the gap is visible in logs even though it does not raise.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Self
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.providers.base import (
    ChainSnapshot,
    OptionChainProvider,
    OptionContract,
    SymbolNotSupported,
    Underlying,
    UpstreamUnavailable,
)

__all__ = ["CboeProvider"]

logger = logging.getLogger(__name__)

_BASE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

# Cboe rejects some default HTTP-client user agents outright; a browser-like one is required.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Index underlyings take an underscore-prefixed request symbol; ETFs use the bare ticker.
_VENDOR_SYMBOL: dict[Underlying, str] = {
    Underlying.SPX: "_SPX",
    Underlying.SPY: "SPY",
    Underlying.QQQ: "QQQ",
}

_NY = ZoneInfo("America/New_York")

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0


def _parse_utc_timestamp(raw: str) -> dt.datetime:
    """Parse Cboe's top-level ``timestamp`` (``"2026-09-04 18:18:34"``), which is naive UTC.

    This is *not* the same zone as ``last_trade_time`` inside each option (naive NY) — see the
    module docstring. Getting the two swapped puts every trade several hours in the wrong
    direction without raising, since both are merely naive strings on the wire.
    """
    return dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC)


def _parse_ny_datetime(raw: str) -> dt.datetime:
    """Parse a Cboe ``last_trade_time`` (``"2026-09-04T13:53:06"``), naive America/New_York."""
    return dt.datetime.fromisoformat(raw).replace(tzinfo=_NY)


def _to_int(value: Any) -> int | None:
    """Cboe reports integer-valued fields (``open_interest``, ``volume``) as JSON floats.

    Rounds rather than truncates. Every value observed on the live feed is integral
    (``43793.0``), so the two agree today; if a fractional value ever did arrive, ``int()``
    would silently floor it — turning an open interest of ``0.9`` into ``0`` and erasing that
    contract from the GEX total, which is precisely the ``None``-vs-``0`` corruption
    ``app/models/chain.py`` warns about. Rounding is the smaller lie of the two.
    """
    return None if value is None else round(float(value))


def _contract_from_vendor(raw: dict[str, Any]) -> OptionContract:
    """Map one entry of ``data.options[]`` to an :class:`OptionContract`.

    Raises:
        ValueError: the OCC symbol does not parse, or its root is not one this application
            maps to an :class:`Underlying` (see :func:`app.models.chain.underlying_for_root`).
            The caller decides what to do with this — see the module docstring's
            unknown-root policy.
    """
    iv = raw.get("iv")
    if iv == 0.0:
        iv = None  # sentinel for "vendor could not invert IV", not a zero measurement

    last_trade_time_raw = raw.get("last_trade_time")
    last_trade_time = _parse_ny_datetime(last_trade_time_raw) if last_trade_time_raw else None

    return OptionContract.from_occ(
        raw["option"],
        bid=raw.get("bid"),
        ask=raw.get("ask"),
        last=raw.get("last_trade_price"),
        volume=_to_int(raw.get("volume")),
        open_interest=_to_int(raw.get("open_interest")),
        iv=iv,
        delta=raw.get("delta"),
        gamma=raw.get("gamma"),  # 0.0 is a genuine rounded value here, not a sentinel
        vega=raw.get("vega"),
        theta=raw.get("theta"),
        last_trade_time=last_trade_time,
    )


class CboeProvider(OptionChainProvider):
    """Fetches SPX, SPY and QQQ option chains from Cboe's free delayed-quotes JSON endpoint."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        """
        Args:
            client: An existing ``httpx.AsyncClient`` to use instead of creating one lazily.
                Tests inject a client built on ``httpx.MockTransport`` so the suite never
                touches the network; the caller who injects a client also owns closing it —
                :meth:`close` only closes a client this provider created itself.
            timeout: Per-request timeout in seconds.
            max_retries: Total attempts (not additional retries) before raising
                :class:`UpstreamUnavailable`.
            backoff_seconds: Base for the linear backoff between attempts (``n *
                backoff_seconds`` before attempt ``n + 1``). ``0`` for instant retries in tests.
        """
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    @property
    def name(self) -> str:
        return "cboe"

    @property
    def delayed_minutes(self) -> int:
        return 15

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

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        """GET ``url`` with retries and linear backoff, returning the parsed JSON body.

        Raises:
            UpstreamUnavailable: every attempt failed (timeout, transport error, a non-2xx
                status, or a 200 whose body is not JSON — a CDN error page or bot-challenge
                HTML served with status 200 is a realistic Cboe failure mode, and letting the
                resulting ``json.JSONDecodeError`` escape would break the one guarantee this
                provider owes the scheduler: every failure is a ``ProviderError``).
        """
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.TransportError, ValueError) as exc:
                last_exc = exc
                logger.warning(
                    "cboe: attempt %d/%d failed for %s: %s", attempt, self._max_retries, url, exc
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
        raise UpstreamUnavailable(
            f"Cboe request failed after {self._max_retries} attempts: {url}"
        ) from last_exc

    async def fetch_chain(self, underlying: str) -> ChainSnapshot:
        try:
            canonical = Underlying(underlying.strip().upper())
        except (AttributeError, ValueError):
            raise SymbolNotSupported(f"cboe provider does not support {underlying!r}") from None

        url = _BASE_URL.format(symbol=_VENDOR_SYMBOL[canonical])
        payload = await self._fetch_json(url)
        return self._parse_payload(canonical, payload)

    def _parse_payload(self, underlying: Underlying, payload: dict[str, Any]) -> ChainSnapshot:
        try:
            data = payload["data"]
            captured_at = _parse_utc_timestamp(payload["timestamp"])
            spot = float(data["current_price"])
            raw_options = data["options"]
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamUnavailable(
                f"Cboe response for {underlying.value} is missing an expected field"
            ) from exc

        contracts: list[OptionContract] = []
        skipped = 0
        for raw in raw_options:
            try:
                contracts.append(_contract_from_vendor(raw))
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                # Unknown root, an OCC symbol that does not parse, a field the schema rejects,
                # or an entry that is not even shaped like a contract (missing "option", a
                # bare string instead of an object). See the module docstring's
                # "unknown-root policy": one bad contract must not blank out every other
                # correct contract in the snapshot. The catch is deliberately wider than
                # ValueError -- a KeyError escaping here would propagate out of `fetch_chain`
                # as a non-ProviderError and abort the whole multi-symbol capture run.
                skipped += 1
                logger.warning(
                    "cboe: skipping contract %r: %s: %s",
                    raw.get("option") if isinstance(raw, dict) else raw,
                    type(exc).__name__,
                    exc,
                )
        if skipped:
            logger.warning(
                "cboe: skipped %d/%d contracts for %s (unparseable symbol, unmapped root, or "
                "a field the schema rejected)",
                skipped,
                len(raw_options),
                underlying.value,
            )

        if not contracts:
            # An empty chain is never a real answer for SPX/SPY/QQQ. Returning one would
            # store a "successful" snapshot with zero contracts, whose GEX is silently 0 --
            # far worse than a logged failure, because the capture looks fine in the logs and
            # the hole is only visible months later on a chart.
            raise UpstreamUnavailable(
                f"Cboe returned no usable contracts for {underlying.value} "
                f"({len(raw_options)} listed, {skipped} skipped)"
            )

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
                f"Cboe response for {underlying.value} failed schema validation"
            ) from exc


async def _run_cli(symbol: str) -> None:
    async with CboeProvider() as provider:
        snapshot = await provider.fetch_chain(symbol)
    print(
        f"{snapshot.underlying.value}: spot={snapshot.spot:.2f} "
        f"contracts={len(snapshot)} expiries={len(snapshot.expiries)} "
        f"captured_at={snapshot.captured_at.isoformat()}"
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m app.providers.cboe SPX|SPY|QQQ", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_run_cli(sys.argv[1]))
