"""Tests for the Cboe delayed-quotes provider.

Entirely offline: every HTTP call goes through ``httpx.MockTransport`` serving the recorded
fixtures under ``tests/fixtures/cboe/``, never the real network. The fixtures are trimmed real
responses (2026-09-04) — see ``backend/app/providers/cboe.py`` for the vendor quirks these
tests lock in: mixed timezones, the ``iv: 0.0`` sentinel, and genuine ``gamma: 0.0`` values.
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
from app.providers.cboe import CboeProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cboe"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _load_fixture_json(name: str) -> dict[str, Any]:
    return json.loads(_load_fixture(name))


def _provider_for_fixture(name: str, **kwargs: Any) -> tuple[CboeProvider, httpx.AsyncClient]:
    """Build a provider whose client always serves the given fixture bytes."""
    payload = _load_fixture(name)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CboeProvider(client=client, **kwargs), client


async def _fetch(symbol: str, fixture: str, **kwargs: Any):
    provider, client = _provider_for_fixture(fixture, **kwargs)
    try:
        return await provider.fetch_chain(symbol)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("symbol", "fixture", "expected_count", "expected_min_expiries"),
    [
        ("SPX", "spx.json", 246, 3),
        ("SPY", "spy.json", 111, 3),
        ("QQQ", "qqq.json", 111, 3),
    ],
)
async def test_fixture_contract_count(symbol, fixture, expected_count, expected_min_expiries):
    snapshot = await _fetch(symbol, fixture)
    assert len(snapshot) == expected_count
    assert snapshot.underlying == Underlying[symbol]
    assert len(snapshot.expiries) >= expected_min_expiries
    assert snapshot.source == "cboe"
    assert snapshot.delayed_minutes == 15


async def test_spx_fixture_has_both_am_and_pm_roots():
    """The AM/PM settlement split is real: third Fridays list both SPX and SPXW."""
    snapshot = await _fetch("SPX", "spx.json")
    assert set(snapshot.roots) == {"SPX", "SPXW"}
    settlements = {c.settlement for c in snapshot.contracts}
    assert {s.value for s in settlements} == {"AM", "PM"}


async def test_captured_at_is_tz_aware_and_matches_vendor_timestamp():
    raw = _load_fixture_json("spx.json")
    snapshot = await _fetch("SPX", "spx.json")
    assert snapshot.captured_at.tzinfo is not None
    assert snapshot.captured_at.utcoffset() == dt.timedelta(0)
    expected = dt.datetime.strptime(raw["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC)
    assert snapshot.captured_at == expected


async def test_known_contract_maps_fields_correctly():
    """SPX260918C07700000 in the SPX fixture, values as recorded live 2026-09-04."""
    snapshot = await _fetch("SPX", "spx.json")
    contract = next(c for c in snapshot.contracts if c.occ_symbol == "SPX260918C07700000")

    assert contract.root == "SPX"
    assert contract.underlying == Underlying.SPX
    assert contract.settlement.value == "AM"
    assert contract.expiry == dt.date(2026, 9, 18)
    assert contract.strike == 7700.0
    assert contract.right.value == "C"
    assert contract.bid == 71.5
    assert contract.ask == 72.1
    assert contract.last == 72.6
    assert contract.volume == 5864
    assert contract.open_interest == 43793
    assert contract.iv == pytest.approx(0.1071)
    assert contract.delta == pytest.approx(0.5413)
    assert contract.gamma == pytest.approx(0.0025)
    assert contract.vega == pytest.approx(5.9493)
    assert contract.theta == pytest.approx(-2.3524)
    assert contract.multiplier == 100


async def test_last_trade_time_converts_ny_to_correct_utc_instant():
    """Vendor sends '2026-09-04T13:53:06' naive America/New_York (EDT, UTC-4)."""
    snapshot = await _fetch("SPX", "spx.json")
    contract = next(c for c in snapshot.contracts if c.occ_symbol == "SPX260918C07700000")
    assert contract.last_trade_time == dt.datetime(2026, 9, 4, 17, 53, 6, tzinfo=dt.UTC)
    assert contract.last_trade_time.tzinfo is dt.UTC


async def test_iv_zero_sentinel_becomes_none():
    """SPX270617C00200000 is quoted iv: 0.0 in the fixture — an inversion sentinel, not data."""
    raw = _load_fixture_json("spx.json")
    raw_contract = next(o for o in raw["data"]["options"] if o["option"] == "SPX270617C00200000")
    assert raw_contract["iv"] == 0.0  # sanity: the fixture still carries the sentinel case

    snapshot = await _fetch("SPX", "spx.json")
    contract = next(c for c in snapshot.contracts if c.occ_symbol == "SPX270617C00200000")
    assert contract.iv is None


async def test_gamma_zero_is_passed_through_not_sentineled():
    """SPX260918C00200000 is quoted gamma: 0.0 — genuine vendor rounding, must survive."""
    raw = _load_fixture_json("spx.json")
    raw_contract = next(o for o in raw["data"]["options"] if o["option"] == "SPX260918C00200000")
    assert raw_contract["gamma"] == 0.0
    assert raw_contract["iv"] != 0.0  # isolate the gamma case from the iv-sentinel case

    snapshot = await _fetch("SPX", "spx.json")
    contract = next(c for c in snapshot.contracts if c.occ_symbol == "SPX260918C00200000")
    assert contract.gamma == 0.0
    assert contract.iv is not None


async def test_spot_is_current_price():
    raw = _load_fixture_json("spx.json")
    snapshot = await _fetch("SPX", "spx.json")
    assert snapshot.spot == pytest.approx(raw["data"]["current_price"])


async def test_unsupported_symbol_raises_without_network():
    """Validation happens before any request is built, so no client/transport is needed."""
    provider = CboeProvider()
    with pytest.raises(SymbolNotSupported):
        await provider.fetch_chain("IWM")


async def test_unknown_root_contract_is_skipped_not_fatal(caplog):
    """An unmapped root (e.g. a new quarterly series) must not blank out the whole snapshot."""
    payload = {
        "timestamp": "2026-09-04 18:18:34",
        "data": {
            "current_price": 500.0,
            "options": [
                {
                    "option": "SPY260904C00500000",
                    "bid": 1.0,
                    "ask": 1.1,
                    "iv": 0.2,
                    "open_interest": 10,
                    "volume": 5,
                    "delta": 0.5,
                    "gamma": 0.01,
                    "vega": 0.1,
                    "theta": -0.05,
                    "last_trade_price": 1.05,
                    "last_trade_time": "2026-09-04T13:00:00",
                },
                {
                    "option": "ZZZ260904C00500000",
                    "bid": 1.0,
                    "ask": 1.1,
                    "iv": 0.2,
                    "open_interest": 10,
                    "volume": 5,
                    "delta": 0.5,
                    "gamma": 0.01,
                    "vega": 0.1,
                    "theta": -0.05,
                    "last_trade_price": 1.05,
                    "last_trade_time": "2026-09-04T13:00:00",
                },
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeProvider(client=client)
    try:
        with caplog.at_level(logging.WARNING):
            snapshot = await provider.fetch_chain("SPY")
    finally:
        await client.aclose()

    assert len(snapshot) == 1
    assert snapshot.contracts[0].occ_symbol == "SPY260904C00500000"
    assert any("ZZZ260904C00500000" in record.message for record in caplog.records)


async def test_retries_on_transport_error_then_succeeds():
    calls = {"n": 0}
    payload = _load_fixture("spy.json")

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeProvider(client=client, backoff_seconds=0.0)
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
    provider = CboeProvider(client=client, max_retries=2, backoff_seconds=0.0)
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_chain("SPY")
    finally:
        await client.aclose()


# --- T06 review regressions ---------------------------------------------------------------
# Each failure below used to escape `fetch_chain` as something other than a ProviderError,
# which aborts `capture_all_symbols` mid-loop and loses every symbol after the failing one.


async def test_html_body_with_status_200_raises_upstream_unavailable():
    """A CDN error page or bot challenge served with status 200 previously raised
    json.JSONDecodeError straight out of the provider."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>Access Denied</html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeProvider(client=client, max_retries=2, backoff_seconds=0.0)
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_chain("SPX")
    finally:
        await client.aclose()


async def test_contract_entry_missing_option_key_is_skipped_not_fatal(caplog):
    """A malformed entry previously raised KeyError('option') for the whole snapshot."""
    payload = {
        "timestamp": "2026-09-04 18:18:34",
        "data": {
            "current_price": 500.0,
            "options": [
                {"bid": 1.0, "iv": 0.2},  # no "option" key at all
                {"option": "SPY260904C00500000", "bid": 1.0, "iv": 0.2, "open_interest": 10},
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeProvider(client=client)
    try:
        with caplog.at_level(logging.WARNING):
            snapshot = await provider.fetch_chain("SPY")
    finally:
        await client.aclose()

    assert len(snapshot) == 1
    assert any("KeyError" in record.message for record in caplog.records)


async def test_chain_with_no_usable_contracts_raises_instead_of_storing_an_empty_snapshot():
    """A zero-contract snapshot has a GEX of exactly 0 and looks like a success in the logs --
    worse than a loud failure, because the hole stays invisible for months."""
    payload = {
        "timestamp": "2026-09-04 18:18:34",
        "data": {"current_price": 500.0, "options": [{"option": "ZZZ260904C00500000"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeProvider(client=client)
    try:
        with pytest.raises(UpstreamUnavailable, match="no usable contracts"):
            await provider.fetch_chain("SPY")
    finally:
        await client.aclose()


async def test_open_interest_float_is_rounded_not_truncated():
    payload = {
        "timestamp": "2026-09-04 18:18:34",
        "data": {
            "current_price": 500.0,
            "options": [
                {
                    "option": "SPY260904C00500000",
                    "iv": 0.2,
                    "open_interest": 42.7,
                    "volume": 3.4,
                }
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CboeProvider(client=client)
    try:
        snapshot = await provider.fetch_chain("SPY")
    finally:
        await client.aclose()

    assert snapshot.contracts[0].open_interest == 43
    assert snapshot.contracts[0].volume == 3
