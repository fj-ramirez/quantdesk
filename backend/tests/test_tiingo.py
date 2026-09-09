"""Tests for the Tiingo bars provider skeleton.

Entirely offline, same isolation pattern as `test_marketdata.py`: there is no Tiingo account or
token available in this environment (see `app/providers/tiingo.py`'s module docstring), so the
fixture under `tests/fixtures/tiingo/` is hand-built from Tiingo's published docs, not recorded
live traffic. This suite proves the credential guard and the documented-shape parse work; it
does not, and cannot, prove the provider matches what the live API actually returns.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.providers.bars import SymbolNotSupported, UpstreamUnavailable
from app.providers.tiingo import MissingCredential, TiingoBarProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "tiingo"

_TOKEN = "test-token-123"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _provider_for_fixture(
    name: str, *, status_code: int = 200, **kwargs: Any
) -> tuple[TiingoBarProvider, httpx.AsyncClient]:
    payload = _load_fixture(name)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs.setdefault("token", _TOKEN)
    return TiingoBarProvider(client=client, **kwargs), client


def test_missing_token_raises_missing_credential(monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "TIINGO_TOKEN", "")
    with pytest.raises(MissingCredential):
        TiingoBarProvider()


def test_explicit_token_bypasses_settings():
    # Must not raise even if settings.TIINGO_TOKEN is unset -- constructing with an explicit
    # client avoids any real network call either way.
    provider = TiingoBarProvider(token="explicit-token")
    assert provider is not None


async def test_fixture_parses_to_bars_with_unadjusted_ohlc_and_tiingo_raw_source():
    provider, client = _provider_for_fixture("spy_synthetic.json")
    try:
        bars = await provider.fetch_daily_bars("SPY", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()

    assert len(bars) == 3
    assert [b.date for b in bars] == [
        dt.date(2026, 9, 3),
        dt.date(2026, 9, 4),
        dt.date(2026, 9, 8),
    ]
    assert all(b.source == "tiingo-raw" for b in bars)
    # Unadjusted fields, not adjClose/adjOpen/... -- see module docstring's adjustment caveat.
    last = bars[-1]
    assert last.close == pytest.approx(765.96)
    assert last.volume == 44708800


async def test_404_raises_symbol_not_supported():
    provider, client = _provider_for_fixture("spy_synthetic.json", status_code=404)
    try:
        with pytest.raises(SymbolNotSupported):
            await provider.fetch_daily_bars("ZZZZZNOTREAL", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()


async def test_non_list_payload_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "not a list"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TiingoBarProvider(client=client, token=_TOKEN)
    try:
        with pytest.raises(UpstreamUnavailable):
            await provider.fetch_daily_bars("SPY", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()


async def test_row_with_null_price_field_is_skipped():
    payload = json.loads(_load_fixture("spy_synthetic.json").decode())
    payload[0]["close"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TiingoBarProvider(client=client, token=_TOKEN)
    try:
        bars = await provider.fetch_daily_bars("SPY", start=dt.date(2020, 1, 1))
    finally:
        await client.aclose()

    assert len(bars) == 2
    assert dt.date(2026, 9, 3) not in [b.date for b in bars]
