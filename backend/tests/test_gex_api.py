"""Tests for `app/modules/gex/api/gex.py` (T11).

Entirely offline: SQLite + a `tmp_path` Parquet root, the real SPX fixture
(`tests/fixtures/cboe/spx.json`, 246 contracts, per T08's own test suite) run through the real
`CboeProvider` to get a genuine `ChainSnapshot`, then written and indexed exactly as
`app.modules.gex.jobs.capture.capture_snapshot` would. `get_session_factory` is monkeypatched at its import
site in `app.modules.gex.api.gex`, same pattern as `tests/test_snapshots_api.py`.

The live end-to-end path (real Postgres, a real captured snapshot, all four T11 routes hit for
real) is covered separately by the manual verification run in the T11 report, not here.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.api.gex import router
from app.modules.gex.gex.store import compute_and_store
from app.modules.gex.models.chain import ChainSnapshot, OptionContract, Underlying
from app.modules.gex.models.db import Base
from app.modules.gex.providers.cboe import CboeProvider
from app.modules.gex.storage.parquet import write_snapshot
from app.modules.gex.storage.repository import SnapshotRepository

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cboe"


@pytest.fixture
def client():
    app = FastAPI()
    # T75: `/api/gex`, matching how `app/main.py` mounts `app.modules.gex.router` -- these
    # per-router mini-apps exist to keep the tests offline, not to serve a different URL space.
    app.include_router(router, prefix="/api/gex")
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
    """The real 246-contract SPX fixture, parsed by the real Cboe provider -- same loader
    `tests/test_gex_engine.py` uses, copied rather than imported so this file has no
    cross-test-module dependency.
    """
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
    """Write the fixture snapshot to Parquet and index it, is_eod=True. Returns the row id."""
    path = write_snapshot(spx_fixture_snapshot, data_dir=tmp_path)
    with session_factory() as session:
        row = SnapshotRepository(session).add(spx_fixture_snapshot, path, is_eod=True)
        return row.id


def _patched_client(monkeypatch, client, session_factory):
    monkeypatch.setattr("app.modules.gex.api.gex.get_session_factory", lambda: session_factory)
    return client


# --------------------------------------------------------------------------------------
# GET /gex/{underlying}/latest
# --------------------------------------------------------------------------------------


def test_latest_returns_full_gex_result_shape(client, monkeypatch, tmp_path, session_factory, indexed_row):
    _patched_client(monkeypatch, client, session_factory)

    response = client.get("/api/gex/gex/SPX/latest", params={"filter": "ALL"})

    assert response.status_code == 200
    body = response.json()
    assert body["underlying"] == "SPX"
    assert body["filter"] == "ALL"
    assert body["snapshot"]["id"] == indexed_row
    assert body["snapshot"]["is_eod"] is True
    assert body["snapshot"]["underlying"] == "SPX"
    assert set(body["levels"]) >= {"net_gex", "call_wall", "put_wall", "flip_point", "spot"}
    assert len(body["by_strike"]) > 0
    assert len(body["by_expiry"]) == 4  # the fixture's four expiries
    assert len(body["profile"]) == 201  # engine default grid, TASKS.md T08
    assert "diagnostics" in body


def test_latest_effective_at_equals_captured_at_mid_session(
    client, monkeypatch, session_factory, indexed_row
):
    """T34: the SPX fixture's vendor timestamp (2026-09-04 18:18:34 UTC == 14:18:34 ET) falls
    inside the regular session, so the derived `effective_at` must equal `captured_at`
    verbatim -- the intraday case (T18) this fix must not disturb.
    """
    _patched_client(monkeypatch, client, session_factory)
    body = client.get("/api/gex/gex/SPX/latest").json()
    assert body["snapshot"]["effective_at"] == body["snapshot"]["captured_at"]
    assert body["snapshot"]["captured_at"] == "2026-09-04T18:18:34Z"


def test_latest_defaults_to_all_filter(client, monkeypatch, session_factory, indexed_row):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/SPX/latest")
    assert response.status_code == 200
    assert response.json()["filter"] == "ALL"


def test_latest_unsupported_underlying_is_422_not_500(client, monkeypatch, session_factory):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/DOGE/latest")
    assert response.status_code == 422


def test_latest_with_no_snapshot_yet_is_404(client, monkeypatch, session_factory):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/SPX/latest")
    assert response.status_code == 404


def test_latest_rejects_unknown_filter_with_422(client, monkeypatch, session_factory, indexed_row):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/SPX/latest", params={"filter": "BOGUS"})
    assert response.status_code == 422


def test_latest_explicit_expiries_filter_restricts_by_expiry(
    client, monkeypatch, session_factory, indexed_row
):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/SPX/latest", params={"filter": "EXPIRIES:2026-09-18"})
    assert response.status_code == 200
    body = response.json()
    assert body["filter"] == "EXPIRIES:2026-09-18"
    assert [e["expiry"] for e in body["by_expiry"]] == ["2026-09-18"]


def test_latest_explicit_expiries_bad_date_is_422_not_500(
    client, monkeypatch, session_factory, indexed_row
):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/SPX/latest", params={"filter": "EXPIRIES:not-a-date"})
    assert response.status_code == 422


def test_flip_point_serializes_as_json_null_never_zero(
    client, monkeypatch, session_factory, indexed_row
):
    """The fixture's ZERO_DTE slice is admitted at capture-time (14:18 ET, still alive) --
    this test just confirms flip_point round-trips through JSON as `null`, not `0`, whenever
    the engine returns `None` for it, by directly checking the type against the raw response
    text rather than the parsed dict (`json.loads` would turn a real `0` and a `null` back
    into indistinguishable-looking values once parsed as plain Python).
    """
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/SPX/latest", params={"filter": "ALL"})
    body = response.json()
    flip = body["levels"]["flip_point"]
    assert flip is None or isinstance(flip, float)
    # The raw JSON text must spell a missing flip as the literal token `null`.
    if flip is None:
        assert '"flip_point":null' in response.text.replace(" ", "")


# --------------------------------------------------------------------------------------
# GET /gex/{underlying}/snapshots/{snapshot_id}
# --------------------------------------------------------------------------------------


def test_snapshot_by_id_matches_latest_for_the_same_row(
    client, monkeypatch, session_factory, indexed_row
):
    _patched_client(monkeypatch, client, session_factory)
    latest = client.get("/api/gex/gex/SPX/latest").json()
    by_id = client.get(f"/api/gex/gex/SPX/snapshots/{indexed_row}").json()
    assert latest == by_id


def test_snapshot_missing_id_is_404(client, monkeypatch, session_factory):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/SPX/snapshots/999999")
    assert response.status_code == 404


def test_snapshot_wrong_underlying_for_id_is_404(client, monkeypatch, session_factory, indexed_row):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get(f"/api/gex/gex/SPY/snapshots/{indexed_row}")
    assert response.status_code == 404


# --------------------------------------------------------------------------------------
# ZERO_DTE-after-close: the null-not-zero scenario (mirrors tests/test_gex_store.py).
# --------------------------------------------------------------------------------------

CAPTURED_AFTER_CLOSE = dt.datetime(2026, 9, 4, 20, 20, 0, tzinfo=dt.UTC)  # 16:20 ET


def _make_contract(symbol: str, **kw) -> OptionContract:
    return OptionContract.from_occ(symbol, **kw)


def _make_after_close_snapshot() -> ChainSnapshot:
    contracts = [
        _make_contract("SPY260904C00500000", open_interest=1000, iv=0.15, gamma=0.01, delta=0.5),
        _make_contract("SPY260904P00500000", open_interest=800, iv=0.16, gamma=0.01, delta=-0.5),
        _make_contract("SPY260911C00505000", open_interest=1500, iv=0.18, gamma=0.008, delta=0.4),
        _make_contract("SPY260911P00495000", open_interest=1200, iv=0.19, gamma=0.009, delta=-0.4),
    ]
    return ChainSnapshot(
        underlying=Underlying.SPY,
        spot=500.0,
        captured_at=CAPTURED_AFTER_CLOSE,
        source="stub",
        delayed_minutes=15,
        contracts=contracts,
    )


@pytest.fixture
def after_close_row(tmp_path, session_factory):
    snapshot = _make_after_close_snapshot()
    path = write_snapshot(snapshot, data_dir=tmp_path)
    with session_factory() as session:
        row = SnapshotRepository(session).add(snapshot, path, is_eod=True)
        return row.id


def test_zero_dte_after_close_nulls_not_zeros_over_the_wire(
    client, monkeypatch, session_factory, after_close_row
):
    """The everyday EOD case (T09 brief, T11 brief): every same-day contract has expired by
    16:20 ET, so ZERO_DTE's walls/flip must arrive as JSON `null`, and `net_gex` as `0.0` --
    never a fabricated wall at strike zero.
    """
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/SPY/latest", params={"filter": "ZERO_DTE"})
    assert response.status_code == 200
    body = response.json()
    levels = body["levels"]
    assert levels["call_wall"] is None
    assert levels["put_wall"] is None
    assert levels["max_abs_strike"] is None
    assert levels["flip_point"] is None
    assert levels["net_gex"] == 0.0
    assert body["by_strike"] == []

    # EX_ZERO_DTE on the same snapshot has real, non-null levels.
    live = client.get("/api/gex/gex/SPY/latest", params={"filter": "EX_ZERO_DTE"}).json()
    assert live["levels"]["call_wall"] is not None


def test_effective_at_clamps_to_close_after_hours(client, monkeypatch, session_factory, after_close_row):
    """T34's core acceptance: `CAPTURED_AFTER_CLOSE` is 2026-09-04 20:20 UTC (16:20 ET) --
    already past the 16:00 ET close -- so `effective_at` must clamp to 16:15 ET (close +
    the fixture's 15-minute `delayed_minutes`), not echo `captured_at` (which would read as
    "5 minutes old" instead of the true, much larger, age once viewed later that evening).
    `captured_at` itself must be untouched.
    """
    _patched_client(monkeypatch, client, session_factory)
    body = client.get("/api/gex/gex/SPY/latest").json()
    snapshot = body["snapshot"]
    assert snapshot["captured_at"] == "2026-09-04T20:20:00Z"
    assert snapshot["effective_at"] == "2026-09-04T20:15:00Z"
    assert snapshot["effective_at"] != snapshot["captured_at"]


# --------------------------------------------------------------------------------------
# GET /gex/{underlying}/levels/history
# --------------------------------------------------------------------------------------


def test_levels_history_never_opens_parquet(
    client, monkeypatch, tmp_path, session_factory, indexed_row
):
    """Delete the Parquet file after computing levels, then confirm the history endpoint
    still answers -- proof it reads only `gex_levels`/`snapshots`, exactly as its docstring
    (and T11's brief: "must not touch Parquet -- that endpoint may span months") requires.
    """
    compute_and_store(indexed_row, session_factory=session_factory, data_dir=tmp_path)
    _patched_client(monkeypatch, client, session_factory)

    with session_factory() as session:
        from app.modules.gex.models.db import Snapshot

        row = session.get(Snapshot, indexed_row)
        parquet_file = tmp_path / row.parquet_path
    assert parquet_file.exists()
    parquet_file.unlink()

    response = client.get("/api/gex/gex/SPX/levels/history", params={"filter": "ALL"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["snapshot_id"] == indexed_row
    assert body[0]["call_wall"] is not None


def test_levels_history_respects_eod_only_and_date_range(
    client, monkeypatch, tmp_path, session_factory
):
    _patched_client(monkeypatch, client, session_factory)
    base = _make_after_close_snapshot()
    rows = []
    for i, (offset_days, is_eod) in enumerate([(0, True), (1, False), (2, True)]):
        snap = ChainSnapshot(
            underlying=base.underlying,
            spot=base.spot + i,
            captured_at=CAPTURED_AFTER_CLOSE + dt.timedelta(days=offset_days),
            source=base.source,
            delayed_minutes=base.delayed_minutes,
            contracts=base.contracts,
        )
        path = write_snapshot(snap, data_dir=tmp_path)
        with session_factory() as session:
            row = SnapshotRepository(session).add(snap, path, is_eod=is_eod)
            rows.append(row.id)
        compute_and_store(row.id, session_factory=session_factory, data_dir=tmp_path)

    response = client.get(
        "/api/gex/gex/SPY/levels/history", params={"filter": "EX_ZERO_DTE", "eod_only": "true"}
    )
    assert response.status_code == 200
    body = response.json()
    assert [r["snapshot_id"] for r in body] == [rows[0], rows[2]]
    assert all(r["is_eod"] for r in body)

    # Date range: only the first day.
    response = client.get(
        "/api/gex/gex/SPY/levels/history",
        params={
            "filter": "EX_ZERO_DTE",
            "start": CAPTURED_AFTER_CLOSE.isoformat(),
            "end": (CAPTURED_AFTER_CLOSE + dt.timedelta(hours=1)).isoformat(),
        },
    )
    assert [r["snapshot_id"] for r in response.json()] == [rows[0]]


def test_levels_history_rejects_explicit_expiries_filter(
    client, monkeypatch, session_factory, indexed_row
):
    """`gex_levels` never persists an `EXPIRIES:...` row (only the default filters are
    computed by `app.modules.gex.gex.store`), so this must be a 422, not a silently-empty 200.
    """
    _patched_client(monkeypatch, client, session_factory)
    response = client.get(
        "/api/gex/gex/SPX/levels/history", params={"filter": "EXPIRIES:2026-09-18"}
    )
    assert response.status_code == 422


def test_levels_history_unknown_underlying_is_422(client, monkeypatch, session_factory):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/DOGE/levels/history")
    assert response.status_code == 422


def test_levels_history_empty_for_a_symbol_with_no_captures(client, monkeypatch, session_factory):
    _patched_client(monkeypatch, client, session_factory)
    response = client.get("/api/gex/gex/QQQ/levels/history")
    assert response.status_code == 200
    assert response.json() == []
