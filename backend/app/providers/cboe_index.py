"""Cboe index-history daily-bar provider (T54, plans/continuation/06-cross-asset-regime.md).

Free, keyless CSVs at ``https://cdn.cboe.com/api/global/us_indices/daily_prices/{IX}_History.csv``
-- a different Cboe host path from the delayed-quotes options endpoint
(``app.providers.cboe``, **not modified by this task**). Reuses that module's ``httpx``
client-injection / retry / ``ProviderError`` pattern (same constructor shape, same
``_get_client``/``close``/``__aenter__``/``__aexit__`` lifecycle), but is otherwise
independent: a bars provider (``app.providers.bars.BarProvider``), not an
``OptionChainProvider``, and CSV rather than JSON on the wire.

Live verification, 2026-09-09 (supervisor; recorded here per the plan's "record in the
provider docstring which exist, their column names, and the earliest date")
--------------------------------------------------------------------------------------------
All six URLs below were live-probed and returned ``200 text/csv``, header on line 1 (no
preamble), dates ``MM/DD/YYYY``. Every file already carried that day's own row at 21:00 ET, so
the 17:30 ET bars job gets same-day data (same as `app.providers.yahoo`'s settled-session
data, but Cboe's own CSV needs no partial-bar handling -- see below).

| Symbol   | Vendor file    | Columns                        | Earliest date |
|----------|----------------|---------------------------------|----------------|
| ``^VIX``   | ``VIX_History``   | ``DATE,OPEN,HIGH,LOW,CLOSE``   | 01/02/1990     |
| ``^VIX9D`` | ``VIX9D_History`` | ``DATE,OPEN,HIGH,LOW,CLOSE``   | 01/04/2011     |
| ``^VIX3M`` | ``VIX3M_History`` | ``DATE,OPEN,HIGH,LOW,CLOSE``   | 09/18/2009     |
| ``^VIX6M`` | ``VIX6M_History`` | ``DATE,OPEN,HIGH,LOW,CLOSE``   | 01/02/2008     |
| ``^VVIX``  | ``VVIX_History``  | ``DATE,VVIX`` (close only)     | 03/06/2006     |
| ``^SKEW``  | ``SKEW_History``  | ``DATE,SKEW`` (close only)     | 01/02/1990     |

All six exist -- nothing is dropped from :data:`_VENDOR_SYMBOL` (the plan's "a missing index
is dropped from the default group with a comment, not guessed at" only applies if a probe ever
comes back missing; it did not).

The close-only-schema decision (VVIX, SKEW)
--------------------------------------------
``app.models.bars.DailyBar`` declares ``open``/``high``/``low``/``close`` all required,
non-null floats, but VVIX and SKEW publish a close only. Of the three options the plan lays
out (store ``open = high = low = close``; widen the four columns to nullable; keep these two
out of ``daily_bars`` entirely), **this module takes option 1**: a close-only row is stored
with all four OHLC fields set to that one close. This is not a guess -- it is **Cboe's own
convention for its own early history**: the 1990 rows of the ``^VIX`` file itself are
literally ``17.240000,17.240000,17.240000,17.240000``, so the vendor's own OHLC file format
already carries a "closes dressed as OHLC" precedent for exactly this situation (see
``backend/tests/fixtures/cboe_index/README.md``, which keeps those 1990 rows for this exact
reason). Option 2 (widen the schema to nullable) is a migration plus a nullability change
propagating through every reader of ``daily_bars`` -- a much larger change than this task's
scope, and exactly the class of change the supervision report flagged for hiding type errors
elsewhere. Option 3 contradicts the plan's own "no new table" constraint.

The cost of option 1: a consumer cannot tell "no intraday range published" from "a genuinely
flat day" from the stored row alone. The mitigation is ``source="cboe_index"`` (this module's
own ``name``) plus this paragraph -- a caller computing anything from ``high``/``low`` on a
``cboe_index``-sourced row (nothing in this task does; only ``close`` is read anywhere in
``app.scan.cross_asset``) must treat that range as fabricated. This is restated, not merely
cross-referenced, in ``docs/validation-scan.md``.

``^VIX`` changes hands from Yahoo to Cboe
-------------------------------------------
``T42`` already fetches ``^VIX`` from ``app.providers.yahoo`` (``BARS_PROVIDER=yahoo`` is the
default for every symbol not listed in a ``BAR_PROVIDER_GROUPS`` entry). Listing ``^VIX`` in
the ``cboe_index`` group (``app.config.settings.BAR_PROVIDER_GROUPS``) moves it here, because
``BarProviderRegistry.provider_name_for`` resolves a group entry ahead of the default
(``app.providers.bars``'s own module docstring). This is deliberate, not incidental: the Cboe
file is native OHLC back to 1990 and has none of Yahoo's partial-last-bar problem (see
``app.providers.yahoo``'s own module docstring for why that problem exists there at all).
Existing Yahoo-sourced ``^VIX`` rows carry ``source="yahoo-splitadj"``; ``upsert_bars`` keys
its ``ON CONFLICT`` on ``(symbol, date)`` and its ``set_`` clause includes ``source`` (see
``app.storage.bars_repository.upsert_bars``), so a re-backfill through this provider
overwrites each existing ``^VIX`` row's OHLCV **and** its ``source`` in place -- the changeover
is recorded in the column, not silently left stale on old rows or duplicated as a second
source per date.

Failure modes this module guards against
--------------------------------------------
* **A non-CSV 200 body** (Cboe, like its own options endpoint, can serve an HTML error/denial
  page under a 200 -- see ``backend/tests/fixtures/cboe_index/not-a-csv-error-body.html``).
  Detected by scanning for a header row starting with ``DATE`` within the first
  :data:`_MAX_HEADER_SEARCH_LINES` lines (see :func:`_find_header`); a body where none is found
  raises :class:`~app.providers.bars.UpstreamUnavailable`, never a bare parse exception.
* **A header not on line 1.** Nothing observed live has a preamble (the verification table
  above), but the plan's own "likely first-contact failures" names this explicitly, so the
  header is located by content, not assumed to be row 0.
* **The ``^`` prefix mismatch.** ``BAR_PROVIDER_GROUPS`` and every caller in this app name the
  symbol ``^VIX``; the vendor's own file is ``VIX_History.csv`` (no caret, no underscore). The
  mapping lives in :data:`_VENDOR_SYMBOL`, one line per symbol, never string-manipulated.
* **A bad or unparseable row** (a blank line, a row with a non-numeric field) is skipped and
  logged, one bad row never blanking the whole fetch -- same "skip-and-log" posture
  ``app.providers.cboe``'s unknown-root policy and ``app.providers.yahoo``'s null-OHLC skip
  both already use.
* **Volume.** These CSVs carry no volume column at all; every ``DailyBar`` here has
  ``volume=None`` (the vendor did not report it -- CLAUDE.md invariant 3's None-vs-zero
  discipline, the "no field at all" case named in ``app.models.bars.DailyBar``'s own
  docstring), never a fabricated ``0``.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import logging
from typing import Self

import httpx
from pydantic import ValidationError

from app.models.bars import DailyBar
from app.providers.bars import BarProvider, SymbolNotSupported, UpstreamUnavailable

__all__ = ["CboeIndexHistoryProvider"]

logger = logging.getLogger(__name__)

_BASE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{ix}_History.csv"

# Cboe's CDN, like its delayed-quotes endpoint, occasionally rejects a bare httpx default user
# agent -- same browser-like string as app.providers.cboe, same rationale.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Plain ticker (as every caller in this app knows it, "^"-prefixed) -> Cboe's own CSV file
# stem. See module docstring's "^ prefix mismatch" failure mode.
_VENDOR_SYMBOL: dict[str, str] = {
    "^VIX": "VIX",
    "^VIX9D": "VIX9D",
    "^VIX3M": "VIX3M",
    "^VIX6M": "VIX6M",
    "^VVIX": "VVIX",
    "^SKEW": "SKEW",
}

# Symbols whose CSV publishes a close only (DATE,<NAME>), not OHLC (DATE,OPEN,HIGH,LOW,CLOSE).
# See module docstring's close-only-schema decision.
_CLOSE_ONLY_SYMBOLS = frozenset({"^VVIX", "^SKEW"})

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0

# Defensive header search: the live verification found the header on line 1 with no preamble
# for every one of the six files, but the plan names a multi-line preamble as a likely
# first-contact failure, so this never assumes a fixed offset -- see _find_header.
_MAX_HEADER_SEARCH_LINES = 10


def _parse_date(raw: str) -> dt.date:
    """Cboe's ``MM/DD/YYYY`` date format. Raises `ValueError` for anything else -- caught by
    the per-row skip in :meth:`CboeIndexHistoryProvider._parse_csv`.

    Deliberately naive (ruff's DTZ007 suppressed below), same as
    `app.providers.etf_flows`'s own date-only parse: this is a calendar date with no time
    component to attach a zone to -- see `app.models.bars.DailyBar`'s own docstring for why
    `date` is a `datetime.date`, never a `datetime`.
    """
    return dt.datetime.strptime(raw.strip(), "%m/%d/%Y").date()  # noqa: DTZ007


def _find_header(lines: list[str]) -> tuple[int, list[str]] | None:
    """The (line index, uppercased comma-split field names) of the first line whose first
    field is ``DATE``, searched within the first `_MAX_HEADER_SEARCH_LINES` lines only --
    `None` if no such line exists (a non-CSV body, e.g. an HTML error page)."""
    for i, line in enumerate(lines[:_MAX_HEADER_SEARCH_LINES]):
        stripped = line.strip()
        if not stripped:
            continue
        fields = [f.strip().upper() for f in stripped.split(",")]
        if fields and fields[0] == "DATE":
            return i, fields
    return None


class CboeIndexHistoryProvider(BarProvider):
    """Fetches daily OHLCV(-shaped) bars for the six Cboe volatility/skew indices in
    :data:`_VENDOR_SYMBOL` from Cboe's free, keyless index-history CSV endpoint.
    """

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
            client: An existing `httpx.AsyncClient` to use instead of creating one lazily.
                Tests inject a client built on `httpx.MockTransport` so the suite never touches
                the network; the caller who injects a client also owns closing it.
            timeout: Per-request timeout in seconds.
            max_retries: Total attempts (not additional retries) before raising
                `UpstreamUnavailable`.
            backoff_seconds: Base for the linear backoff between attempts (`0` for instant
                retries in tests).
        """
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    @property
    def name(self) -> str:
        return "cboe_index"

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

    async def _fetch_csv(self, url: str, *, requested_symbol: str) -> str:
        """GET `url` with retries and linear backoff, returning the raw response body text.

        Same retry shape as `app.providers.cboe._fetch_json`: every non-2xx status retries
        through the budget and then raises `UpstreamUnavailable` -- there is no equivalent of
        that module's "structured 404" special case here, since this endpoint has no
        documented not-found shape to special-case.
        """
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                logger.warning(
                    "cboe_index: attempt %d/%d failed for %s (%s): %s",
                    attempt,
                    self._max_retries,
                    requested_symbol,
                    url,
                    exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
        raise UpstreamUnavailable(
            f"cboe_index request failed after {self._max_retries} attempts: "
            f"{requested_symbol} ({url})"
        ) from last_exc

    async def fetch_daily_bars(
        self, symbol: str, *, start: dt.date, end: dt.date | None = None
    ) -> list[DailyBar]:
        requested_symbol = symbol.strip().upper()
        vendor_ix = _VENDOR_SYMBOL.get(requested_symbol)
        if vendor_ix is None:
            raise SymbolNotSupported(
                f"cboe_index: no index history for {requested_symbol!r}; supported symbols "
                f"are {sorted(_VENDOR_SYMBOL)}"
            )

        url = _BASE_URL.format(ix=vendor_ix)
        text = await self._fetch_csv(url, requested_symbol=requested_symbol)
        return self._parse_csv(requested_symbol, text, start=start, end=end)

    def _parse_csv(
        self, requested_symbol: str, text: str, *, start: dt.date, end: dt.date | None
    ) -> list[DailyBar]:
        """Turn one CSV response body into ascending `DailyBar`s for `requested_symbol`,
        filtered to `[start, end]` (or `[start, ...]` when `end` is `None`).

        Raises:
            UpstreamUnavailable: no `DATE`-led header row was found within the first
                `_MAX_HEADER_SEARCH_LINES` lines (a non-CSV body -- see the module docstring's
                first failure mode), or the header's shape does not match either the OHLC or
                the close-only schema this module knows about.
        """
        lines = text.splitlines()
        found = _find_header(lines)
        if found is None:
            raise UpstreamUnavailable(
                f"cboe_index: no CSV header found in the response for {requested_symbol!r} "
                f"(non-CSV body?)"
            )
        header_idx, fields = found

        close_only = requested_symbol in _CLOSE_ONLY_SYMBOLS
        if close_only:
            if len(fields) < 2:
                raise UpstreamUnavailable(
                    f"cboe_index: close-only header for {requested_symbol!r} has fewer than "
                    f"2 columns: {fields!r}"
                )
            close_col = 1
        else:
            required = {"OPEN", "HIGH", "LOW", "CLOSE"}
            if not required.issubset(fields):
                raise UpstreamUnavailable(
                    f"cboe_index: OHLC header for {requested_symbol!r} is missing one of "
                    f"{sorted(required)}: {fields!r}"
                )
            open_col = fields.index("OPEN")
            high_col = fields.index("HIGH")
            low_col = fields.index("LOW")
            close_col = fields.index("CLOSE")

        bars: list[DailyBar] = []
        skipped = 0
        for row in csv.reader(lines[header_idx + 1 :]):
            if not row or not row[0].strip():
                continue
            try:
                date = _parse_date(row[0])
            except ValueError:
                skipped += 1
                continue

            if date < start or (end is not None and date > end):
                continue

            try:
                if close_only:
                    close = float(row[close_col])
                    o = h = low = close
                else:
                    o = float(row[open_col])
                    h = float(row[high_col])
                    low = float(row[low_col])
                    close = float(row[close_col])
            except (ValueError, IndexError):
                skipped += 1
                continue

            try:
                bars.append(
                    DailyBar(
                        symbol=requested_symbol,
                        date=date,
                        open=o,
                        high=h,
                        low=low,
                        close=close,
                        volume=None,  # not published by this endpoint -- see module docstring
                        source=self.name,
                    )
                )
            except ValidationError as exc:
                skipped += 1
                logger.warning(
                    "cboe_index: skipping %s row dated %s: %s", requested_symbol, date, exc
                )

        if skipped:
            logger.warning(
                "cboe_index: skipped %d unparseable/rejected row(s) for %s",
                skipped,
                requested_symbol,
            )

        bars.sort(key=lambda b: b.date)
        return bars


async def _run_cli(symbol: str, years: int) -> None:
    today = dt.datetime.now(dt.UTC).date()
    async with CboeIndexHistoryProvider() as provider:
        bars = await provider.fetch_daily_bars(symbol, start=today - dt.timedelta(days=365 * years))
    print(f"{symbol}: {len(bars)} bars")
    if bars:
        print(f"  first={bars[0].date} last={bars[-1].date}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) not in (2, 3):
        print("usage: python -m app.providers.cboe_index SYMBOL [years]", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_run_cli(sys.argv[1], int(sys.argv[2]) if len(sys.argv) == 3 else 5))
