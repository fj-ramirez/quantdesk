"""Tests for `app/modules/gex/api/report.py` (T39).

Entirely offline, in the same shape as `tests/test_gex_api.py`: SQLite plus a `tmp_path`
Parquet root, the committed GLD/DIA Cboe fixtures run through the real `CboeProvider` to get
genuine `ChainSnapshot`s, then written and indexed exactly as `app.modules.gex.jobs.capture` would.
`get_session_factory` is monkeypatched at its import site in `app.modules.gex.api.report`.

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

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.api.report import router
from app.modules.gex.gex.engine import ExpiryFilter, compute_all, to_frame
from app.modules.gex.gex.report import build_report, render_text
from app.modules.gex.models.chain import ChainSnapshot
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


@pytest.fixture(scope="module")
def xlk_snapshot() -> ChainSnapshot:
    """T47: a sector ETF with no `CFD_INSTRUMENTS` entry, for the CFD-degrade tests below.
    Trimmed the same way gld.json/dia.json were (head+tail slice of the live 2026-09-09
    capture) to keep the fixture small."""
    return _fixture_snapshot("XLK", "xlk.json")


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


@pytest.fixture
def indexed_xlk(tmp_path, session_factory, xlk_snapshot):
    path = write_snapshot(xlk_snapshot, data_dir=tmp_path)
    with session_factory() as session:
        return SnapshotRepository(session).add(xlk_snapshot, path, is_eod=True).id


def _patch(monkeypatch, session_factory):
    monkeypatch.setattr("app.modules.gex.api.report.get_session_factory", lambda: session_factory)


# --------------------------------------------------------------------------------------
# The acceptance criterion: API output == direct compute_all + build_report
# --------------------------------------------------------------------------------------


def test_report_matches_a_direct_compute_and_build(
    client, monkeypatch, session_factory, gld_snapshot, indexed_gld
):
    """`GET /api/gex/report/GLD` must equal a direct `compute_all` + `build_report` call.

    Compared through the pure module's own `to_dict()` so this asserts the *whole* payload,
    not a handful of spot-checked fields. Only `snapshot` is excluded: the API merges `id`,
    `is_eod` and `effective_at` into it (the T11/T34 merge the engine deliberately omits
    because it does no I/O), so it legitimately carries three fields the pure result cannot.
    """
    _patch(monkeypatch, session_factory)

    frame = to_frame(gld_snapshot)
    result = compute_all(gld_snapshot, ExpiryFilter.ALL, frame=frame)
    expected = build_report(result, frame, ExpiryFilter.ALL).to_dict()

    body = client.get("/api/gex/report/GLD", params={"filter": "ALL"}).json()

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

    response = client.get("/api/gex/report/GLD", params={"format": "text"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == expected


# --------------------------------------------------------------------------------------
# Shape, and the figures a reader would check
# --------------------------------------------------------------------------------------


def test_report_carries_every_section(client, monkeypatch, session_factory, indexed_gld):
    _patch(monkeypatch, session_factory)
    body = client.get("/api/gex/report/GLD").json()

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
    regime = client.get("/api/gex/report/GLD").json()["iv_regime"]

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
    ratios = client.get("/api/gex/report/GLD").json()["ratios"]

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
    levels = client.get("/api/gex/report/DIA").json()["levels"]

    if levels["resistance"] and levels["support"]:
        assert min(row["strike"] for row in levels["resistance"]) > max(
            row["strike"] for row in levels["support"]
        )


def test_empty_filter_scope_returns_a_report_not_an_error(
    client, monkeypatch, session_factory, indexed_gld
):
    """`ZERO_DTE` on an EOD capture admits nothing. 200 with empty sections, never a 500."""
    _patch(monkeypatch, session_factory)
    response = client.get("/api/gex/report/GLD", params={"filter": "ZERO_DTE"})

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
    response = client.get("/api/gex/report/SPX")

    assert response.status_code == 404
    assert response.json()["detail"] == "no snapshot captured yet for SPX"


def test_unsupported_underlying_is_422(client, monkeypatch, session_factory):
    _patch(monkeypatch, session_factory)
    response = client.get("/api/gex/report/NOPE")
    assert response.status_code == 422
    assert "unsupported underlying" in response.json()["detail"]


def test_unknown_filter_is_422(client, monkeypatch, session_factory, indexed_gld):
    _patch(monkeypatch, session_factory)
    response = client.get("/api/gex/report/GLD", params={"filter": "SOMEDAY"})
    assert response.status_code == 422
    assert "unknown filter" in response.json()["detail"]


def test_unknown_format_is_422(client, monkeypatch, session_factory, indexed_gld):
    _patch(monkeypatch, session_factory)
    response = client.get("/api/gex/report/GLD", params={"format": "pdf"})
    assert response.status_code == 422
    assert "unknown format" in response.json()["detail"]


def test_explicit_expiry_filter_round_trips(client, monkeypatch, session_factory, gld_snapshot, indexed_gld):
    """The `EXPIRIES:<date>` form `api/gex.py` accepts works here too, and echoes back."""
    _patch(monkeypatch, session_factory)
    expiry = min(c.expiry for c in gld_snapshot.contracts)

    response = client.get("/api/gex/report/GLD", params={"filter": f"EXPIRIES:{expiry.isoformat()}"})

    assert response.status_code == 200
    assert response.json()["filter"] == f"EXPIRIES:{expiry.isoformat()}"


def test_pinned_snapshot_id_is_honoured(client, monkeypatch, session_factory, indexed_gld):
    _patch(monkeypatch, session_factory)
    body = client.get("/api/gex/report/GLD", params={"snapshot": indexed_gld}).json()
    assert body["snapshot"]["id"] == indexed_gld


def test_pinned_snapshot_for_the_wrong_symbol_is_404(
    client, monkeypatch, session_factory, indexed_gld
):
    _patch(monkeypatch, session_factory)
    response = client.get("/api/gex/report/DIA", params={"snapshot": indexed_gld})
    assert response.status_code == 404


# --------------------------------------------------------------------------------------
# CFD translation (T41) -- `?cfd_spot=` at the HTTP boundary
# --------------------------------------------------------------------------------------


def test_absent_cfd_spot_leaves_the_response_unchanged(
    client, monkeypatch, session_factory, indexed_gld
):
    """No `cfd_spot` query param -> `cfd` is `null` and every other field is exactly what a
    request built before T41 existed would have returned."""
    _patch(monkeypatch, session_factory)

    without = client.get("/api/gex/report/GLD").json()
    assert without["cfd"] is None

    with_param_omitted_entirely = client.get("/api/gex/report/GLD", params={"filter": "ALL"}).json()
    assert with_param_omitted_entirely["cfd"] is None
    without.pop("generated_at")
    with_param_omitted_entirely.pop("generated_at")
    assert without == with_param_omitted_entirely


def test_cfd_spot_returns_a_converted_block_matching_the_percentage_invariant(
    client, monkeypatch, session_factory, gld_snapshot, indexed_gld
):
    """`GET /api/gex/report/GLD?cfd_spot=4412.50` -- T41's acceptance criterion at the wire.

    Cross-checked against a direct `translate_to_cfd` call so the endpoint cannot drift from
    the pure module, and the percentage-distance invariant is asserted on the actual numbers
    this fixture produces.
    """
    _patch(monkeypatch, session_factory)
    cfd_spot = 4412.50

    frame = to_frame(gld_snapshot)
    result = compute_all(gld_snapshot, ExpiryFilter.ALL, frame=frame)
    expected = build_report(result, frame, ExpiryFilter.ALL, cfd_spot=cfd_spot).to_dict()

    body = client.get("/api/gex/report/GLD", params={"cfd_spot": cfd_spot}).json()

    assert body["cfd"] is not None
    assert body["cfd"] == expected["cfd"]

    ratio = cfd_spot / body["spot"]
    assert body["cfd"]["ratio"] == pytest.approx(ratio)
    for native, translated in zip(body["levels"]["resistance"], body["cfd"]["resistance"], strict=True):
        assert translated["strike"] == pytest.approx(native["strike"] * ratio)
        assert translated["distance_pct"] == native["distance_pct"]

    # Never-convert fields: unaffected by the presence of `cfd_spot`.
    without_cfd = client.get("/api/gex/report/GLD").json()
    assert body["positioning"] == without_cfd["positioning"]
    assert body["ratios"] == without_cfd["ratios"]
    assert body["iv_regime"] == without_cfd["iv_regime"]
    assert body["premium"] == without_cfd["premium"]


@pytest.mark.parametrize("bad_value", ["0", "-1", "-4412.50", "not-a-number"])
def test_bad_cfd_spot_is_rejected_with_422(
    client, monkeypatch, session_factory, indexed_gld, bad_value
):
    """Zero, negative and non-numeric `cfd_spot` are all rejected rather than producing
    infinities -- `Query(..., gt=0)` handles the numeric cases and FastAPI's own type
    validation handles the non-numeric one."""
    _patch(monkeypatch, session_factory)
    response = client.get("/api/gex/report/GLD", params={"cfd_spot": bad_value})
    assert response.status_code == 422


# --------------------------------------------------------------------------------------
# T47: CFD_INSTRUMENTS degrade for a symbol without an entry (every sector/industry ETF today)
# --------------------------------------------------------------------------------------


def test_cfd_spot_for_an_unmapped_underlying_degrades_to_no_cfd_mapping(
    client, monkeypatch, session_factory, indexed_xlk
):
    """Pins the T47 behaviour change: before this task, `?cfd_spot=` on a symbol absent from
    `CFD_INSTRUMENTS` (XLK has no broker CFD mapping configured) hit `translate_to_cfd`'s
    "no CFD instrument is configured" `ValueError` and surfaced as a 422 -- turning an
    *optional* add-on into a hard failure for the whole report. It must now degrade: the
    request succeeds, `cfd` stays `null`, and every other field is unaffected."""
    _patch(monkeypatch, session_factory)

    response = client.get("/api/gex/report/XLK", params={"cfd_spot": 250.0})

    assert response.status_code == 200
    body = response.json()
    assert body["cfd"] is None

    # Identical to the response with no `cfd_spot` at all, except the timestamp the pure
    # module reads no clock for -- the degrade must be silent, not merely non-crashing.
    without = client.get("/api/gex/report/XLK").json()
    body.pop("generated_at")
    without.pop("generated_at")
    assert body == without


def test_cfd_spot_for_a_mapped_underlying_is_unaffected_by_the_degrade(
    client, monkeypatch, session_factory, indexed_gld
):
    """The degrade in `_build` must not touch the five symbols `CFD_INSTRUMENTS` already
    covers -- GLD's existing T41 conversion still fires exactly as before."""
    _patch(monkeypatch, session_factory)
    response = client.get("/api/gex/report/GLD", params={"cfd_spot": 4412.50})
    assert response.status_code == 200
    assert response.json()["cfd"] is not None
