"""Shares-outstanding provider interface and fetchers (T52,
plans/continuation/05-etf-flows.md; survey: docs/etf-flows-sources.md).

**Read the survey before touching this file.** It is ground truth, verified live on
2026-09-09, and it overrides the plan's *Data* section in two places this module implements
directly:

1. The iShares `?fileType=csv` endpoint no longer returns CSV -- it returns HTML under a
   `text/csv` content type. The real source is a JSON blob embedded in the product page
   (`sharesOutstanding`), which this module regexes out after unescaping the body.
2. Issuer files lag a full trading day, so every row is keyed on the issuer's own stated
   as-of date, never the day the fetch ran (`app.storage.flows_repository.insert_new_rows`
   enforces the "insert once per (symbol, date), never update" half of that rule; this module
   is only responsible for reporting the date honestly).

Only two families have a working, login-free, non-JS-rendered source (the survey's "Result at
a glance" table): State Street/SPDR (one all-funds XLSX, 17 of our symbols) and iShares (six
per-fund product pages, embedded JSON). VanEck, Invesco and USCF are JS-loaded or otherwise
unreachable without scripting a browser and stay unsupported -- `UNSUPPORTED_SYMBOLS` below,
never a fetcher, per this task's "no scraping behind a login or an anti-bot cookie, and the
four unsupported families stay unsupported" guardrail.

Mirrors `app.providers.bars`'s shape (one ABC, the `ProviderError` hierarchy reused rather
than reinvented) with one deliberate difference: `BarProvider.fetch_daily_bars` takes a single
symbol because every bars vendor here is a per-symbol endpoint, but the SPDR source is a
*single all-funds file* -- one HTTP request legitimately answers for 17 symbols at once. So
`SharesOutstandingProvider.fetch` takes an optional `symbols` subset and returns a
`SharesOutstandingFetchResult` covering however many of them the underlying request(s)
actually produced, rather than being called once per symbol.

Per-symbol failures inside one family fetch (the iShares "no sharesOutstanding key" signature
of a moved product page, or a symbol simply absent from the SPDR file) are collected into
`SharesOutstandingFetchResult.failures` and never raised -- per this task's acceptance: "a body
with no sharesOutstanding key is a named per-symbol failure, not a crash." A raised
`ProviderError` from `fetch` means the whole family's request itself failed (the SPDR file
didn't come back at all, or its header row is not the shape every column lookup depends on).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import io
import json
import logging
import re
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx

from app.providers.base import ProviderError, SymbolNotSupported, UpstreamUnavailable

__all__ = [
    "ALL_SUPPORTED_SYMBOLS",
    "FAMILY_SYMBOLS",
    "ISHARES_SYMBOLS",
    "SPDR_SYMBOLS",
    "UNSUPPORTED_SYMBOLS",
    "ISharesProductPageProvider",
    "ProviderError",
    "SharesOutstandingFetchResult",
    "SharesOutstandingProvider",
    "SharesOutstandingRow",
    "SpdrAllFundsProvider",
    "SymbolNotSupported",
    "UpstreamUnavailable",
    "parse_ishares_page",
    "parse_spdr_xlsx",
]

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0

#: The survey's "Result at a glance" table, verbatim. Order matters only for readability.
SPDR_SYMBOLS: tuple[str, ...] = (
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    "XBI", "KRE", "XOP", "SPY", "DIA", "GLD",
)

#: Symbol -> iShares product-page URL, the six URLs the survey enumerated and probed live.
ISHARES_PRODUCT_URLS: dict[str, str] = {
    "IWM": "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf",
    "TLT": "https://www.ishares.com/us/products/239454/ishares-20-year-treasury-bond-etf",
    "HYG": "https://www.ishares.com/us/products/239565/ishares-iboxx-high-yield-corporate-bond-etf",
    "EEM": "https://www.ishares.com/us/products/239637/ishares-msci-emerging-markets-etf",
    "FXI": "https://www.ishares.com/us/products/239536/ishares-china-large-cap-etf",
    "SLV": "https://www.ishares.com/us/products/239855/ishares-silver-trust",
}
ISHARES_SYMBOLS: tuple[str, ...] = tuple(ISHARES_PRODUCT_URLS)

#: The survey's four "unsupported" symbols: VanEck (SMH, GDX), Invesco (QQQ), USCF (USO) --
#: every one JS-loaded from an internal endpoint the survey deliberately did not chase (this
#: task's "no scraping behind a login or an anti-bot cookie" guardrail, and "the four
#: unsupported families stay unsupported -- do not go hunting their private APIs"). Callers
#: (the health block, the flows API) list these as "no flow data," never draw them at zero.
UNSUPPORTED_SYMBOLS: tuple[str, ...] = ("SMH", "GDX", "QQQ", "USO")

#: Family name -> the symbols that family's provider covers. Used by the health block and the
#: flows API to report per-family freshness without either module re-deriving which provider
#: owns which symbol.
FAMILY_SYMBOLS: dict[str, tuple[str, ...]] = {
    "spdr": SPDR_SYMBOLS,
    "ishares": ISHARES_SYMBOLS,
}

ALL_SUPPORTED_SYMBOLS: tuple[str, ...] = SPDR_SYMBOLS + ISHARES_SYMBOLS

#: One creation unit for these funds (the survey's recommendation): the derived-vs-printed
#: shares-outstanding cross-check flags a disagreement only beyond this many shares, since
#: anything smaller is fully explained by the printed figure's own two-decimal-places-in-
#: millions rounding.
_ONE_CREATION_UNIT = 50_000


@dataclass(frozen=True, slots=True)
class SharesOutstandingRow:
    """One fund's shares outstanding for one issuer-stated as-of date -- the
    `(symbol, as_of_date, shares_outstanding, nav | None)` tuple the survey's "What T52 should
    build" section specifies, as a named dataclass rather than a bare tuple so a caller never
    has to remember field order.
    """

    symbol: str
    as_of_date: dt.date
    shares_outstanding: int
    nav: float | None
    source: str


@dataclass(frozen=True, slots=True)
class SharesOutstandingFetchResult:
    """One `fetch()` call's outcome: every row it could produce, plus every requested symbol
    it could not, named with why. See the module docstring for why a per-symbol miss lands
    here rather than raising.
    """

    rows: list[SharesOutstandingRow]
    failures: dict[str, str] = field(default_factory=dict)


class SharesOutstandingProvider(ABC):
    """Fetch issuer-published shares outstanding for one fund family.

    Same lifecycle contract as `app.providers.bars.BarProvider`: cheap to construct, safe to
    reuse across calls, owns its own `httpx.AsyncClient` lazily with an optional injected one
    for tests.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable family identifier (`'spdr'`, `'ishares'`), written into
        `SharesOutstandingRow.source` alongside the fetch mechanism (e.g. `'spdr-xlsx'`) and
        used as the `FAMILY_SYMBOLS` key by callers grouping freshness per family."""

    @property
    @abstractmethod
    def symbols(self) -> tuple[str, ...]:
        """Every symbol this provider can ever answer for -- its family's full membership."""

    @abstractmethod
    async def fetch(self, symbols: Sequence[str] | None = None) -> SharesOutstandingFetchResult:
        """Fetch shares outstanding for `symbols` (default: every symbol in `self.symbols`).

        A symbol outside `self.symbols` is silently ignored, not a failure entry -- the caller
        (`app.jobs.flows.update_flows_job`) is expected to route each requested symbol to the
        provider(s) that cover it, and asking a provider about a symbol it never claimed to
        support is the caller's routing question, not this provider's problem to report on.

        Raises:
            ProviderError: the family-level request itself failed (network, malformed
                response, a required column/field missing). Never raised for a single
                covered symbol failing on its own -- see `SharesOutstandingFetchResult`.
        """

    async def close(self) -> None:  # pragma: no cover - default no-op, overridden where owned
        """Close any owned network resources. A no-op unless a subclass overrides it."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


# --------------------------------------------------------------------------------------------
# State Street / SPDR -- one all-funds XLSX
# --------------------------------------------------------------------------------------------

_SPDR_URL = "https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-product-data-us-en.xlsx"

#: Row 2 of the sheet is the header; data starts at row 4 (row 1 is a performance disclaimer,
#: row 3 is a secondary/units header the survey found carries no ticker rows) -- exactly as
#: measured in docs/etf-flows-sources.md and re-confirmed directly against the recorded
#: fixture while building this module.
_SPDR_HEADER_ROW = 2
_SPDR_DATA_START_ROW = 4

#: Matches the header text each required SPDR column must satisfy, keyed by our own name for
#: it. `Ticker`/`NAV`/`Shares Outstanding`/`Total Net Assets` are matched by exact text (after
#: stripping surrounding whitespace); the as-of column's real header carries a trailing space
#: and two trailing asterisks (`'As of** '`, verified verbatim in the fixture), so it is
#: matched by prefix instead of equality -- the survey's own "the trailing space is real" trap.
_SPDR_HEADER_MATCHERS: dict[str, re.Pattern[str] | None] = {
    "ticker": re.compile(r"^Ticker$"),
    "nav": re.compile(r"^NAV$"),
    "shares_outstanding": re.compile(r"^Shares Outstanding$"),
    "total_net_assets": re.compile(r"^Total Net Assets$"),
    "as_of": re.compile(r"^As of"),
}

_ROW_RE = re.compile(r'<row r="(\d+)"[^>]*>(.*?)</row>', re.DOTALL)
_CELL_RE = re.compile(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(?:<v>([^<]*)</v>)?</c>)')


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """`xl/sharedStrings.xml` -> the ordered list of strings the sheet's `t="s"` cells index
    into. Each `<si>` may hold either a plain `<t>` or several rich-text `<r><t>...</t></r>`
    runs (SPDR's trademarked tickers -- `GLD®`, `GLDM®` -- are stored as runs); concatenating
    every `<t>` found inside the `<si>` handles both shapes identically, since a plain `<si>`
    has exactly one `<t>` to concatenate.
    """
    xml = zf.read("xl/sharedStrings.xml").decode("utf-8")
    return [
        html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.DOTALL)))
        for si in re.findall(r"<si>(.*?)</si>", xml, re.DOTALL)
    ]


def _parse_row(row_xml: str, shared: list[str]) -> dict[str, str]:
    """One `<row>` element's inner XML -> `{column_letter: cell_text}`, resolving `t="s"`
    cells through `shared` and passing every other cell's raw `<v>` text through unchanged
    (SPDR's numeric-looking cells here are all formatted strings anyway -- see the module
    docstring's "values are formatted strings" note -- so no numeric cell type is expected).
    A self-closing empty cell (`<c r="B1" s="33"/>`) or one with no `<v>` at all is simply
    absent from the returned dict.
    """
    out: dict[str, str] = {}
    for col, attrs, value in _CELL_RE.findall(row_xml):
        if value == "":
            continue
        out[col] = shared[int(value)] if 't="s"' in attrs else value
    return out


def _normalize_ticker(raw: str) -> str:
    """Drop every non-A-Z character (the trademark glyph, whitespace) and uppercase what's
    left -- the survey's documented trap: SPDR's ticker column carries `GLD®`/`GLDM®` but
    plain `XLK`/`SPY`/`DIA` for everything else.
    """
    return re.sub(r"[^A-Z]", "", raw.upper())


def _parse_spdr_date(raw: str) -> dt.date:
    """`'Sep 08 2026'` -> `date(2026, 9, 8)`.

    Deliberately naive (ruff's DTZ007 suppressed below): this is a calendar date with no
    instant attached (see `app.models.db.EtfSharesOutstanding`'s docstring on why `Date`,
    not `UTCDateTime`, is the right type here), not a naive stand-in for an aware one.
    """
    return dt.datetime.strptime(raw.strip(), "%b %d %Y").date()  # noqa: DTZ007


def _parse_dollars(raw: str) -> float:
    """`'$187.88'` -> `187.88`."""
    return float(raw.replace("$", "").replace(",", "").strip())


def _parse_millions(raw: str) -> float:
    """`'651.81 M'` or `'$122,461.30 M'` -> the actual count (multiplies by 1e6). Strips `$`,
    `,` and a trailing ` M` -- the survey's "values are formatted strings, not numbers" trap.
    """
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if cleaned.endswith("M"):
        cleaned = cleaned[:-1].strip()
    return float(cleaned) * 1e6


def parse_spdr_xlsx(content: bytes, symbols: Sequence[str]) -> SharesOutstandingFetchResult:
    """Parse one SPDR all-funds XLSX response body into rows for whichever of `symbols` it
    contains.

    Reads `xl/worksheets/sheet1.xml` and `xl/sharedStrings.xml` directly out of the zip via
    stdlib `zipfile` plus a small regex-based XML scan -- **`openpyxl` is deliberately not
    used**; see this task's report for the full reasoning (adding it to the backend means a
    Docker image rebuild for a ~25-line parse the survey already proved out this way). Columns
    are looked up by header name, never by hard-coded letter, and a missing required header
    raises `UpstreamUnavailable` immediately (the survey's "fail loudly if a header is
    missing" instruction) -- the file is 53 columns wide and State Street is not contractually
    bound to keep them in the same order.

    **Shares outstanding is derived as `Total Net Assets / NAV`, not read off the printed
    `Shares Outstanding` column**, per the survey's precision analysis: `TNA` is quoted to
    $0.01M while `Shares Outstanding` is quoted to only 10,000 shares, so the derived figure
    recovers roughly an order of magnitude more precision (±~540 shares vs ±5,000). The printed
    column is still parsed and used purely as a cross-check: a disagreement beyond
    `_ONE_CREATION_UNIT` (50,000 shares) is logged as a warning, never raised -- see
    `docs/validation-scan.md` for whether that ever actually happened against the recorded
    fixture.

    Args:
        content: The raw XLSX response body (a zip archive).
        symbols: The subset of `SPDR_SYMBOLS` to look for. A symbol in this file (as ticker,
            trademark-glyph-normalized) but not in `symbols` is skipped entirely; a symbol in
            `symbols` but not found in the file becomes a `failures` entry, not a crash.

    Raises:
        UpstreamUnavailable: the zip has no worksheet/shared-strings parts, the sheet has no
            rows at all, the header row is missing, or a required header is missing from it.
    """
    wanted = {s.strip().upper() for s in symbols}
    if not wanted:
        return SharesOutstandingFetchResult(rows=[], failures={})

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            shared = _shared_strings(zf)
            sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UpstreamUnavailable(f"spdr: response body is not the expected XLSX shape: {exc}") from exc

    rows = _ROW_RE.findall(sheet_xml)
    if not rows:
        raise UpstreamUnavailable("spdr: worksheet has no rows at all")

    header_row_xml = next((body for num, body in rows if int(num) == _SPDR_HEADER_ROW), None)
    if header_row_xml is None:
        raise UpstreamUnavailable(f"spdr: header row {_SPDR_HEADER_ROW} not found")
    header_cells = _parse_row(header_row_xml, shared)

    col_for: dict[str, str] = {}
    for key, matcher in _SPDR_HEADER_MATCHERS.items():
        match = next((col for col, name in header_cells.items() if matcher.match(name.strip())), None)
        if match is None:
            raise UpstreamUnavailable(
                f"spdr: expected header for {key!r} not found among {sorted(header_cells.values())}"
            )
        col_for[key] = match

    found: dict[str, SharesOutstandingRow] = {}
    failures: dict[str, str] = {}
    for row_num, row_xml in rows:
        if int(row_num) < _SPDR_DATA_START_ROW:
            continue
        cells = _parse_row(row_xml, shared)
        ticker_raw = cells.get(col_for["ticker"])
        if not ticker_raw:
            continue
        ticker = _normalize_ticker(ticker_raw)
        if ticker not in wanted or ticker in found:
            continue
        try:
            as_of = _parse_spdr_date(cells[col_for["as_of"]])
            nav = _parse_dollars(cells[col_for["nav"]])
            tna = _parse_millions(cells[col_for["total_net_assets"]])
            printed_shares = _parse_millions(cells[col_for["shares_outstanding"]])
        except (KeyError, ValueError) as exc:
            failures[ticker] = f"row present but unparseable: {exc}"
            continue
        if nav <= 0:
            failures[ticker] = f"non-positive NAV ({nav!r}), cannot derive shares"
            continue

        derived_shares = round(tna / nav)
        disagreement = abs(derived_shares - printed_shares)
        if disagreement > _ONE_CREATION_UNIT:
            logger.warning(
                "spdr: %s derived shares %.0f vs printed %.0f differ by %.0f shares "
                "(> one creation unit, %d)",
                ticker,
                derived_shares,
                printed_shares,
                disagreement,
                _ONE_CREATION_UNIT,
            )
        found[ticker] = SharesOutstandingRow(
            symbol=ticker,
            as_of_date=as_of,
            shares_outstanding=derived_shares,
            nav=nav,
            source="spdr-xlsx",
        )

    for symbol in wanted - found.keys() - failures.keys():
        failures[symbol] = "not found in SPDR all-funds file"

    return SharesOutstandingFetchResult(rows=list(found.values()), failures=failures)


class SpdrAllFundsProvider(SharesOutstandingProvider):
    """Fetches the one SPDR all-funds XLSX and parses out whichever `SPDR_SYMBOLS` were
    requested. See `parse_spdr_xlsx` for the parsing itself; this class owns only the HTTP
    fetch (with the same retry/backoff shape `app.providers.yahoo.YahooBarProvider` uses).
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    @property
    def name(self) -> str:
        return "spdr"

    @property
    def symbols(self) -> tuple[str, ...]:
        return SPDR_SYMBOLS

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,  # the survey's "requires -L" note: /intermediary/ 301s
            )
        return self._client

    async def fetch(self, symbols: Sequence[str] | None = None) -> SharesOutstandingFetchResult:
        wanted = [s for s in (symbols if symbols is not None else self.symbols) if s in self.symbols]
        if not wanted:
            return SharesOutstandingFetchResult(rows=[], failures={})

        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(_SPDR_URL)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning("spdr: attempt %d/%d transport error: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 200:
                return parse_spdr_xlsx(response.content, wanted)

            last_exc = httpx.HTTPStatusError(
                f"unexpected status {response.status_code}", request=response.request, response=response
            )
            logger.warning("spdr: attempt %d/%d status %d", attempt, self._max_retries, response.status_code)
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * attempt)

        raise UpstreamUnavailable(f"spdr: request failed after {self._max_retries} attempts") from last_exc


# --------------------------------------------------------------------------------------------
# iShares -- one product-page fetch per fund, embedded JSON
# --------------------------------------------------------------------------------------------

#: Matches `"sharesOutstanding":{...}` or `"navAmount":{...}` after the body has been
#: HTML-entity-unescaped. Neither object nests another `{`/`}` in the samples the survey
#: recorded, so a non-greedy `.*?` up to the first `}` is sufficient and avoids needing a real
#: brace-balancing scanner for what is, in practice, always a single flat JSON object.
_ISHARES_FIELD_RE = re.compile(r'"(sharesOutstanding|navAmount)":(\{.*?\})', re.DOTALL)


def parse_ishares_page(body: str, symbol: str) -> SharesOutstandingRow:
    """Parse one iShares product-page response body for `symbol`.

    **Unescapes the body first, unconditionally.** The survey found the same field served raw
    on one fund's page and HTML-entity-escaped on another's, at the same instant -- `IWM` came
    back raw, `TLT` escaped. `html.unescape` on already-plain text is a no-op, so doing it
    always (rather than sniffing which form a given page uses) handles both without a branch.

    `navAmount`'s own `formattedValue` is used for NAV when present; this table's `nav` column
    is genuinely optional (see `app.models.db.EtfSharesOutstanding`'s docstring) because a
    `navAmount` block is not guaranteed to exist on every page shape -- the fixtures this
    parser was built against do not carry one at all.

    Args:
        body: The raw (or HTML-entity-escaped) response text.
        symbol: The plain ticker this page is for, written straight into the returned row --
            never re-derived from the page body itself.

    Raises:
        ValueError: no `sharesOutstanding` field was found, or it is missing the two fields
            this parser needs (`formattedValue`, `formattedAsOfDate`) -- the survey's
            documented "signature of a moved page" for a fund whose product id has changed.
            Callers (`ISharesProductPageProvider.fetch`) catch this and record it as a named
            per-symbol failure rather than letting it crash the whole family fetch.
    """
    unescaped = html.unescape(body)
    fields: dict[str, dict] = {}
    for field_name, blob in _ISHARES_FIELD_RE.findall(unescaped):
        try:
            fields[field_name] = json.loads(blob)
        except json.JSONDecodeError:
            continue

    shares_field = fields.get("sharesOutstanding")
    if (
        shares_field is None
        or not shares_field.get("formattedValue")
        or not shares_field.get("formattedAsOfDate")
    ):
        raise ValueError(
            "no sharesOutstanding field found (page layout may have moved -- product id stale?)"
        )

    try:
        shares = int(str(shares_field["formattedValue"]).replace(",", ""))
        # Same rationale as `_parse_spdr_date`: a calendar date, not an instant.
        as_of = dt.datetime.strptime(  # noqa: DTZ007
            shares_field["formattedAsOfDate"].strip(), "%b %d, %Y"
        ).date()
    except ValueError as exc:
        raise ValueError(f"sharesOutstanding field present but unparseable: {exc}") from exc

    nav: float | None = None
    nav_field = fields.get("navAmount")
    if nav_field is not None and nav_field.get("formattedValue"):
        try:
            nav = float(str(nav_field["formattedValue"]).replace(",", ""))
        except ValueError:
            nav = None  # a malformed NAV never blocks the shares-outstanding row itself

    return SharesOutstandingRow(
        symbol=symbol,
        as_of_date=as_of,
        shares_outstanding=shares,
        nav=nav,
        source="ishares-productpage",
    )


class ISharesProductPageProvider(SharesOutstandingProvider):
    """Fetches one product page per requested `ISHARES_SYMBOLS` member and parses each with
    `parse_ishares_page`. Unlike `SpdrAllFundsProvider`, a per-symbol failure here is genuinely
    per-request (each fund is its own HTTP call), so this class is what turns
    `parse_ishares_page`'s `ValueError` into a `SharesOutstandingFetchResult.failures` entry.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    @property
    def name(self) -> str:
        return "ishares"

    @property
    def symbols(self) -> tuple[str, ...]:
        return ISHARES_SYMBOLS

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _USER_AGENT})
        return self._client

    async def _fetch_one(self, symbol: str) -> SharesOutstandingRow:
        url = ISHARES_PRODUCT_URLS[symbol]
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(url)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "ishares: attempt %d/%d transport error for %s: %s",
                    attempt, self._max_retries, symbol, exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 200:
                return parse_ishares_page(response.text, symbol)

            last_exc = httpx.HTTPStatusError(
                f"unexpected status {response.status_code}", request=response.request, response=response
            )
            logger.warning(
                "ishares: attempt %d/%d status %d for %s",
                attempt, self._max_retries, response.status_code, symbol,
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * attempt)

        raise UpstreamUnavailable(
            f"ishares: {symbol} request failed after {self._max_retries} attempts"
        ) from last_exc

    async def fetch(self, symbols: Sequence[str] | None = None) -> SharesOutstandingFetchResult:
        wanted = [s for s in (symbols if symbols is not None else self.symbols) if s in self.symbols]

        rows: list[SharesOutstandingRow] = []
        failures: dict[str, str] = {}
        for symbol in wanted:
            try:
                rows.append(await self._fetch_one(symbol))
            except ValueError as exc:
                # parse_ishares_page's "moved page" signature -- a named per-symbol failure,
                # not a crash, per this task's acceptance.
                failures[symbol] = str(exc)
            except ProviderError as exc:
                failures[symbol] = str(exc)

        return SharesOutstandingFetchResult(rows=rows, failures=failures)
