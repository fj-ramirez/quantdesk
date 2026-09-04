"""Tests for `app/api/chains.py` (T11): `GET /chains/{underlying}/latest?expiry=`.

Same offline pattern as `tests/test_gex_api.py`: the real SPX fixture through the real
`CboeProvider`, written to a `tmp_path` Parquet root and indexed in a temp SQLite DB.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chains import router
from app.models.chain import ChainSnapshot
from app.models.db import Base, get_engine, get_sessionmaker
from app.providers.cboe import CboeProvider
from app.storage.parquet import write_snapshot
from app.storage.repository import SnapshotRepository

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cboe"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


@pytest.fixture(scope="module")
def spx_fixture_snapshot() -> ChainSnapshot:
    payload = (FIXTURES_DIR / "spx.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def fetch() -> ChainSnapshot:
        transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await CboeProvider(client=transport_client).fetch_chain("SPX")
        finally:
            await transport_client.aclose()

    return asyncio.run(fetch())


@pytest.fixture
def indexed_row(tmp_path, session_factory, spx_fixture_snapshot):
    path = write_snapshot(spx_fixture_snapshot, data_dir=tmp_path)
    with session_factory() as session:
        row = SnapshotRepository(session).add(spx_fixture_snapshot, path, is_eod=False)
        return row.id


def _patched(monkeypatch, client, session_factory):
    monkeypatch.setattr("app.api.chains.get_session_factory", lambda: session_factory)
    return client


def test_latest_chain_returns_only_the_requested_expiry(
    client, monkeypatch, session_factory, indexed_row
):
    _patched(monkeypatch, client, session_factory)
    response = client.get("/api/chains/SPX/latest", params={"expiry": "2026-09-18"})
    assert response.status_code == 200
    body = response.json()
    assert body["underlying"] == "SPX"
    assert body["expiry"] == "2026-09-18"
    assert body["snapshot"]["id"] == indexed_row
    assert len(body["contracts"]) == 82  # fixture count for this expiry (test_gex_engine.py)
    assert all(c["expiry"] == "2026-09-18" for c in body["contracts"])


def test_known_contract_fields_match_the_vendor_fixture_verbatim(
    client, monkeypatch, session_factory, indexed_row
):
    """Cross-check against `tests/test_cboe.py`'s own known-contract assertions -- if this
    diverges, the read API is misprojecting a field the provider layer already got right.
    """
    _patched(monkeypatch, client, session_factory)
    body = client.get("/api/chains/SPX/latest", params={"expiry": "2026-09-18"}).json()
    contract = next(c for c in body["contracts"] if c["occ_symbol"] == "SPX260918C07700000")

    assert contract["root"] == "SPX"
    assert contract["settlement"] == "AM"
    assert contract["strike"] == 7700.0
    assert contract["right"] == "C"
    assert contract["open_interest"] == 43793
    assert contract["iv"] == pytest.approx(0.1071)  # decimal fraction, never a percent
    assert contract["gamma"] == pytest.approx(0.0025)
    assert contract["multiplier"] == 100
    # tz-aware UTC, ISO 8601 with an explicit offset -- never a naive-looking string.
    assert contract["last_trade_time"].startswith("2026-09-04T17:53:06")
    assert contract["last_trade_time"].endswith(("+00:00", "Z"))


def test_zero_open_interest_is_preserved_not_dropped(
    client, monkeypatch, session_factory, indexed_row
):
    """19 contracts in the SPX fixture genuinely have `open_interest: 0` (verified above);
    the response must carry the literal `0`, not `null` and not a filtered-out row.
    """
    _patched(monkeypatch, client, session_factory)
    body = client.get("/api/chains/SPX/latest", params={"expiry": "2027-06-17"}).json()
    contracts = body["contracts"]
    assert len(contracts) == 41
    zero_oi = [c for c in contracts if c["open_interest"] == 0]
    assert zero_oi  # at least one genuinely-zero-OI contract on this expiry
    for c in zero_oi:
        assert c["open_interest"] is not None


def test_open_interest_none_is_distinct_from_zero_end_to_end(
    client, monkeypatch, tmp_path, session_factory
):
    """Synthetic contract with unknown (`None`) open interest, since the fixture happens to
    have no such row -- proves the `None`-vs-`0` distinction (`OptionContract`'s own
    docstring: "None means unknown ... 0 means genuinely zero") survives the Parquet
    round-trip and JSON serialization, never collapsing one into the other.
    """
    from app.models.chain import OptionContract, Underlying

    _patched(monkeypatch, client, session_factory)

    contracts = (
        OptionContract.from_occ("SPY260918C00500000", open_interest=None, iv=0.2, gamma=0.01),
        OptionContract.from_occ("SPY260918P00500000", open_interest=0, iv=0.2, gamma=0.01),
    )
    snapshot = ChainSnapshot(
        underlying=Underlying.SPY,
        spot=500.0,
        captured_at=dt.datetime(2026, 9, 4, 18, 0, tzinfo=dt.UTC),
        source="stub",
        delayed_minutes=15,
        contracts=contracts,
    )
    path = write_snapshot(snapshot, data_dir=tmp_path)
    with session_factory() as session:
        SnapshotRepository(session).add(snapshot, path, is_eod=False)

    response = client.get("/api/chains/SPY/latest", params={"expiry": "2026-09-18"})

    assert response.status_code == 200
    by_symbol = {c["occ_symbol"]: c["open_interest"] for c in response.json()["contracts"]}
    assert by_symbol["SPY260918C00500000"] is None
    assert by_symbol["SPY260918P00500000"] == 0


def test_unknown_expiry_returns_empty_contracts_not_404(
    client, monkeypatch, session_factory, indexed_row
):
    _patched(monkeypatch, client, session_factory)
    response = client.get("/api/chains/SPX/latest", params={"expiry": "2099-01-01"})
    assert response.status_code == 200
    assert response.json()["contracts"] == []


def test_missing_expiry_query_param_is_422(client, monkeypatch, session_factory, indexed_row):
    _patched(monkeypatch, client, session_factory)
    response = client.get("/api/chains/SPX/latest")
    assert response.status_code == 422


def test_malformed_expiry_is_422_not_500(client, monkeypatch, session_factory, indexed_row):
    _patched(monkeypatch, client, session_factory)
    response = client.get("/api/chains/SPX/latest", params={"expiry": "not-a-date"})
    assert response.status_code == 422


def test_unsupported_underlying_is_422(client, monkeypatch, session_factory):
    _patched(monkeypatch, client, session_factory)
    response = client.get("/api/chains/DOGE/latest", params={"expiry": "2026-09-18"})
    assert response.status_code == 422


def test_no_snapshot_yet_is_404(client, monkeypatch, session_factory):
    _patched(monkeypatch, client, session_factory)
    response = client.get("/api/chains/SPX/latest", params={"expiry": "2026-09-18"})
    assert response.status_code == 404
