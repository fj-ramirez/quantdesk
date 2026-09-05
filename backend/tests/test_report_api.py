"""Tests for `app/api/report.py` (T39).

Entirely offline, in the same shape as `tests/test_gex_api.py`: SQLite plus a `tmp_path`
Parquet root, the committed GLD/DIA Cboe fixtures run through the real `CboeProvider` to get
genuine `ChainSnapshot`s, then written and indexed exactly as `app.jobs.capture` would.
`get_session_factory` is monkeypatched at its import site in `app.api.report`.

The load-bearing test here is `test_report_matches_a_direct_compute_and_build` — T39's own
acceptance criterion. It re-runs `compute_all` + `build_report` in the test process and
compares the endpoint's JSON field by field, so the API cannot drift into recomputing
something subtly different (a different filter, a second `to_frame` against a different clock)
from what the pure module produces.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.report import router
from app.gex.engine import ExpiryFilter, compute_all, to_frame
from app.gex.report import build_report, render_text
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


def _fixture_snapshot(symbol: str, filename: str) -> ChainSnapshot:
    payload = (FIXTURES_DIR / filename).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def fetch() -> ChainSnapshot:
        transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await CboeProvider(client=transport_client).fetch_chain(symbol)
        finally:
            await transport_client.aclose()

    return asyncio.run(fetch())


@pytest.fixture(scope="module")
def gld_snapshot() -> ChainSnapshot:
    return _fixture_snapshot("GLD", "gld.json")


@pytest.fixture(scope="module")
def dia_snapshot() -> ChainSnapshot:
    return _fixture_snapshot("DIA", "dia.json")


@pytest.fixture
def indexed_gld(tmp_path, session_factory, gld_snapshot):
    path = write_snapshot(gld_snapshot, data_dir=tmp_path)
    with session_factory() as session:
        return SnapshotRepository(session).add(gld_snapshot, path, is_eod=True).id


@pytest.fixture
def indexed_dia(tmp_path, session_factory, dia_snapshot):
    path = write_snapshot(dia_snapshot, data_dir=tmp_path)
    with session_factory() as session:
        return SnapshotRepository(session).add(dia_snapshot, path, is_eod=True).id


def _patch(monkeypatch, session_factory):
    monkeypatch.setattr("app.api.report.get_session_factory", lambda: session_factory)


# --------------------------------------------------------------------------------------
# The acceptance criterion: API output == direct compute_all + build_report
# --------------------------------------------------------------------------------------


def test_report_matches_a_direct_compute_and_build(
    client, monkeypatch, session_factory, gld_snapshot, indexed_gld
):
    """`GET /api/report/GLD` must equal a direct `compute_all` + `build_report` call.

    Compared through the pure module's own `to_dict()` so this asserts the *whole* payload,
    not a handful of spot-checked fields. Only `snapshot` is excluded: the API merges `id`,
    `is_eod` and `effective_at` into it (the T11/T34 merge the engine deliberately omits
    because it does no I/O), so it legitimately carries three fields the pure result cannot.
    """
    _patch(monkeypatch, session_factory)

    frame = to_frame(gld_snapshot)
    result = compute_all(gld_snapshot, ExpiryFilter.ALL, frame=frame)
    expected = build_report(result, frame, ExpiryFilter.ALL).to_dict()

    body = client.get("/api/report/GLD", params={"filter": "ALL"}).json()

    assert body["snapshot"]["id"] == indexed_gld
    assert body["snapshot"]["is_eod"] is True

    # `generated_at` is compared as an instant, not a string: the pure module emits
    # `datetime.isoformat()` ("+00:00") while Pydantic serializes the same UTC instant as
    # "Z". That is the app-wide wire convention (`tests/test_gex_api.py` pins it), so the
    # difference is a formatting one and asserting on the text would be asserting the wrong
    # thing.
    assert dt.datetime.fromisoformat(body["generated_at"]) == dt.datetime.fromisoformat(
        expected["generated_at"]
    )

    for key in expected:
        if key in ("snapshot", "generated_at"):
            continue
        assert body[key] == expected[key], f"{key} diverged from the pure computation"


def test_report_text_format_matches_render_text(
    client, monkeypatch, session_factory, gld_snapshot, indexed_gld
):
    _patch(monkeypatch, session_factory)

    frame = to_frame(gld_snapshot)
    result = compute_all(gld_snapshot, ExpiryFilter.ALL, frame=frame)
    expected = render_text(build_report(result, frame, ExpiryFilter.ALL))

    response = client.get("/api/report/GLD", params={"format": "text"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == expected


# --------------------------------------------------------------------------------------
# Shape, and the figures a reader would check
# --------------------------------------------------------------------------------------


def test_report_carries_every_section(client, monkeypatch, session_factory, indexed_gld):
    _patch(monkeypatch, session_factory)
    body = client.get("/api/report/GLD").json()

    assert body["underlying"] == "GLD"
    assert body["filter"] == "ALL"
    assert set(body) >= {
        "spot",
        "generated_at",
        "snapshot",
        "max_pain",
        "ratios",
        "iv_regime",
        "positioning",
        "levels",
        "premium",
        "playbook",
        "alerts",
        "summary",
    }
    assert body["snapshot"]["effective_at"], "T34's staleness field must reach the report page"


def test_iv_regime_label_is_null_on_a_single_snapshot_database(
    client, monkeypatch, session_factory, indexed_gld
):
    """T39's acceptance criterion, at the HTTP boundary.

    Nothing persists ATM IV history, so the endpoint supplies no `iv_history` and the label
    must come back `null` with the number still present. The frontend renders "insufficient
    history" for this; it must never see a fabricated band.
    """
    _patch(monkeypatch, session_factory)
    regime = client.get("/api/report/GLD").json()["iv_regime"]

    assert regime["atm_iv"] is not None
    assert regime["label"] is None
    assert regime["history_observations"] == 0
    assert regime["min_history_required"] == 20


def test_ratios_are_puts_over_calls_over_the_wire(
    client, monkeypatch, session_factory, indexed_gld
):
    """The ratio's orientation survives serialization: it is always puts / calls.

    The example report inverted exactly this on GLD (2.54 puts per call, against the real
    chain's 0.45) and derived a bearish reading from the inversion. The direction of the
    inequality is *not* asserted here, because the committed fixture is trimmed to three
    expiries and does not reproduce the full chain's call-heavy open interest -- the full
    2026-09-05 GLD chain carries 4,304,029 call OI against 1,945,056 put OI, this 154-contract
    slice of it does not. What must hold on any chain is that the published ratio is its own
    numerator over its own denominator, which is what a reader would check.
    """
    _patch(monkeypatch, session_factory)
    ratios = client.get("/api/report/GLD").json()["ratios"]

    assert ratios["open_interest_ratio"] == pytest.approx(
        ratios["put_open_interest"] / ratios["call_open_interest"]
    )
    assert ratios["volume_ratio"] == pytest.approx(
        ratios["put_volume"] / ratios["call_volume"]
    )
    assert (
        ratios["total_open_interest"]
        == ratios["call_open_interest"] + ratios["put_open_interest"]
    )


def test_resistance_stays_above_support(client, monkeypatch, session_factory, indexed_dia):
    _patch(monkeypatch, session_factory)
    levels = client.get("/api/report/DIA").json()["levels"]

    if levels["resistance"] and levels["support"]:
        assert min(row["strike"] for row in levels["resistance"]) > max(
            row["strike"] for row in levels["support"]
        )


def test_empty_filter_scope_returns_a_report_not_an_error(
    client, monkeypatch, session_factory, indexed_gld
):
    """`ZERO_DTE` on an EOD capture admits nothing. 200 with empty sections, never a 500."""
    _patch(monkeypatch, session_factory)
    response = client.get("/api/report/GLD", params={"filter": "ZERO_DTE"})

    assert response.status_code == 200
    body = response.json()
    assert body["max_pain"]["strike"] is None
    assert body["levels"]["resistance"] == []
    assert body["positioning"]["direction"] is None
    assert [alert["code"] for alert in body["alerts"]] == ["EMPTY_SCOPE"]


# --------------------------------------------------------------------------------------
# Error contract -- identical to `api/gex.py`, which T37's empty state matches on
# --------------------------------------------------------------------------------------


def test_uncaptured_symbol_is_a_clean_404_with_a_specific_detail(
    client, monkeypatch, session_factory
):
    """The exact 404 body T37's empty state keys off. Not a 500, not a bare body."""
    _patch(monkeypatch, session_factory)
    response = client.get("/api/report/SPX")

    assert response.status_code == 404
    assert response.json()["detail"] == "no snapshot captured yet for SPX"


def test_unsupported_underlying_is_422(client, monkeypatch, session_factory):
    _patch(monkeypatch, session_factory)
    response = client.get("/api/report/NOPE")
    assert response.status_code == 422
    assert "unsupported underlying" in response.json()["detail"]


def test_unknown_filter_is_422(client, monkeypatch, session_factory, indexed_gld):
    _patch(monkeypatch, session_factory)
    response = client.get("/api/report/GLD", params={"filter": "SOMEDAY"})
    assert response.status_code == 422
    assert "unknown filter" in response.json()["detail"]


def test_unknown_format_is_422(client, monkeypatch, session_factory, indexed_gld):
    _patch(monkeypatch, session_factory)
    response = client.get("/api/report/GLD", params={"format": "pdf"})
    assert response.status_code == 422
    assert "unknown format" in response.json()["detail"]


def test_explicit_expiry_filter_round_trips(client, monkeypatch, session_factory, gld_snapshot, indexed_gld):
    """The `EXPIRIES:<date>` form `api/gex.py` accepts works here too, and echoes back."""
    _patch(monkeypatch, session_factory)
    expiry = min(c.expiry for c in gld_snapshot.contracts)

    response = client.get("/api/report/GLD", params={"filter": f"EXPIRIES:{expiry.isoformat()}"})

    assert response.status_code == 200
    assert response.json()["filter"] == f"EXPIRIES:{expiry.isoformat()}"


def test_pinned_snapshot_id_is_honoured(client, monkeypatch, session_factory, indexed_gld):
    _patch(monkeypatch, session_factory)
    body = client.get("/api/report/GLD", params={"snapshot": indexed_gld}).json()
    assert body["snapshot"]["id"] == indexed_gld


def test_pinned_snapshot_for_the_wrong_symbol_is_404(
    client, monkeypatch, session_factory, indexed_gld
):
    _patch(monkeypatch, session_factory)
    response = client.get("/api/report/DIA", params={"snapshot": indexed_gld})
    assert response.status_code == 404
