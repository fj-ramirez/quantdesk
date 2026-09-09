"""Tests for the MarketData.app fallback provider.

Entirely offline: every HTTP call goes through ``httpx.MockTransport`` serving fixtures under
``tests/fixtures/marketdata/``. Unlike ``tests/fixtures/cboe/`` (trimmed *real* responses), these
fixtures are **hand-built from MarketData.app's published documentation** — there is no
MarketData.app account or token available to record real traffic against. See the top of
``backend/app/providers/marketdata.py`` for the doc URLs and the exact provenance of every
mapping decision these tests lock in. This test suite proves the provider parses the *documented*
shape correctly; it does not, and cannot, prove the provider matches what the live API actually
returns.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.providers.base import SymbolNotSupported, Underlying, UpstreamUnavailable
from app.providers.marketdata import MarketDataProvider, MissingCredential

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "marketdata"

_TOKEN = "test-token-123"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _load_fixture_json(name: str) -> dict[str, Any]:
    return json.loads(_load_fixture(name))


def _provider_for_fixture(
    name: str, **kwargs: Any
) -> tuple[MarketDataProvider, httpx.AsyncClient]:
    """Build a provider whose client always serves the given fixture bytes."""
    payload = _load_fixture(name)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs.setdefault("token", _TOKEN)
    return MarketDataProvider(client=client, **kwargs), client


async def _fetch(symbol: str, fixture: str, **kwargs: Any):
    provider, client = _provider_for_fixture(fixture, **kwargs)
    try:
        return await provider.fetch_chain(symbol)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("symbol", "fixture", "expected_count", "expected_min_expiries"),
    [
        ("SPX", "spx_synthetic.json", 6, 3),
        ("SPY", "spy_synthetic.json", 3, 2),
        ("QQQ", "qqq_synthetic.json", 3, 2),
    ],
)
async def test_fixture_contract_count(symbol, fixture, expected_count, expected_min_expiries):
    snapshot = await _fetch(symbol, fixture)
    assert len(snapshot) == expected_count
    assert snapshot.underlying == Underlying[symbol]
    assert len(snapshot.expiries) >= expected_min_expiries
    assert snapshot.source == "marketdata"
    assert snapshot.delayed_minutes == 24 * 60


async def test_spx_fixture_has_both_am_and_pm_roots():
    """Same AM/PM merge behavior as Cboe: SPX (AM) and SPXW (PM) share a strike/expiry."""
    snapshot = await _fetch("SPX", "spx_synthetic.json")
    assert set(snapshot.roots) == {"SPX", "SPXW"}
    settlements = {c.settlement for c in snapshot.contracts}
    assert {s.value for s in settlements} == {"AM", "PM"}


async def test_known_contract_maps_fields_correctly():
    snapshot = await _fetch("SPX", "spx_synthetic.json")
    contract = next(c for c in snapshot.contracts if c.occ_symbol == "SPX260918C06700000")

    assert contract.root == "SPX"
    assert contract.underlying == Underlying.SPX
    assert contract.settlement.value == "AM"
    assert contract.expiry == dt.date(2026, 9, 18)
    assert contract.strike == 6700.0
    assert contract.right.value == "C"
    assert contract.bid == pytest.approx(145.20)
    assert contract.ask == pytest.approx(146.10)
    assert contract.last == pytest.approx(145.80)
    assert contract.volume == 3200
    assert contract.open_interest == 12500
    assert contract.iv == pytest.approx(0.1180)
    assert contract.delta == pytest.approx(0.55)
    assert contract.gamma == pytest.approx(0.0021)
    assert contract.vega == pytest.approx(8.20)
    assert contract.theta == pytest.approx(-3.10)
    assert contract.multiplier == 100


async def test_iv_zero_sentinel_becomes_none():
    """SPX260918P06700000 is fixtured with iv: 0.0 — same sentinel policy as Cboe."""
    raw = _load_fixture_json("spx_synthetic.json")
    idx = raw["optionSymbol"].index("SPX260918P06700000")
    assert raw["iv"][idx] == 0.0  # sanity: fixture still carries the sentinel case

    snapshot = await _fetch("SPX", "spx_synthetic.json")
    contract = next(c for c in snapshot.contracts if c.occ_symbol == "SPX260918P06700000")
    assert contract.iv is None


async def test_open_interest_null_is_unknown_zero_is_genuine():
    """SPXW261016C06800000 has openInterest: null; SPXW261016P06800000 has openInterest: 0.

    None must mean unknown, not fabricated as zero; 0 must survive as a genuine measurement.
    """
    snapshot = await _fetch("SPX", "spx_synthetic.json")
    unknown = next(c for c in snapshot.contracts if c.occ_symbol == "SPXW261016C06800000")
    zero = next(c for c in snapshot.contracts if c.occ_symbol == "SPXW261016P06800000")
    assert unknown.open_interest is None
    assert zero.open_interest == 0


async def test_volume_null_maps_to_none():
    snapshot = await _fetch("SPX", "spx_synthetic.json")
    contract = next(c for c in snapshot.contracts if c.occ_symbol == "SPXW261016P06800000")
    assert contract.volume is None


async def test_last_trade_time_always_none():
    """The documented response has no per-contract trade timestamp, only a quote 'updated'."""
    snapshot = await _fetch("SPX", "spx_synthetic.json")
    assert all(c.last_trade_time is None for c in snapshot.contracts)


async def test_captured_at_is_max_of_updated_column():
    raw = _load_fixture_json("spx_synthetic.json")
    snapshot = await _fetch("SPX", "spx_synthetic.json")
    assert snapshot.captured_at.tzinfo is not None
    assert snapshot.captured_at.utcoffset() == dt.timedelta(0)
    expected = dt.datetime.fromtimestamp(max(raw["updated"]), tz=dt.UTC)
    assert snapshot.captured_at == expected


async def test_spot_is_first_underlying_price():
    raw = _load_fixture_json("spx_synthetic.json")
    snapshot = await _fetch("SPX", "spx_synthetic.json")
    assert snapshot.spot == pytest.approx(raw["underlyingPrice"][0])


async def test_unsupported_symbol_raises_without_network():
    """Validation happens before any request is built, so no client/transport is needed.

    T47 note: this used to use "IWM" as the example unsupported symbol -- it was true when
    written, but T47 added IWM as a real `Underlying` member, so it stopped exercising this
    path. "ZZZZ" is not, and never will be, a symbol this application tracks."""
    provider = MarketDataProvider(token=_TOKEN)
    with pytest.raises(SymbolNotSupported):
        await provider.fetch_chain("ZZZZ")


async def test_missing_token_raises_before_any_network_call():
    """A missing MARKETDATA_TOKEN must fail loudly and legibly, not as a blank-header 401."""
    with pytest.raises(MissingCredential, match="MARKETDATA_TOKEN"):
        MarketDataProvider(token="")


async def test_authorization_header_uses_bearer_token():
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, content=_load_fixture("spy_synthetic.json"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token="secret-abc", client=client)
    try:
        await provider.fetch_chain("SPY")
    finally:
        await client.aclose()

    assert seen["request"].headers["Authorization"] == "Bearer secret-abc"


async def test_request_uses_expiration_all_and_no_mode_by_default():
    """expiration=all avoids the vendor's next-monthly-only default.

    `mode` is deliberately absent: a Free Forever token cannot set it and answers
    `mode=cached` with a 402, which would make this fallback provider fail on the only plan
    it is configured for (T06 review).
    """
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, content=_load_fixture("spy_synthetic.json"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token=_TOKEN, client=client)
    try:
        await provider.fetch_chain("SPY")
    finally:
        await client.aclose()

    params = seen["request"].url.params
    assert params["expiration"] == "all"
    assert "mode" not in params
    assert seen["request"].url.path == "/v1/options/chain/SPY/"


async def test_mode_is_sent_when_explicitly_configured():
    """A paid-plan caller can still ask for the 1-credit cached chain."""
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, content=_load_fixture("spy_synthetic.json"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token=_TOKEN, client=client, mode="cached")
    try:
        await provider.fetch_chain("SPY")
    finally:
        await client.aclose()

    assert seen["request"].url.params["mode"] == "cached"


async def test_unknown_root_contract_is_skipped_not_fatal(caplog):
    """An unmapped root must not blank out the whole snapshot — same policy as Cboe."""
    payload = {
        "s": "ok",
        "optionSymbol": ["SPY260904C00500000", "ZZZ260904C00500000"],
        "underlying": ["SPY", "ZZZ"],
        "strike": [500, 500],
        "bid": [1.0, 1.0],
        "ask": [1.1, 1.1],
        "last": [1.05, 1.05],
        "openInterest": [10, 10],
        "volume": [5, 5],
        "iv": [0.2, 0.2],
        "delta": [0.5, 0.5],
        "gamma": [0.01, 0.01],
        "theta": [-0.05, -0.05],
        "vega": [0.1, 0.1],
        "underlyingPrice": [500.0, 500.0],
        "updated": [1788545100, 1788545100],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token=_TOKEN, client=client)
    try:
        with caplog.at_level(logging.WARNING):
            snapshot = await provider.fetch_chain("SPY")
    finally:
        await client.aclose()

    assert len(snapshot) == 1
    assert snapshot.contracts[0].occ_symbol == "SPY260904C00500000"
    assert any("ZZZ260904C00500000" in record.message for record in caplog.records)


async def test_vendor_error_status_raises_upstream_unavailable():
    payload = {"s": "error", "errmsg": "plan does not support mode=cached"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token=_TOKEN, client=client)
    try:
        with pytest.raises(UpstreamUnavailable, match="plan does not support"):
            await provider.fetch_chain("SPY")
    finally:
        await client.aclose()


async def test_no_data_status_raises_upstream_unavailable():
    payload = {"s": "no_data"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token=_TOKEN, client=client)
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_chain("SPY")
    finally:
        await client.aclose()


async def test_retries_on_transport_error_then_succeeds():
    calls = {"n": 0}
    payload = _load_fixture("spy_synthetic.json")

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token=_TOKEN, client=client, backoff_seconds=0.0)
    try:
        snapshot = await provider.fetch_chain("SPY")
    finally:
        await client.aclose()

    assert calls["n"] == 3
    assert snapshot.underlying == Underlying.SPY


async def test_retries_exhausted_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(
        token=_TOKEN, client=client, max_retries=2, backoff_seconds=0.0
    )
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_chain("SPY")
    finally:
        await client.aclose()


# --- T06 review regressions ---------------------------------------------------------------
# The same three failure modes fixed in the Cboe provider, asserted here so the two providers
# stay behaviourally identical for an operator: every failure is a ProviderError.


async def test_html_body_with_status_200_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>502 Bad Gateway</html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(
        token=_TOKEN, client=client, max_retries=2, backoff_seconds=0.0
    )
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_chain("SPY")
    finally:
        await client.aclose()


async def test_empty_updated_column_raises_upstream_unavailable():
    """`max(updated)` used to raise a bare ValueError out of the provider."""
    payload = {
        "s": "ok",
        "optionSymbol": ["SPY260904C00500000"],
        "underlyingPrice": [500.0],
        "updated": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token=_TOKEN, client=client)
    try:
        with pytest.raises(UpstreamUnavailable, match="unusable"):
            await provider.fetch_chain("SPY")
    finally:
        await client.aclose()


async def test_chain_with_no_usable_contracts_raises_instead_of_an_empty_snapshot():
    payload = {
        "s": "ok",
        "optionSymbol": ["ZZZ260904C00500000"],
        "underlyingPrice": [500.0],
        "updated": [1788545100],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = MarketDataProvider(token=_TOKEN, client=client)
    try:
        with pytest.raises(UpstreamUnavailable, match="no usable contracts"):
            await provider.fetch_chain("SPY")
    finally:
        await client.aclose()
