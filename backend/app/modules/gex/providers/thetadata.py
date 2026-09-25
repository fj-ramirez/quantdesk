"""ThetaData providers -- historical EOD chains (T26) and the live chain (T126) from a Theta
Terminal v3.

The subscription is **Options Standard, options only** (plans/thetadata/README.md): no Stocks,
no Indices. Everything goes through a locally running Theta Terminal, never ThetaData's cloud
directly. On the homeserver that is the ``theta-terminal`` service (``THETADATA_URL`` =
``http://theta-terminal:25503``), which owns the account's single live session.

IMPORTANT -- provenance of everything below
--------------------------------------------
Written on 2026-09-25 from ThetaData's published v3 docs, **before any live response was seen**
(the terminal had not been started yet). Endpoints, parameter names and field names are from:

* ``/v3/option/history/greeks/eod`` -- https://thetadata.net/docs/operations/option_history_greeks_eod.html
* ``/v3/option/history/open_interest`` -- https://thetadata.net/docs/operations/option_history_open_interest.html
* ``/v3/option/snapshot/greeks/first_order`` and ``/v3/option/snapshot/open_interest`` (T126) --
  https://thetadata.net/docs/operations/option_snapshot_greeks_first_order.html (Standard tier;
  ``greeks/all`` is Pro-only). Its columns are the EOD ones minus ``close``/``volume``/``gamma``.
* v2 -> v3 changes (strike in dollars, ``right`` = ``call``/``put``, ``format``) --
  https://thetadata.net/docs/Articles/Getting-Started/v2-migration-guide.html

The fixtures in ``tests/fixtures/thetadata/`` are hand-built from those pages, not recorded.
``python -m app.modules.gex.providers.thetadata SPX 2026-09-24`` pulls a real session and prints the headers; whatever it shows
overrules this docstring. The CSV parsing below is by header name and tolerant of extra
columns for exactly that reason.

The history snapshot for session D
----------------------------------
One :class:`ChainSnapshot` per (underlying, D), assembled from two requests per root:

* **greeks/EOD for D** -- every contract's close/bid/ask/volume, ``implied_vol``, the Greeks and
  ``underlying_price``, from ThetaData's EOD report (generated 17:15 ET).
* **open interest reported on D**, which ThetaData documents as "the open interest at the end of
  the previous trading day". That is exactly the OI a live 16:20 Cboe capture on D carries,
  which is why D's greeks are paired with D's OI report and **not** D+1's: the latter is D's
  closing OI, which nobody watching the close on D could have had, and a backtest fed it would
  be quietly clairvoyant.

``captured_at`` is D 16:00 America/New_York -- the close the EOD values describe -- so
``calendar.session_date`` maps it to D. A contract with no OI row gets ``open_interest=None``
(unknown, excluded by the engine), never ``0`` (invariant 3).

Units and conventions
---------------------
* ``implied_vol`` is taken as a decimal fraction, the schema's unit. ThetaData inverts it with
  Black-Scholes and **no dividend** by default, unlike Cboe; the engine recomputes gamma from
  this IV, so that difference flows through (plan rule 4). ``<= 0`` means "no IV" -> ``None``.
* ``gamma`` is stored as the vendor cross-check only, as Cboe's is.
* ``vega`` is divided by 100: ThetaData documents that "Rho and Vega must be divided by 100 for
  actual values", giving per-vol-point vega like Cboe's.
* Spot is the median positive ``underlying_price`` across the session's rows. If there is none
  -- plausible for SPX without an Indices subscription -- this raises rather than inventing
  one; the parity-implied forward (T33) is the planned fallback, not a silent default.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import logging
import statistics
from collections.abc import Iterable, Mapping
from typing import Self
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.modules.gex.providers.base import (
    ChainSnapshot,
    OptionChainProvider,
    OptionContract,
    ProviderError,
    SymbolNotSupported,
    Underlying,
    UpstreamUnavailable,
)

__all__ = [
    "ThetaDataClient",
    "ThetaDataEodProvider",
    "ThetaDataProvider",
    "build_eod_snapshot",
    "build_live_snapshot",
    "vendor_roots",
]

logger = logging.getLogger(__name__)

_NY = ZoneInfo("America/New_York")
_CLOSE = dt.time(16, 0)
_DEFAULT_TIMEOUT_SECONDS = 120.0
_LIVE_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 5.0
#: ThetaData's "no data for this request" status (v2 documented it as 472 NO_DATA). An empty
#: answer, not a failure: a holiday or an expiry-less day legitimately has none.
_NO_DATA_STATUS = 472

#: SPX lists under two roots (AM-settled monthly SPX, PM-settled SPXW); both are needed for
#: the chain Cboe serves as one. Everything else is root == ticker.
_ROOTS: dict[Underlying, tuple[str, ...]] = {Underlying.SPX: ("SPX", "SPXW")}


def vendor_roots(underlying: Underlying) -> tuple[str, ...]:
    """The ThetaData ``symbol`` values that together make up ``underlying``'s chain."""
    return _ROOTS.get(underlying, (underlying.value,))


def _float(row: Mapping[str, str], key: str) -> float | None:
    raw = row.get(key)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _positive(value: float | None) -> float | None:
    return value if value is not None and value > 0 else None


def _right_letter(raw: str) -> str:
    value = raw.strip().upper()
    if value in {"C", "CALL"}:
        return "C"
    if value in {"P", "PUT"}:
        return "P"
    raise ValueError(f"unknown right {raw!r}")


def _expiry(raw: str) -> dt.date:
    value = raw.strip()
    return dt.date.fromisoformat(value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:8]}")


def _key(row: Mapping[str, str], default_root: str) -> tuple[str, dt.date, float, str]:
    root = (row.get("symbol") or default_root).strip().upper()
    return root, _expiry(row["expiration"]), round(float(row["strike"]), 3), _right_letter(row["right"])


def _occ(root: str, expiry: dt.date, strike: float, right: str) -> str:
    return f"{root}{expiry:%y%m%d}{right}{round(strike * 1000):08d}"


def _assemble(
    underlying: Underlying,
    label: str,
    greeks_rows: Iterable[tuple[str, Mapping[str, str]]],
    oi_rows: Iterable[tuple[str, Mapping[str, str]]],
) -> tuple[tuple[OptionContract, ...], float]:
    """Join greeks rows with OI rows into contracts plus the median spot. Pure.

    Shared by the EOD and the live snapshot: both endpoints carry the same identifying and
    ``bid``/``ask``/``implied_vol``/``underlying_price`` columns. A column one of them lacks
    (the live ``first_order`` rows have no ``close``, ``volume`` or ``gamma``) reads as
    ``None``, which is what an absent value is.

    Raises:
        UpstreamUnavailable: no usable contracts, or no ``underlying_price`` to take spot from.
    """
    open_interest: dict[tuple[str, dt.date, float, str], int] = {}
    for root, row in oi_rows:
        try:
            oi = _float(row, "open_interest")
            if oi is not None and oi >= 0:
                open_interest[_key(row, root)] = int(oi)
        except (KeyError, ValueError) as exc:
            logger.warning("thetadata: skipping OI row %r: %s", row, exc)

    contracts: dict[tuple[str, dt.date, float, str], OptionContract] = {}
    spots: list[float] = []
    listed = skipped = 0
    for root, row in greeks_rows:
        listed += 1
        try:
            key = _key(row, root)
            if key in contracts:
                continue  # SPX returned under both requested roots
            vega = _float(row, "vega")
            volume = _float(row, "volume")
            contracts[key] = OptionContract.from_occ(
                _occ(*key),
                bid=_float(row, "bid"),
                ask=_float(row, "ask"),
                last=_positive(_float(row, "close")),
                volume=int(volume) if volume is not None and volume >= 0 else None,
                open_interest=open_interest.get(key),
                iv=_positive(_float(row, "implied_vol")),
                delta=_float(row, "delta"),
                gamma=_positive(_float(row, "gamma")),
                vega=vega / 100 if vega is not None and vega >= 0 else None,
                theta=_float(row, "theta"),
            )
            spot = _positive(_float(row, "underlying_price"))
            if spot is not None:
                spots.append(spot)
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            skipped += 1
            logger.warning("thetadata: skipping contract %r: %s: %s", row, type(exc).__name__, exc)

    if skipped:
        logger.warning("thetadata: skipped %d/%d contracts for %s %s", skipped, listed, underlying.value, label)
    if not contracts:
        raise UpstreamUnavailable(
            f"ThetaData returned no usable contracts for {underlying.value} {label} "
            f"({listed} listed, {skipped} skipped)"
        )
    if not spots:
        raise UpstreamUnavailable(
            f"ThetaData rows for {underlying.value} {label} carry no underlying_price; "
            "spot cannot be taken from the chain (see plans/thetadata/README.md: T33 fallback)"
        )
    missing_oi = sum(1 for c in contracts.values() if c.open_interest is None)
    if missing_oi:
        logger.info(
            "thetadata: %d/%d %s contracts %s have no OI row (kept as unknown, not zero)",
            missing_oi, len(contracts), underlying.value, label,
        )
    return tuple(contracts.values()), statistics.median(spots)


def _snapshot(
    underlying: Underlying, label: str, contracts: tuple[OptionContract, ...], spot: float,
    captured_at: dt.datetime, source: str,
) -> ChainSnapshot:
    try:
        return ChainSnapshot(
            underlying=underlying,
            spot=spot,
            captured_at=captured_at,
            source=source,
            delayed_minutes=0,
            contracts=contracts,
        )
    except ValidationError as exc:
        raise UpstreamUnavailable(
            f"ThetaData snapshot for {underlying.value} {label} failed schema validation"
        ) from exc


def build_eod_snapshot(
    underlying: Underlying,
    session: dt.date,
    greeks_rows: Iterable[tuple[str, Mapping[str, str]]],
    oi_rows: Iterable[tuple[str, Mapping[str, str]]],
    *,
    source: str = "thetadata",
) -> ChainSnapshot:
    """Join one session's greeks/EOD rows with the OI reported on that session. Pure.

    Args:
        greeks_rows / oi_rows: ``(requested_root, csv_row)`` pairs; the requested root is the
            fallback when a row carries no ``symbol`` column.

    Raises:
        UpstreamUnavailable: no usable contracts, or no ``underlying_price`` to take spot from.
    """
    label = f"on {session}"
    contracts, spot = _assemble(underlying, label, greeks_rows, oi_rows)
    captured_at = dt.datetime.combine(session, _CLOSE, tzinfo=_NY).astimezone(dt.UTC)
    return _snapshot(underlying, label, contracts, spot, captured_at, source)


def _quote_time(raw: str | None) -> dt.datetime | None:
    """A v3 ``timestamp`` (``YYYY-MM-DDTHH:mm:ss.SSS``, naive America/New_York) as UTC."""
    if not raw or not raw.strip():
        return None
    try:
        return dt.datetime.fromisoformat(raw.strip()).replace(tzinfo=_NY).astimezone(dt.UTC)
    except ValueError:
        return None


def build_live_snapshot(
    underlying: Underlying,
    greeks_rows: Iterable[tuple[str, Mapping[str, str]]],
    oi_rows: Iterable[tuple[str, Mapping[str, str]]],
    *,
    now: dt.datetime,
    source: str = "thetadata",
) -> ChainSnapshot:
    """Join the real-time ``greeks/first_order`` snapshot with the morning's OI report. Pure.

    ``captured_at`` is the newest quote timestamp in the rows -- the vendor's own "as of",
    which is what the engine measures time to expiry from and what the Cboe provider records
    too. After the close it stops at the last quote rather than advancing with the wall
    clock. It is capped at ``now`` (a clock-skewed terminal must not stamp the future) and
    falls back to ``now`` only when no row carries a parseable timestamp.

    OI is the snapshot OI report, published ~06:30 ET for the previous close -- the same OI a
    Cboe capture carries all day (plan rule 3), so intraday GEX moves with IV and spot, never
    with positioning.
    """
    greeks_rows = list(greeks_rows)
    contracts, spot = _assemble(underlying, "live", greeks_rows, oi_rows)
    stamps = [t for t in (_quote_time(row.get("timestamp")) for _, row in greeks_rows) if t is not None]
    captured_at = min(max(stamps), now) if stamps else now
    return _snapshot(underlying, "live", contracts, spot, captured_at, source)


class ThetaDataClient:
    """Thin async client for the Theta Terminal v3 REST API. CSV in, dict rows out."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        url = (base_url if base_url is not None else settings.THETADATA_URL).strip().rstrip("/")
        if not url:
            raise ProviderError(
                "THETADATA_URL is not set: no Theta Terminal is configured here. The homeserver "
                "runs one (compose.prod.yaml); dev needs compose.theta-dev.yaml -- see "
                "plans/thetadata/README.md before starting it."
            )
        self._base_url = url
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def get_rows(self, path: str, params: Mapping[str, str]) -> list[dict[str, str]]:
        """GET ``path`` as CSV with retries. A no-data answer is ``[]``, not an error."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        url = f"{self._base_url}{path}"
        query = {**params, "format": "csv"}
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._client.get(url, params=query)
                if response.status_code == _NO_DATA_STATUS:
                    return []
                response.raise_for_status()
                return list(csv.DictReader(io.StringIO(response.text)))
            except (httpx.HTTPStatusError, httpx.TransportError, csv.Error) as exc:
                last_exc = exc
                logger.warning(
                    "thetadata: attempt %d/%d failed for %s %s: %s",
                    attempt, self._max_retries, path, dict(params), exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
        raise UpstreamUnavailable(
            f"Theta Terminal request failed after {self._max_retries} attempts: {path} {dict(params)}"
        ) from last_exc

    async def eod_rows(
        self, underlying: Underlying, session: dt.date
    ) -> tuple[list[tuple[str, dict[str, str]]], list[tuple[str, dict[str, str]]]]:
        """Every root's greeks/EOD rows for ``session`` and the OI reported on ``session``."""
        day = f"{session:%Y%m%d}"
        greeks: list[tuple[str, dict[str, str]]] = []
        oi: list[tuple[str, dict[str, str]]] = []
        for root in vendor_roots(underlying):
            common = {"symbol": root, "expiration": "*"}
            for row in await self.get_rows(
                "/v3/option/history/greeks/eod", {**common, "start_date": day, "end_date": day}
            ):
                greeks.append((root, row))
            for row in await self.get_rows("/v3/option/history/open_interest", {**common, "date": day}):
                oi.append((root, row))
        return greeks, oi

    async def snapshot_rows(
        self, underlying: Underlying, expiration: dt.date | None = None
    ) -> tuple[list[tuple[str, dict[str, str]]], list[tuple[str, dict[str, str]]]]:
        """Every root's real-time ``greeks/first_order`` rows and the current OI report.

        ``expiration`` narrows both requests to one expiry (a 0DTE pull is a few hundred
        contracts instead of the whole chain); ``None`` asks for every expiry.
        ``first_order`` rather than ``greeks/all``: the latter needs the Pro tier, and the
        engine recomputes gamma from IV anyway (plan rule 4).
        """
        expiry = "*" if expiration is None else f"{expiration:%Y%m%d}"
        greeks: list[tuple[str, dict[str, str]]] = []
        oi: list[tuple[str, dict[str, str]]] = []
        for root in vendor_roots(underlying):
            common = {"symbol": root, "expiration": expiry}
            for row in await self.get_rows("/v3/option/snapshot/greeks/first_order", common):
                greeks.append((root, row))
            for row in await self.get_rows("/v3/option/snapshot/open_interest", common):
                oi.append((root, row))
        return greeks, oi


class ThetaDataEodProvider(OptionChainProvider):
    """A one-shot provider bound to one past session, so a history load goes through
    ``jobs.capture.capture_snapshot`` -- the same dedupe, Parquet, index and level
    computation as a live capture -- instead of a parallel write path.

    Not registered in ``get_provider``: it cannot answer "the chain now";
    :class:`ThetaDataProvider` does.
    """

    def __init__(self, client: ThetaDataClient, session: dt.date) -> None:
        self._client = client
        self._session = session

    @property
    def name(self) -> str:
        return "thetadata"

    @property
    def delayed_minutes(self) -> int:
        return 0

    async def fetch_chain(self, underlying: str) -> ChainSnapshot:
        canonical = _canonical(underlying)
        greeks, oi = await self._client.eod_rows(canonical, self._session)
        return build_eod_snapshot(canonical, self._session, greeks, oi, source=self.name)


def _canonical(underlying: str) -> Underlying:
    try:
        return Underlying(underlying.strip().upper())
    except (AttributeError, ValueError):
        raise SymbolNotSupported(f"thetadata provider does not support {underlying!r}") from None


class ThetaDataProvider(OptionChainProvider):
    """The live provider behind ``PROVIDER=thetadata`` (T126): the chain *now*, real time.

    Reads the terminal's snapshot endpoints (``greeks/first_order`` + ``open_interest``),
    which the Options Standard subscription covers. Outside a session day the terminal has
    no snapshot (its cache resets at midnight ET) and this raises ``UpstreamUnavailable``,
    which the capture job logs and moves past like any other vendor gap.

    Timeouts are shorter than the history client's: this also answers the Explorer's live
    0DTE pull, where a two-minute hang is worse than a quick, named failure.
    """

    def __init__(self, client: ThetaDataClient | None = None) -> None:
        self._client = client if client is not None else ThetaDataClient(
            timeout=_LIVE_TIMEOUT_SECONDS, max_retries=2, backoff_seconds=1.0
        )

    @property
    def name(self) -> str:
        return "thetadata"

    @property
    def delayed_minutes(self) -> int:
        return 0

    async def close(self) -> None:
        await self._client.close()

    async def fetch_chain(self, underlying: str) -> ChainSnapshot:
        canonical = _canonical(underlying)
        greeks, oi = await self._client.snapshot_rows(canonical)
        return build_live_snapshot(canonical, greeks, oi, now=dt.datetime.now(dt.UTC), source=self.name)

    async def fetch_expiry(self, underlying: str, expiry: dt.date) -> ChainSnapshot:
        """One expiry only, asked of the terminal rather than filtered out of the full chain."""
        canonical = _canonical(underlying)
        greeks, oi = await self._client.snapshot_rows(canonical, expiry)
        return build_live_snapshot(canonical, greeks, oi, now=dt.datetime.now(dt.UTC), source=self.name)


async def _probe(symbol: str, session: dt.date) -> None:
    """T125's data-level check: what the terminal really returns for one session."""
    underlying = Underlying(symbol.upper())
    async with ThetaDataClient() as client:
        greeks, oi = await client.eod_rows(underlying, session)
    for label, got in (("greeks/eod", greeks), ("open_interest", oi)):
        print(f"{label}: {len(got)} rows")
        if got:
            print(f"  columns: {list(got[0][1])}")
            print(f"  first:   {got[0][1]}")
    prices = [r.get("underlying_price", "") for _, r in greeks]
    filled = sum(1 for p in prices if _positive(_float({"p": p}, "p")) is not None)
    print(f"underlying_price filled on {filled}/{len(prices)} rows")
    stamps = sorted({r.get("timestamp", "")[:10] for _, r in oi})
    print(f"OI timestamps (dates): {stamps[:5]}")
    snap = build_eod_snapshot(underlying, session, greeks, oi)
    known = sum(1 for c in snap.contracts if c.open_interest is not None)
    print(
        f"snapshot: {len(snap.contracts)} contracts, spot {snap.spot}, "
        f"{known} with OI, expiries {len({c.expiry for c in snap.contracts})}"
    )


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(_probe(sys.argv[1] if len(sys.argv) > 1 else "SPY", dt.date.fromisoformat(sys.argv[2])))
