"""Tests for `app/providers/etf_flows.py` (T52; T59 adds VanEck/Invesco/USCF). Offline: every
parser is exercised directly against the recorded fixtures under `tests/fixtures/etf_flows/`
-- never the live site, per this task's constraint -- and the provider classes are exercised
through an injected `httpx.MockTransport` client, same pattern as `test_yahoo.py`.
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
    InvescoShareclassProvider,
    ISharesProductPageProvider,
    SpdrAllFundsProvider,
    USCFDailyPriceProvider,
    VanEckFundDetailsProvider,
    parse_invesco_shareclass,
    parse_ishares_page,
    parse_spdr_xlsx,
    parse_uscf_dailyprice,
    parse_vaneck_fund_details,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "etf_flows"


def _spdr_bytes() -> bytes:
    return (_FIXTURES / "spdr-product-data-us-en-2026-09-08.xlsx").read_bytes()


def _ishares_text(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _fixture_text(name: str) -> str:
    """Generic recorded-fixture reader for the T59 families -- same job as `_ishares_text`,
    named without the family prefix since it is shared by VanEck/Invesco/USCF fixtures below.
    """
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


# --- parse_vaneck_fund_details (T59) ---------------------------------------------------------


def test_parse_vaneck_fund_details_smh_matches_the_live_fixture():
    row = parse_vaneck_fund_details(
        _fixture_text("vaneck-SMH-funddetails-2026-09-10.json"), "SMH"
    )
    assert row.symbol == "SMH"
    assert row.shares_outstanding == 123_891_874
    assert row.as_of_date == dt.date(2026, 9, 9)
    assert row.nav is None  # no NAV field in this block -- see the parser's own docstring
    assert row.source == "vaneck-funddetails"


def test_parse_vaneck_fund_details_gdx_matches_the_live_fixture():
    row = parse_vaneck_fund_details(
        _fixture_text("vaneck-GDX-funddetails-2026-09-10.json"), "GDX"
    )
    assert row.symbol == "GDX"
    assert row.shares_outstanding == 301_152_500
    assert row.as_of_date == dt.date(2026, 9, 9)


def test_parse_vaneck_fund_details_missing_shares_outstanding_is_a_named_failure():
    with pytest.raises(ValueError, match="Shares Outstanding"):
        parse_vaneck_fund_details('{"data": {"LongVersionAsOfDate": "09/09/2026", "Values": []}}', "SMH")


def test_parse_vaneck_fund_details_missing_as_of_date_is_a_named_failure():
    """No `LongVersionAsOfDate` at all must fail rather than default to a wrong date -- the
    as-of-date rule this task's brief restates from T52's survey."""
    body = (
        '{"data": {"Values": [{"Title": "Shares Outstanding", "Value": "1,000,000"}]}}'
    )
    with pytest.raises(ValueError, match="AsOfDate"):
        parse_vaneck_fund_details(body, "SMH")


def test_parse_vaneck_fund_details_not_json_is_a_named_failure():
    with pytest.raises(ValueError):
        parse_vaneck_fund_details("<html>not json</html>", "SMH")


# --- VanEckFundDetailsProvider (httpx.MockTransport) -----------------------------------------


async def test_vaneck_provider_fetch_success_both_funds():
    from app.providers.etf_flows import VANECK_PAGE_IDS

    fixtures = {
        "SMH": _fixture_text("vaneck-SMH-funddetails-2026-09-10.json"),
        "GDX": _fixture_text("vaneck-GDX-funddetails-2026-09-10.json"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        ticker = request.url.params.get("ticker")
        pageid = request.url.params.get("pageid")
        if ticker in fixtures and pageid == VANECK_PAGE_IDS.get(ticker):
            return httpx.Response(200, text=fixtures[ticker])
        return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = VanEckFundDetailsProvider(client=client)
    result = await provider.fetch(["SMH", "GDX"])

    assert {r.symbol for r in result.rows} == {"SMH", "GDX"}
    assert result.failures == {}
    await provider.close()


async def test_vaneck_provider_one_symbol_failing_does_not_stop_the_other():
    smh_body = _fixture_text("vaneck-SMH-funddetails-2026-09-10.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("ticker") == "SMH":
            return httpx.Response(200, text=smh_body)
        return httpx.Response(200, text='{"data": {"Values": []}}')  # GDX: moved page

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = VanEckFundDetailsProvider(client=client)
    result = await provider.fetch(["SMH", "GDX"])

    assert {r.symbol for r in result.rows} == {"SMH"}
    assert "GDX" in result.failures


async def test_vaneck_provider_ignores_a_symbol_outside_its_family():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("should never fetch a non-VanEck symbol")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = VanEckFundDetailsProvider(client=client)
    result = await provider.fetch(["XLK"])

    assert result.rows == []
    assert result.failures == {}


async def test_vaneck_provider_non_200_is_a_named_per_symbol_failure_after_retries():
    """Same per-request-per-symbol shape as `ISharesProductPageProvider`: a family with no
    single all-funds file has no "the whole family's request failed" case distinct from "this
    one symbol's request failed" -- so a non-200 becomes a `failures` entry, not a raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = VanEckFundDetailsProvider(client=client, max_retries=2, backoff_seconds=0.0)
    result = await provider.fetch(["SMH"])

    assert result.rows == []
    assert "SMH" in result.failures


# --- parse_invesco_shareclass (T59) -----------------------------------------------------------


def test_parse_invesco_shareclass_qqq_matches_the_live_fixture():
    row = parse_invesco_shareclass(
        _fixture_text("invesco-QQQ-shareclass-2026-09-10.json"), "QQQ"
    )
    assert row.symbol == "QQQ"
    assert row.shares_outstanding == 672_600_000
    assert row.as_of_date == dt.date(2026, 9, 9)
    assert row.nav == pytest.approx(716.185355)
    assert row.source == "invesco-shareclass"


def test_parse_invesco_shareclass_unrecognized_ticker_empty_body_is_a_named_failure():
    """An unrecognized ticker returns the literal two-character body `""` (still HTTP 200,
    per the survey's live probe) -- not an object, not an error status."""
    with pytest.raises(ValueError, match="not a fund-details object"):
        parse_invesco_shareclass('""', "NOPE")


def test_parse_invesco_shareclass_missing_shares_outstanding_is_a_named_failure():
    with pytest.raises(ValueError, match="sharesOutstanding"):
        parse_invesco_shareclass('{"effectiveDate": "2026-09-09"}', "QQQ")


def test_parse_invesco_shareclass_missing_as_of_date_is_a_named_failure():
    with pytest.raises(ValueError, match="sharesOutstanding"):
        parse_invesco_shareclass('{"sharesOutstanding": 1000000}', "QQQ")


def test_parse_invesco_shareclass_not_json_is_a_named_failure():
    with pytest.raises(ValueError):
        parse_invesco_shareclass("not json", "QQQ")


# --- InvescoShareclassProvider (httpx.MockTransport) -------------------------------------------


async def test_invesco_provider_fetch_success():
    body = _fixture_text("invesco-QQQ-shareclass-2026-09-10.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = InvescoShareclassProvider(client=client)
    result = await provider.fetch(["QQQ"])

    assert {r.symbol for r in result.rows} == {"QQQ"}
    assert result.failures == {}
    await provider.close()


async def test_invesco_provider_empty_body_is_a_named_per_symbol_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='""')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = InvescoShareclassProvider(client=client)
    result = await provider.fetch(["QQQ"])

    assert result.rows == []
    assert "QQQ" in result.failures


async def test_invesco_provider_ignores_a_symbol_outside_its_family():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("should never fetch a non-Invesco symbol")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = InvescoShareclassProvider(client=client)
    result = await provider.fetch(["XLK"])

    assert result.rows == []
    assert result.failures == {}


async def test_invesco_provider_non_200_is_a_named_per_symbol_failure_after_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = InvescoShareclassProvider(client=client, max_retries=2, backoff_seconds=0.0)
    result = await provider.fetch(["QQQ"])

    assert result.rows == []
    assert "QQQ" in result.failures


# --- parse_uscf_dailyprice (T59) --------------------------------------------------------------


def test_parse_uscf_dailyprice_uso_matches_the_live_fixture():
    row = parse_uscf_dailyprice(_fixture_text("uscf-USO-dailyprice-2026-09-10.json"), "USO")
    assert row.symbol == "USO"
    assert row.shares_outstanding == 14_223_603
    assert row.as_of_date == dt.date(2026, 9, 9)
    assert row.nav == pytest.approx(149.17)
    assert row.source == "uscf-dailyprice"


def test_parse_uscf_dailyprice_missing_so_field_is_a_named_failure():
    with pytest.raises(ValueError, match="'so'"):
        parse_uscf_dailyprice('[{"displaydate": "2026-09-09T05:00:00"}]', "USO")


def test_parse_uscf_dailyprice_empty_array_is_a_named_failure():
    with pytest.raises(ValueError, match="non-empty"):
        parse_uscf_dailyprice("[]", "USO")


def test_parse_uscf_dailyprice_not_json_is_a_named_failure():
    with pytest.raises(ValueError):
        parse_uscf_dailyprice("not json", "USO")


# --- USCFDailyPriceProvider (httpx.MockTransport) ---------------------------------------------


def _uscf_token_body(token: str = "faketoken123") -> str:
    return (
        f"var token = '{token}';var api_url_v2 = "
        "'https://secure.alpsinc.com/MarketingAPI/api/v1/';"
        "var api_domain = 'www.uscfinvestments.com';"
    )


async def test_uscf_provider_fetch_success():
    dailyprice_body = _fixture_text("uscf-USO-dailyprice-2026-09-10.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if "api_key.php" in str(request.url):
            return httpx.Response(200, text=_uscf_token_body())
        assert request.headers.get("authorization") == "Bearer faketoken123"
        return httpx.Response(200, text=dailyprice_body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = USCFDailyPriceProvider(client=client)
    result = await provider.fetch(["USO"])

    assert {r.symbol for r in result.rows} == {"USO"}
    assert result.failures == {}
    await provider.close()


async def test_uscf_provider_token_fetch_failure_is_family_level_not_per_symbol():
    """No `var token = ...` in the api_key.php body -- the endpoint moved. This must raise
    (family-level), never surface as a per-symbol failure entry."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "api_key.php" in str(request.url):
            return httpx.Response(200, text="// token endpoint moved, no token here")
        raise AssertionError("should never reach dailyprice without a token")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = USCFDailyPriceProvider(client=client)
    with pytest.raises(UpstreamUnavailable):
        await provider.fetch(["USO"])


async def test_uscf_provider_404_on_dailyprice_is_a_named_per_symbol_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if "api_key.php" in str(request.url):
            return httpx.Response(200, text=_uscf_token_body())
        return httpx.Response(404, text="No resources found for given resource: USO.")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = USCFDailyPriceProvider(client=client)
    result = await provider.fetch(["USO"])

    assert result.rows == []
    assert "USO" in result.failures


async def test_uscf_provider_ignores_a_symbol_outside_its_family():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("should never fetch a non-USCF symbol")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = USCFDailyPriceProvider(client=client)
    result = await provider.fetch(["XLK"])

    assert result.rows == []
    assert result.failures == {}
