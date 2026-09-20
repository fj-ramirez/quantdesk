"""Tests for `app.modules.gex.providers.cboe_index.CboeIndexHistoryProvider` (T54,
plans/continuation/06-cross-asset-regime.md).

Entirely offline: every HTTP call goes through `httpx.MockTransport` serving the recorded
fixtures under `tests/fixtures/cboe_index/` (real Cboe index-history CSVs, trimmed -- see that
directory's own `README.md`), never the real network. Same pattern `test_cboe.py` already uses
for the delayed-quotes provider.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.modules.gex.providers.bars import (
    BarProviderRegistry,
    SymbolNotSupported,
    UpstreamUnavailable,
)
from app.modules.gex.providers.cboe_index import CboeIndexHistoryProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cboe_index"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _provider_for_fixture(name: str, **kwargs: Any) -> tuple[CboeIndexHistoryProvider, httpx.AsyncClient]:
    payload = _load_fixture(name)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CboeIndexHistoryProvider(client=client, **kwargs), client


async def _fetch(symbol: str, fixture: str, *, start: dt.date, end: dt.date | None = None, **kwargs: Any):
    provider, client = _provider_for_fixture(fixture, **kwargs)
    try:
        return await provider.fetch_daily_bars(symbol, start=start, end=end)
    finally:
        await client.aclose()


async def test_vix_fixture_parses_ohlc_and_keeps_the_1990_rows():
    """The VIX fixture deliberately keeps its 1990 rows (Cboe's own "closes dressed as OHLC"
    precedent -- see the provider module docstring's close-only-schema decision) alongside its
    2026 rows."""
    bars = await _fetch("^VIX", "VIX_History-2026-09-09.csv", start=dt.date(1990, 1, 1))
    assert bars[0].date == dt.date(1990, 1, 2)
    assert bars[0].open == pytest.approx(17.24)
    assert bars[0].close == pytest.approx(17.24)
    assert bars[-1].date == dt.date(2026, 9, 9)
    assert bars[-1].close == pytest.approx(16.46)
    assert bars[-1].open == pytest.approx(15.65)
    assert bars[-1].high == pytest.approx(16.68)
    assert bars[-1].low == pytest.approx(15.57)
    assert all(b.symbol == "^VIX" for b in bars)
    assert all(b.source == "cboe_index" for b in bars)
    assert all(b.volume is None for b in bars)
    # ascending by date
    assert [b.date for b in bars] == sorted(b.date for b in bars)


async def test_vix3m_fixture_parses_ohlc():
    bars = await _fetch("^VIX3M", "VIX3M_History-2026-09-09.csv", start=dt.date(1990, 1, 1))
    assert bars
    assert bars[0].date == dt.date(2026, 6, 15)
    assert bars[0].open == pytest.approx(19.61)
    assert bars[0].high == pytest.approx(19.79)
    assert bars[0].low == pytest.approx(19.30)
    assert bars[0].close == pytest.approx(19.36)


async def test_vvix_fixture_is_close_only_stored_as_flat_ohlc():
    """VVIX publishes a close only -- this provider stores `open = high = low = close`
    (the module docstring's close-only-schema decision), never a fabricated intraday range."""
    bars = await _fetch("^VVIX", "VVIX_History-2026-09-09.csv", start=dt.date(1990, 1, 1))
    assert bars
    for b in bars:
        assert b.open == b.high == b.low == b.close
    first = bars[0]
    assert first.date == dt.date(2006, 3, 6)
    assert first.close == pytest.approx(71.73)


async def test_vvix_fixtures_sparse_early_history_is_preserved_not_filled():
    """VVIX's early history genuinely skips days (03/06/2006 then 03/15/2006, kept deliberately
    in the fixture) -- the provider must not fabricate the missing days."""
    bars = await _fetch(
        "^VVIX", "VVIX_History-2026-09-09.csv", start=dt.date(2006, 1, 1), end=dt.date(2006, 4, 1)
    )
    dates = [b.date for b in bars]
    assert dates == [dt.date(2006, 3, 6), dt.date(2006, 3, 15), dt.date(2006, 3, 16)]


async def test_skew_fixture_is_close_only_stored_as_flat_ohlc():
    bars = await _fetch("^SKEW", "SKEW_History-2026-09-09.csv", start=dt.date(1990, 1, 1))
    assert bars
    for b in bars:
        assert b.open == b.high == b.low == b.close
    assert bars[0].close == pytest.approx(136.54)


async def test_start_end_filtering():
    bars = await _fetch(
        "^VIX",
        "VIX_History-2026-09-09.csv",
        start=dt.date(2026, 9, 3),
        end=dt.date(2026, 9, 7),
    )
    assert [b.date for b in bars] == [dt.date(2026, 9, 3), dt.date(2026, 9, 4), dt.date(2026, 9, 7)]


async def test_no_end_means_no_upper_bound():
    bars = await _fetch("^VIX", "VIX_History-2026-09-09.csv", start=dt.date(2026, 9, 8))
    assert [b.date for b in bars] == [dt.date(2026, 9, 8), dt.date(2026, 9, 9)]


async def test_html_body_with_status_200_raises_upstream_unavailable():
    """Cboe, like its own delayed-quotes options endpoint, can serve an HTML error/denial page
    under a 200 -- see `not-a-csv-error-body.html`'s own recorded shape."""
    with pytest.raises(UpstreamUnavailable):
        await _fetch(
            "^VIX", "not-a-csv-error-body.html", start=dt.date(1990, 1, 1), max_retries=1
        )


async def test_unsupported_symbol_raises_without_network():
    """Validation happens before any request is built -- no client/transport is needed, same
    contract as `app.modules.gex.providers.cboe.CboeProvider`'s own unsupported-symbol path."""
    provider = CboeIndexHistoryProvider()
    with pytest.raises(SymbolNotSupported):
        await provider.fetch_daily_bars("^ZZZZ", start=dt.date(2020, 1, 1))


async def test_header_found_after_a_preamble_line():
    """The plan's own named first-contact failure: a header not on line 1. This provider
    searches for it rather than assuming a fixed offset."""
    csv_body = (
        b"Cboe Global Markets Volatility Index History\n"
        b"DATE,OPEN,HIGH,LOW,CLOSE\n"
        b"09/09/2026,15.65,16.68,15.57,16.46\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=csv_body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeIndexHistoryProvider(client=client)
    try:
        bars = await provider.fetch_daily_bars("^VIX", start=dt.date(2026, 1, 1))
    finally:
        await client.aclose()
    assert len(bars) == 1
    assert bars[0].close == pytest.approx(16.46)


async def test_bad_row_is_skipped_not_fatal(caplog):
    csv_body = (
        b"DATE,OPEN,HIGH,LOW,CLOSE\n"
        b"09/08/2026,15.56,15.94,15.22,15.72\n"
        b"not-a-date,x,x,x,x\n"
        b"09/09/2026,15.65,16.68,15.57,16.46\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=csv_body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeIndexHistoryProvider(client=client)
    try:
        bars = await provider.fetch_daily_bars("^VIX", start=dt.date(2026, 1, 1))
    finally:
        await client.aclose()
    assert [b.date for b in bars] == [dt.date(2026, 9, 8), dt.date(2026, 9, 9)]


async def test_retries_on_transport_error_then_succeeds():
    calls = {"n": 0}
    payload = _load_fixture("VIX_History-2026-09-09.csv")

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeIndexHistoryProvider(client=client, backoff_seconds=0.0)
    try:
        bars = await provider.fetch_daily_bars("^VIX", start=dt.date(2026, 9, 9))
    finally:
        await client.aclose()
    assert calls["n"] == 3
    assert bars


async def test_retries_exhausted_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeIndexHistoryProvider(client=client, max_retries=2, backoff_seconds=0.0)
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_daily_bars("^VIX", start=dt.date(2026, 1, 1))
    finally:
        await client.aclose()


def test_name_is_cboe_index():
    assert CboeIndexHistoryProvider().name == "cboe_index"


async def test_registry_routes_configured_symbols_to_this_provider():
    """T54's `BAR_PROVIDER_GROUPS` default routes the six index symbols here -- see
    `app.core.config.settings.BAR_PROVIDER_GROUPS` and `app.modules.gex.providers.bars`'s group-beats-default
    precedence."""
    registry = BarProviderRegistry(
        default="yahoo", groups="cboe_index:^VIX,^VIX9D,^VIX3M,^VIX6M,^VVIX,^SKEW"
    )
    for symbol in ("^VIX", "^VIX9D", "^VIX3M", "^VIX6M", "^VVIX", "^SKEW"):
        assert registry.provider_name_for(symbol) == "cboe_index"
        assert isinstance(registry.for_symbol(symbol), CboeIndexHistoryProvider)
    assert registry.provider_name_for("SPY") == "yahoo"  # unaffected
