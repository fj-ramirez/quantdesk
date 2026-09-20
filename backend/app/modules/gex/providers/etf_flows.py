"""Shares-outstanding provider interface and fetchers (T52,
plans/continuation/05-etf-flows.md; T59, VanEck/Invesco/USCF; survey: docs/etf-flows-sources.md).

**Read the survey before touching this file.** It is ground truth -- T52's half verified live
on 2026-09-09, T59's addition on 2026-09-10 -- and it overrides the plan's *Data* section in
two places this module implements directly:

1. The iShares `?fileType=csv` endpoint no longer returns CSV -- it returns HTML under a
   `text/csv` content type. The real source is a JSON blob embedded in the product page
   (`sharesOutstanding`), which this module regexes out after unescaping the body.
2. Issuer files lag a full trading day, so every row is keyed on the issuer's own stated
   as-of date, never the day the fetch ran (`app.modules.gex.storage.flows_repository.insert_new_rows`
   enforces the "insert once per (symbol, date), never update" half of that rule; this module
   is only responsible for reporting the date honestly).

T52 found a working source for two families (State Street/SPDR, one all-funds XLSX; iShares,
six per-fund product pages) and left three unreached because their pages render shares
outstanding client-side: VanEck (SMH, GDX), Invesco (QQQ), USCF (USO). T59 went looking for
the JSON endpoint each page's own JavaScript calls -- by driving the page with Playwright and
watching its network requests, not by guessing paths -- and found one for every one of them,
all public, unauthenticated `GET`s with a browser `User-Agent`, no login and no anti-bot
cookie:

- **VanEck**: `Main/FundDetailsBlock/GetContent` -- the same content block (`blockid=229617`)
  both funds' pages fetch, parameterized by `pageid` (per-fund) and `ticker`. Returns a
  `Values` list of `{Title, Value}` pairs; `Shares Outstanding` is one of them, in full
  precision (no `M`/`K` suffix), with the block's own `LongVersionAsOfDate` -- not a per-value
  date -- as the as-of date. No NAV field in this block, so `nav` is always `None` here.
- **Invesco**: `dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/{ticker}` with
  `variationType=fundDetails`. Returns one flat JSON object with `sharesOutstanding` (full
  precision), `nav`, and `effectiveDate` as the as-of date. An unrecognized ticker returns
  literal `""` (still HTTP 200), not an object. It is a plain public GET: re-verified on
  2026-09-10 with *no* headers at all (no `User-Agent`, no `Origin`), five for five 200s with
  the full payload. T59 had recorded a 406 without `Origin` the previous evening, which did not
  reproduce -- the headers in `_INVESCO_REQUIRED_HEADERS` are therefore defensive, not required;
  see that constant's own comment before treating them as load-bearing.
- **USCF**: a two-step fetch. `site-template/assets/javascript/api_key.php` mints a short-lived
  bearer JWT (no login -- it is the same anonymous token every visitor's browser gets); that
  token authorizes `secure.alpsinc.com/MarketingAPI/api/v1/dailyprice/{ticker}`, which returns
  a one-element JSON array with `so` (shares outstanding, full precision), `nav`, and
  `displaydate` as the as-of date. An unrecognized ticker 404s.

Mirrors `app.modules.gex.providers.bars`'s shape (one ABC, the `ProviderError` hierarchy reused rather
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
For USCF specifically, a failed *token* fetch is a family-level `ProviderError` too -- without
a token nothing in the family can be fetched, so that failure mode is not a per-symbol one.

`UNSUPPORTED_SYMBOLS` is empty as of T59: every symbol the T52 survey found now has a working
fetcher. The name and shape are kept (rather than deleted) because it is still the honest place
to list a family that a *future* survey finds and cannot clear the "no login, no anti-bot
cookie/captcha" bar -- see this task's own report in docs/etf-flows-sources.md for what was
checked and rejected along the way (there was nothing to reject here; all three cleared it).
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

from app.modules.gex.providers.base import ProviderError, SymbolNotSupported, UpstreamUnavailable

__all__ = [
    "ALL_SUPPORTED_SYMBOLS",
    "FAMILY_SYMBOLS",
    "INVESCO_SYMBOLS",
    "ISHARES_SYMBOLS",
    "SPDR_SYMBOLS",
    "UNSUPPORTED_SYMBOLS",
    "USCF_SYMBOLS",
    "VANECK_SYMBOLS",
    "ISharesProductPageProvider",
    "InvescoShareclassProvider",
    "ProviderError",
    "SharesOutstandingFetchResult",
    "SharesOutstandingProvider",
    "SharesOutstandingRow",
    "SpdrAllFundsProvider",
    "SymbolNotSupported",
    "USCFDailyPriceProvider",
    "UpstreamUnavailable",
    "VanEckFundDetailsProvider",
    "parse_invesco_shareclass",
    "parse_ishares_page",
    "parse_spdr_xlsx",
    "parse_uscf_dailyprice",
    "parse_vaneck_fund_details",
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

#: Symbol -> VanEck `pageid` (T59). Both funds' pages fetch the same `blockid` (229617, the
#: "Fund Details" panel) -- only `pageid` and `ticker` vary per fund. Found by driving each
#: page with Playwright and watching its network requests for the `FundDetailsBlock` call.
VANECK_PAGE_IDS: dict[str, str] = {
    "SMH": "233107",
    "GDX": "233083",
}
VANECK_SYMBOLS: tuple[str, ...] = tuple(VANECK_PAGE_IDS)

#: T59: the one Invesco fund in our universe. `dng-api.invesco.com` is keyed by ticker
#: directly, so there is nothing per-symbol to look up beyond the ticker itself -- unlike
#: VanEck's `pageid`, no separate id map is needed.
INVESCO_SYMBOLS: tuple[str, ...] = ("QQQ",)

#: T59: the one USCF fund in our universe.
USCF_SYMBOLS: tuple[str, ...] = ("USO",)

#: Every symbol the T52 survey found no working source for at all. Empty as of T59 -- VanEck,
#: Invesco and USCF each turned out to have a public, login-free, non-anti-bot JSON endpoint
#: once the right network request was found (see the module docstring). Kept as a named,
#: exported constant rather than deleted: it is still the right place for a future survey to
#: record a family that fails the "no login, no anti-bot cookie/captcha" bar.
UNSUPPORTED_SYMBOLS: tuple[str, ...] = ()

#: Family name -> the symbols that family's provider covers. Used by the health block and the
#: flows API to report per-family freshness without either module re-deriving which provider
#: owns which symbol.
FAMILY_SYMBOLS: dict[str, tuple[str, ...]] = {
    "spdr": SPDR_SYMBOLS,
    "ishares": ISHARES_SYMBOLS,
    "vaneck": VANECK_SYMBOLS,
    "invesco": INVESCO_SYMBOLS,
    "uscf": USCF_SYMBOLS,
}

ALL_SUPPORTED_SYMBOLS: tuple[str, ...] = (
    SPDR_SYMBOLS + ISHARES_SYMBOLS + VANECK_SYMBOLS + INVESCO_SYMBOLS + USCF_SYMBOLS
)

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

    Same lifecycle contract as `app.modules.gex.providers.bars.BarProvider`: cheap to construct, safe to
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
        (`app.modules.gex.jobs.flows.update_flows_job`) is expected to route each requested symbol to the
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
    instant attached (see `app.modules.gex.models.db.EtfSharesOutstanding`'s docstring on why `Date`,
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
    fetch (with the same retry/backoff shape `app.modules.gex.providers.yahoo.YahooBarProvider` uses).
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
    is genuinely optional (see `app.modules.gex.models.db.EtfSharesOutstanding`'s docstring) because a
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


# --------------------------------------------------------------------------------------------
# VanEck (T59) -- one product-page-block fetch per fund, plain JSON
# --------------------------------------------------------------------------------------------

_VANECK_FUND_DETAILS_URL = "https://www.vaneck.com/Main/FundDetailsBlock/GetContent/"
_VANECK_BLOCK_ID = "229617"


def parse_vaneck_fund_details(body: str, symbol: str) -> SharesOutstandingRow:
    """Parse one VanEck `Main/FundDetailsBlock/GetContent` response body for `symbol`.

    Unlike iShares, this is plain JSON (`Content-Type: application/json`) -- no HTML-entity
    escaping to undo. The shape (found by driving `https://www.vaneck.com/us/en/investments/
    semiconductor-etf-smh/` with Playwright and watching its network requests, per this task's
    brief -- the page's own text never carries the value, only the survey's already-recorded
    finding): `{"data": {"LongVersionAsOfDate": "MM/DD/YYYY", "Values": [{"Title": ...,
    "Value": ...}, ...]}}`. `Shares Outstanding` is one of the `Values` entries, printed at
    full precision (`"123,891,874"`, not `"123.89 M"`) -- so, unlike SPDR, there is no unit
    ambiguity to resolve, only commas to strip.

    **The as-of date comes from the block's own `LongVersionAsOfDate`, not a per-value date.**
    Every entry in `Values` (including the `Shares Outstanding` one) carries `"AsOfDate": null`
    in every fixture this parser was built against -- VanEck dates the whole panel, not each
    field in it separately.

    This block has no NAV field at all (VanEck shows NAV in a different part of the page, not
    this one), so the returned row's `nav` is always `None` -- same optionality as
    `parse_ishares_page`'s, for the same reason (`app.modules.gex.models.db.EtfSharesOutstanding.nav`'s
    docstring).

    Args:
        body: The raw JSON response text.
        symbol: The plain ticker this page is for, written straight into the returned row --
            never re-derived from the page body itself.

    Raises:
        ValueError: the body is not JSON, is missing `data`/`Values`/a `Shares Outstanding`
            entry, or is missing `LongVersionAsOfDate` -- every one of these the "page layout
            moved" signature this task's acceptance calls for: a named per-symbol failure, not
            a crash. `LongVersionAsOfDate` missing is deliberately fatal too (not defaulted to
            "today"): per this task's as-of-date rule, a source with no as-of date at all is not
            a usable source for a given fetch, and reporting a wrong date would be worse than
            reporting a failure.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response body is not JSON: {exc}") from exc

    block = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        raise ValueError("no 'data' object in response (page layout may have moved)")  # noqa: TRY004

    values = block.get("Values")
    if not isinstance(values, list):
        raise ValueError("no 'Values' array in response (page layout may have moved)")  # noqa: TRY004

    shares_entry = next(
        (v for v in values if isinstance(v, dict) and v.get("Title") == "Shares Outstanding"),
        None,
    )
    if shares_entry is None or not shares_entry.get("Value"):
        raise ValueError(
            "no 'Shares Outstanding' entry in Values (page layout may have moved)"
        )

    as_of_raw = block.get("LongVersionAsOfDate")
    if not as_of_raw:
        raise ValueError("no LongVersionAsOfDate in response -- cannot key this row honestly")

    try:
        shares = int(str(shares_entry["Value"]).replace(",", "").strip())
        # Same rationale as `_parse_spdr_date`: a calendar date, not an instant.
        as_of = dt.datetime.strptime(str(as_of_raw).strip(), "%m/%d/%Y").date()  # noqa: DTZ007
    except ValueError as exc:
        raise ValueError(f"Shares Outstanding entry present but unparseable: {exc}") from exc

    if shares <= 0:
        raise ValueError(f"non-positive shares outstanding ({shares!r})")

    return SharesOutstandingRow(
        symbol=symbol,
        as_of_date=as_of,
        shares_outstanding=shares,
        nav=None,
        source="vaneck-funddetails",
    )


class VanEckFundDetailsProvider(SharesOutstandingProvider):
    """Fetches one `FundDetailsBlock` per requested `VANECK_SYMBOLS` member and parses each
    with `parse_vaneck_fund_details`. Same per-request-per-symbol shape as
    `ISharesProductPageProvider` (each fund is its own HTTP call, so a per-symbol failure here
    is genuinely per-request), with `pageid` looked up per symbol from `VANECK_PAGE_IDS`
    alongside the one `blockid` both funds share.
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
        return "vaneck"

    @property
    def symbols(self) -> tuple[str, ...]:
        return VANECK_SYMBOLS

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _USER_AGENT})
        return self._client

    async def _fetch_one(self, symbol: str) -> SharesOutstandingRow:
        client = await self._get_client()
        params = {
            "blockid": _VANECK_BLOCK_ID,
            "pageid": VANECK_PAGE_IDS[symbol],
            "ticker": symbol,
            "reactlang": "en",
            "reactctr": "us",
            "epieditmode": "false",
            "latest": "false",
            "contextmode": "Default",
        }
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(_VANECK_FUND_DETAILS_URL, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "vaneck: attempt %d/%d transport error for %s: %s",
                    attempt, self._max_retries, symbol, exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 200:
                return parse_vaneck_fund_details(response.text, symbol)

            last_exc = httpx.HTTPStatusError(
                f"unexpected status {response.status_code}", request=response.request, response=response
            )
            logger.warning(
                "vaneck: attempt %d/%d status %d for %s",
                attempt, self._max_retries, response.status_code, symbol,
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * attempt)

        raise UpstreamUnavailable(
            f"vaneck: {symbol} request failed after {self._max_retries} attempts"
        ) from last_exc

    async def fetch(self, symbols: Sequence[str] | None = None) -> SharesOutstandingFetchResult:
        wanted = [s for s in (symbols if symbols is not None else self.symbols) if s in self.symbols]

        rows: list[SharesOutstandingRow] = []
        failures: dict[str, str] = {}
        for symbol in wanted:
            try:
                rows.append(await self._fetch_one(symbol))
            except ValueError as exc:
                failures[symbol] = str(exc)
            except ProviderError as exc:
                failures[symbol] = str(exc)

        return SharesOutstandingFetchResult(rows=rows, failures=failures)


# --------------------------------------------------------------------------------------------
# Invesco (T59) -- one per-fund JSON fetch, keyed directly by ticker
# --------------------------------------------------------------------------------------------

_INVESCO_SHARECLASS_URL = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/{symbol}"

#: Sent defensively, **not because the endpoint demands them.** T59 recorded that
#: `dng-api.invesco.com` 406s without an `Origin` header; on supervisor re-verification the next
#: morning (2026-09-10) that did not reproduce -- five consecutive requests with *no* headers at
#: all, not even a `User-Agent`, each returned 200 with the full payload. So whatever produced
#: those 406s was transient, or specific to that moment rather than to the missing header.
#:
#: These are kept anyway: they cost nothing, they are what the site's own JS sends, and if the
#: gateway does gate intermittently (or from other networks) this is the shape that passed. They
#: are fixed public values, never a session token, cookie or credential -- so this stays inside
#: the "no login, no anti-bot cookie, no captcha" constraint either way. What must **not** be
#: inferred from their presence is that the endpoint requires them; if a future change makes
#: them a problem, drop them and re-measure rather than assuming they are load-bearing.
_INVESCO_REQUIRED_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "https://www.invesco.com",
    "Referer": "https://www.invesco.com/qqq-etf/en/about.html",
}


def parse_invesco_shareclass(body: str, symbol: str) -> SharesOutstandingRow:
    """Parse one Invesco `dng-api.invesco.com/.../shareclasses/{ticker}?variationType=
    fundDetails` response body for `symbol`.

    Found the same way as the VanEck endpoint: driving `https://www.invesco.com/qqq-etf/en/
    about.html` with Playwright and watching its network requests -- the page's own HTML
    carries only the client-fill label (`{"fundDetailsLabel":"Shares Outstanding",
    "fundDetailsType":"ShareOutstanding"}`, the survey's finding), never the value.

    The response is one flat JSON object: `{"sharesOutstanding": <int>, "effectiveDate":
    "YYYY-MM-DD", "nav": <float>, ...}` -- full precision, ISO date, NAV included (unlike
    VanEck). **An unrecognized ticker returns the literal two-character body `""`** (still
    HTTP 200) rather than an object or an error -- this is what turns into the per-symbol
    failure below, not an HTTP error status.

    Args:
        body: The raw JSON response text.
        symbol: The plain ticker this request was for, written straight into the returned row.

    Raises:
        ValueError: the body is not JSON, is not an object (the empty-string "ticker not
            recognized" shape), or is missing `sharesOutstanding`/`effectiveDate` -- all named
            per-symbol failures, never a crash. Same "no as-of date, no usable row" rule as
            `parse_vaneck_fund_details`: a missing `effectiveDate` is fatal, not defaulted.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response body is not JSON: {exc}") from exc

    if not isinstance(payload, dict) or not payload:
        raise ValueError(
            "response body is not a fund-details object (ticker not recognized by dng-api?)"
        )

    shares_raw = payload.get("sharesOutstanding")
    as_of_raw = payload.get("effectiveDate")
    if shares_raw is None or not as_of_raw:
        raise ValueError("no 'sharesOutstanding'/'effectiveDate' field in response")

    try:
        shares = int(shares_raw)
        as_of = dt.datetime.strptime(str(as_of_raw).strip(), "%Y-%m-%d").date()  # noqa: DTZ007
    except (TypeError, ValueError) as exc:
        raise ValueError(f"sharesOutstanding/effectiveDate present but unparseable: {exc}") from exc

    if shares <= 0:
        raise ValueError(f"non-positive shares outstanding ({shares!r})")

    nav: float | None = None
    nav_raw = payload.get("nav")
    if nav_raw is not None:
        try:
            nav = float(nav_raw)
        except (TypeError, ValueError):
            nav = None  # a malformed NAV never blocks the shares-outstanding row itself

    return SharesOutstandingRow(
        symbol=symbol,
        as_of_date=as_of,
        shares_outstanding=shares,
        nav=nav,
        source="invesco-shareclass",
    )


class InvescoShareclassProvider(SharesOutstandingProvider):
    """Fetches one `dng-api.invesco.com` shareclass response per requested `INVESCO_SYMBOLS`
    member and parses each with `parse_invesco_shareclass`. Same per-request-per-symbol shape
    as `ISharesProductPageProvider`; unlike VanEck there is no separate id to look up -- the
    ticker is the URL path segment directly.
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
        return "invesco"

    @property
    def symbols(self) -> tuple[str, ...]:
        return INVESCO_SYMBOLS

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": _USER_AGENT, **_INVESCO_REQUIRED_HEADERS},
            )
        return self._client

    async def _fetch_one(self, symbol: str) -> SharesOutstandingRow:
        client = await self._get_client()
        url = _INVESCO_SHARECLASS_URL.format(symbol=symbol)
        params = {"idType": "ticker", "variationType": "fundDetails", "productType": "ETF"}
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(url, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "invesco: attempt %d/%d transport error for %s: %s",
                    attempt, self._max_retries, symbol, exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 200:
                return parse_invesco_shareclass(response.text, symbol)

            last_exc = httpx.HTTPStatusError(
                f"unexpected status {response.status_code}", request=response.request, response=response
            )
            logger.warning(
                "invesco: attempt %d/%d status %d for %s",
                attempt, self._max_retries, response.status_code, symbol,
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * attempt)

        raise UpstreamUnavailable(
            f"invesco: {symbol} request failed after {self._max_retries} attempts"
        ) from last_exc

    async def fetch(self, symbols: Sequence[str] | None = None) -> SharesOutstandingFetchResult:
        wanted = [s for s in (symbols if symbols is not None else self.symbols) if s in self.symbols]

        rows: list[SharesOutstandingRow] = []
        failures: dict[str, str] = {}
        for symbol in wanted:
            try:
                rows.append(await self._fetch_one(symbol))
            except ValueError as exc:
                failures[symbol] = str(exc)
            except ProviderError as exc:
                failures[symbol] = str(exc)

        return SharesOutstandingFetchResult(rows=rows, failures=failures)


# --------------------------------------------------------------------------------------------
# USCF (T59) -- a two-step fetch: mint a bearer token, then call the price/shares endpoint
# --------------------------------------------------------------------------------------------

_USCF_TOKEN_URL = "https://www.uscfinvestments.com/site-template/assets/javascript/api_key.php"
_USCF_DAILYPRICE_URL = "https://secure.alpsinc.com/MarketingAPI/api/v1/dailyprice/{symbol}"

#: Matches `var token = '<jwt>';` in `api_key.php`'s response body -- a plain JS snippet, not
#: JSON (see `_parse_uscf_token`'s docstring for why this is the shape).
_USCF_TOKEN_RE = re.compile(r"var\s+token\s*=\s*'([^']+)'")


def _parse_uscf_token(body: str) -> str:
    """Extract the bearer JWT from `api_key.php`'s response body.

    This endpoint is not JSON -- it is a small JS snippet (`var token = '...'; var
    api_url_v2 = '...'; ...`) meant to be `<script>`-included directly, found by loading
    `https://www.uscfinvestments.com/uso` with Playwright and following the relative script
    path in its `<script src="assets/javascript/api_key.php">` tag (resolved against the
    page's own `<base href="https://www.uscfinvestments.com/site-template/">` -- *not* against
    `www.uscfinvestments.com/assets/...`, which 404s the same request differently and is the
    trap a naive path guess falls into).

    The token is anonymous and unauthenticated in the sense that matters here: every visitor's
    browser gets one from this same public, login-free URL, with no cookie or session required
    -- verified by requesting it with no cookies at all and it still returning a fresh, working
    token. It is short-lived (the JWT's own `exp` claim is `iat + 86400`, one day), which is
    why this is minted fresh on every `fetch()` call rather than cached across them.

    Raises:
        UpstreamUnavailable: no `var token = '...'` assignment found in the body -- the
            endpoint moved or changed shape. This is a family-level failure (see the module
            docstring): without a token, no USCF symbol can be fetched at all this run.
    """
    match = _USCF_TOKEN_RE.search(body)
    if match is None:
        raise UpstreamUnavailable(
            "uscf: no 'var token = ...' assignment found in api_key.php response "
            "(endpoint may have moved)"
        )
    return match.group(1)


def parse_uscf_dailyprice(body: str, symbol: str) -> SharesOutstandingRow:
    """Parse one `secure.alpsinc.com/MarketingAPI/api/v1/dailyprice/{ticker}` response body
    for `symbol`.

    The response is a one-element JSON array (`[{...}]`), not a bare object -- found the same
    way as the other two T59 endpoints, by driving `https://www.uscfinvestments.com/uso` with
    Playwright and watching its network requests. The element carries `so` (shares
    outstanding, full precision, as a JSON float with a `.0000` tail -- `14223603.0000`, not an
    int), `nav`, and `displaydate` (`"YYYY-MM-DDTHH:MM:SS"`, a fixed time-of-day with no
    timezone marker; only the date component is used, per this table's `date`-not-`datetime`
    convention -- see `app.modules.gex.models.db.EtfSharesOutstanding`'s docstring).

    **An unrecognized ticker 404s** (unlike Invesco's 200-with-empty-body shape) with a plain
    text body (`"No resources found for given resource: {ticker}."`) -- the calling provider's
    retry loop treats a non-200 status as a transport-level failure the same way every other
    fetcher here does, so that case never reaches this parser at all.

    Args:
        body: The raw JSON response text (expected to be a one-element array).
        symbol: The plain ticker this request was for, written straight into the returned row.

    Raises:
        ValueError: the body is not JSON, is not a non-empty list, or its first element is
            missing `so`/`displaydate` -- all named per-symbol failures, never a crash. Same
            "no as-of date, no usable row" rule as the other two T59 parsers.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response body is not JSON: {exc}") from exc

    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError("response body is not a non-empty JSON array of records")

    record = payload[0]
    shares_raw = record.get("so")
    as_of_raw = record.get("displaydate")
    if shares_raw is None or not as_of_raw:
        raise ValueError("no 'so'/'displaydate' field in response")

    try:
        shares = round(float(shares_raw))
        # Only the date component is meaningful (see this function's docstring); a calendar
        # date, not an instant -- same rationale as `_parse_spdr_date`.
        as_of = dt.datetime.strptime(str(as_of_raw).strip()[:10], "%Y-%m-%d").date()  # noqa: DTZ007
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'so'/'displaydate' present but unparseable: {exc}") from exc

    if shares <= 0:
        raise ValueError(f"non-positive shares outstanding ({shares!r})")

    nav: float | None = None
    nav_raw = record.get("nav")
    if nav_raw is not None:
        try:
            nav = float(nav_raw)
        except (TypeError, ValueError):
            nav = None

    return SharesOutstandingRow(
        symbol=symbol,
        as_of_date=as_of,
        shares_outstanding=shares,
        nav=nav,
        source="uscf-dailyprice",
    )


class USCFDailyPriceProvider(SharesOutstandingProvider):
    """Fetches a fresh bearer token, then one `dailyprice` response per requested
    `USCF_SYMBOLS` member, parsing each with `parse_uscf_dailyprice`.

    The token fetch is family-level (one token serves every symbol in this `fetch()` call, per
    `_parse_uscf_token`'s docstring), so a token-fetch failure raises `UpstreamUnavailable`
    straight out of `fetch` rather than becoming a per-symbol failure -- consistent with the
    module docstring's "a failed token fetch is a family-level `ProviderError`" note.
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
        return "uscf"

    @property
    def symbols(self) -> tuple[str, ...]:
        return USCF_SYMBOLS

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _USER_AGENT})
        return self._client

    async def _fetch_token(self) -> str:
        client = await self._get_client()
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(_USCF_TOKEN_URL)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "uscf: attempt %d/%d transport error fetching token: %s",
                    attempt, self._max_retries, exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 200:
                return _parse_uscf_token(response.text)

            last_exc = httpx.HTTPStatusError(
                f"unexpected status {response.status_code}", request=response.request, response=response
            )
            logger.warning(
                "uscf: attempt %d/%d status %d fetching token",
                attempt, self._max_retries, response.status_code,
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * attempt)

        raise UpstreamUnavailable(
            f"uscf: token request failed after {self._max_retries} attempts"
        ) from last_exc

    async def _fetch_one(self, symbol: str, token: str) -> SharesOutstandingRow:
        client = await self._get_client()
        url = _USCF_DAILYPRICE_URL.format(symbol=symbol)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.get(url, headers=headers)
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "uscf: attempt %d/%d transport error for %s: %s",
                    attempt, self._max_retries, symbol, exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code == 200:
                return parse_uscf_dailyprice(response.text, symbol)
            if response.status_code == 404:
                # "No resources found for given resource: {symbol}." -- an unrecognized
                # ticker, not a transport failure worth retrying.
                raise ValueError(f"no resource found for {symbol!r} (404)")

            last_exc = httpx.HTTPStatusError(
                f"unexpected status {response.status_code}", request=response.request, response=response
            )
            logger.warning(
                "uscf: attempt %d/%d status %d for %s",
                attempt, self._max_retries, response.status_code, symbol,
            )
            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff_seconds * attempt)

        raise UpstreamUnavailable(
            f"uscf: {symbol} request failed after {self._max_retries} attempts"
        ) from last_exc

    async def fetch(self, symbols: Sequence[str] | None = None) -> SharesOutstandingFetchResult:
        wanted = [s for s in (symbols if symbols is not None else self.symbols) if s in self.symbols]
        if not wanted:
            return SharesOutstandingFetchResult(rows=[], failures={})

        token = await self._fetch_token()  # ProviderError here is family-level, not per-symbol

        rows: list[SharesOutstandingRow] = []
        failures: dict[str, str] = {}
        for symbol in wanted:
            try:
                rows.append(await self._fetch_one(symbol, token))
            except ValueError as exc:
                failures[symbol] = str(exc)
            except ProviderError as exc:
                failures[symbol] = str(exc)

        return SharesOutstandingFetchResult(rows=rows, failures=failures)
