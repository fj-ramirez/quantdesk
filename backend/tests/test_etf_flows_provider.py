"""Tests for `app/providers/etf_flows.py` (T52). Offline: the SPDR/iShares parsers are
exercised directly against the recorded fixtures under `tests/fixtures/etf_flows/` -- never
the live site, per this task's constraint -- and the provider classes are exercised through an
injected `httpx.MockTransport` client, same pattern as `test_yahoo.py`.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest

from app.providers.base import UpstreamUnavailable
from app.providers.etf_flows import (
    ISHARES_SYMBOLS,
    SPDR_SYMBOLS,
    ISharesProductPageProvider,
    SpdrAllFundsProvider,
    parse_ishares_page,
    parse_spdr_xlsx,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "etf_flows"


def _spdr_bytes() -> bytes:
    return (_FIXTURES / "spdr-product-data-us-en-2026-09-08.xlsx").read_bytes()


def _ishares_text(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


# --- parse_spdr_xlsx -----------------------------------------------------------------------


def test_parse_spdr_xlsx_parses_all_17_symbols_from_the_real_fixture():
    result = parse_spdr_xlsx(_spdr_bytes(), SPDR_SYMBOLS)
    assert result.failures == {}
    assert {r.symbol for r in result.rows} == set(SPDR_SYMBOLS)


def test_parse_spdr_xlsx_xlk_matches_the_survey_worked_example():
    """docs/etf-flows-sources.md quotes XLK: NAV $187.88, TNA $122,461.30M, printed shares
    651.81M -- the derived shares figure this test pins is `TNA / NAV`, not the printed one.
    """
    result = parse_spdr_xlsx(_spdr_bytes(), ["XLK"])
    xlk = next(r for r in result.rows if r.symbol == "XLK")
    assert xlk.as_of_date == dt.date(2026, 9, 8)
    assert xlk.nav == pytest.approx(187.88)
    assert xlk.source == "spdr-xlsx"
    # 122,461.30e6 / 187.88 -- derived, not the printed "651.81 M" (~651,810,000).
    assert xlk.shares_outstanding == round(122_461.30e6 / 187.88)
    assert abs(xlk.shares_outstanding - 651_810_000) < 50_000  # within one creation unit


def test_parse_spdr_xlsx_strips_the_trademark_glyph_from_gld_ticker():
    """The survey's documented trap: SPDR's ticker column carries 'GLD®', not plain 'GLD'."""
    result = parse_spdr_xlsx(_spdr_bytes(), ["GLD"])
    assert [r.symbol for r in result.rows] == ["GLD"]


def test_parse_spdr_xlsx_symbol_not_in_file_is_a_named_failure_not_a_crash():
    result = parse_spdr_xlsx(_spdr_bytes(), ["XLK", "NOPEXYZ"])
    assert any(r.symbol == "XLK" for r in result.rows)
    assert result.failures == {"NOPEXYZ": "not found in SPDR all-funds file"}


def test_parse_spdr_xlsx_empty_symbol_request_returns_nothing():
    result = parse_spdr_xlsx(_spdr_bytes(), [])
    assert result.rows == []
    assert result.failures == {}


def test_parse_spdr_xlsx_not_a_zip_raises_upstream_unavailable():
    with pytest.raises(UpstreamUnavailable):
        parse_spdr_xlsx(b"<html>not an xlsx</html>", SPDR_SYMBOLS)


# --- parse_ishares_page ---------------------------------------------------------------------


def test_parse_ishares_page_raw_body_iwm():
    """IWM's fixture serves the sharesOutstanding blob raw (unescaped)."""
    row = parse_ishares_page(_ishares_text("ishares-IWM-product-page-2026-09-09.html"), "IWM")
    assert row.symbol == "IWM"
    assert row.shares_outstanding == 269_850_000
    assert row.as_of_date == dt.date(2026, 9, 9)
    assert row.source == "ishares-productpage"


def test_parse_ishares_page_html_escaped_body_tlt():
    """TLT's fixture serves the identical blob HTML-entity-escaped (`&quot;` etc.) -- the
    survey's documented "IWM raw, TLT escaped, same afternoon" trap."""
    row = parse_ishares_page(_ishares_text("ishares-TLT-product-page-2026-09-09.html"), "TLT")
    assert row.symbol == "TLT"
    assert row.shares_outstanding == 570_300_000
    assert row.as_of_date == dt.date(2026, 9, 8)


def test_parse_ishares_page_missing_shares_outstanding_key_raises_value_error():
    """The survey's "signature of a moved page" -- a 200 whose body has no sharesOutstanding
    field must be a named, catchable failure (ValueError), not an unhandled crash."""
    with pytest.raises(ValueError, match="sharesOutstanding"):
        parse_ishares_page("<html><body>no data here</body></html>", "IWM")


def test_parse_ishares_page_no_nav_field_in_fixture_is_none_not_a_crash():
    """Neither trimmed fixture carries a navAmount block -- nav must come back None, never
    raise, matching the ABC's `nav | None` contract."""
    row = parse_ishares_page(_ishares_text("ishares-IWM-product-page-2026-09-09.html"), "IWM")
    assert row.nav is None


# --- SpdrAllFundsProvider (httpx.MockTransport) ---------------------------------------------


@pytest.fixture
def spdr_client_factory():
    def _make(handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return _make


async def test_spdr_provider_fetch_success(spdr_client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_spdr_bytes())

    provider = SpdrAllFundsProvider(client=spdr_client_factory(handler))
    result = await provider.fetch(["XLK", "SPY"])

    assert {r.symbol for r in result.rows} == {"XLK", "SPY"}
    assert result.failures == {}
    await provider.close()


async def test_spdr_provider_defaults_to_every_spdr_symbol(spdr_client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_spdr_bytes())

    provider = SpdrAllFundsProvider(client=spdr_client_factory(handler))
    result = await provider.fetch()

    assert {r.symbol for r in result.rows} == set(SPDR_SYMBOLS)


async def test_spdr_provider_ignores_a_symbol_outside_its_family(spdr_client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_spdr_bytes())

    provider = SpdrAllFundsProvider(client=spdr_client_factory(handler))
    result = await provider.fetch(["IWM"])  # an iShares symbol, not a SPDR one

    assert result.rows == []
    assert result.failures == {}


async def test_spdr_provider_non_200_raises_upstream_unavailable_after_retries(spdr_client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    provider = SpdrAllFundsProvider(
        client=spdr_client_factory(handler), max_retries=2, backoff_seconds=0.0
    )
    with pytest.raises(UpstreamUnavailable):
        await provider.fetch(["XLK"])


# --- ISharesProductPageProvider (httpx.MockTransport) ----------------------------------------


async def test_ishares_provider_fetch_success_both_fixtures():
    from app.providers.etf_flows import ISHARES_PRODUCT_URLS

    fixtures = {
        "IWM": _ishares_text("ishares-IWM-product-page-2026-09-09.html"),
        "TLT": _ishares_text("ishares-TLT-product-page-2026-09-09.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        for symbol, url in ISHARES_PRODUCT_URLS.items():
            if str(request.url) == url and symbol in fixtures:
                return httpx.Response(200, text=fixtures[symbol])
        return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ISharesProductPageProvider(client=client)
    result = await provider.fetch(["IWM", "TLT"])

    assert {r.symbol for r in result.rows} == {"IWM", "TLT"}
    assert result.failures == {}
    await provider.close()


async def test_ishares_provider_one_symbol_failing_does_not_stop_the_others():
    from app.providers.etf_flows import ISHARES_PRODUCT_URLS

    tlt_body = _ishares_text("ishares-TLT-product-page-2026-09-09.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ISHARES_PRODUCT_URLS["TLT"]:
            return httpx.Response(200, text=tlt_body)
        if str(request.url) == ISHARES_PRODUCT_URLS["IWM"]:
            return httpx.Response(200, text="<html>moved page, no data</html>")
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ISharesProductPageProvider(client=client)
    result = await provider.fetch(["IWM", "TLT"])

    assert {r.symbol for r in result.rows} == {"TLT"}
    assert "IWM" in result.failures
    assert set(ISHARES_SYMBOLS) & {"IWM", "TLT"} == {"IWM", "TLT"}


async def test_ishares_provider_ignores_a_symbol_outside_its_family():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("should never fetch a non-iShares symbol")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ISharesProductPageProvider(client=client)
    result = await provider.fetch(["XLK"])  # a SPDR symbol, not an iShares one

    assert result.rows == []
    assert result.failures == {}
