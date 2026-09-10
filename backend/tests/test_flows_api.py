"""Tests for `GET /api/scan/flows` (T52's additive block in `app/api/scan.py`). Kept as its
own file, separate from `test_scan_api.py`, so it never collides with a concurrent edit to
that file -- mirrors this task's own "one clearly-separated additive block" constraint on the
router module itself.

Offline: `app.storage.flows_repository.get_session_factory` is monkeypatched directly (the
route imports it locally inside `get_flows`, not as a module-level name in `app.api.scan`), no
real Postgres.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.scan import router
from app.models.db import Base, get_engine, get_sessionmaker
from app.providers.etf_flows import SharesOutstandingRow
from app.storage.flows_repository import insert_new_rows


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    monkeypatch.setattr("app.storage.flows_repository.get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


def _row(symbol, as_of, shares, nav, source="spdr-xlsx") -> SharesOutstandingRow:
    return SharesOutstandingRow(
        symbol=symbol, as_of_date=as_of, shares_outstanding=shares, nav=nav, source=source
    )


def test_get_flows_rejects_an_invalid_window(client, session_factory):
    response = client.get("/api/scan/flows", params={"window": 7})
    assert response.status_code == 422


def test_get_flows_defaults_to_window_20(client, session_factory):
    response = client.get("/api/scan/flows")
    assert response.status_code == 200
    assert response.json()["window"] == 20


def test_get_flows_lists_unsupported_symbols_as_no_flow_data_never_a_zero_bar(
    client, session_factory
):
    """T59 gave every one of the T52 survey's four unsupported symbols (SMH, GDX, QQQ, USO) a
    working fetcher, so `no_flow_data` (driven by `UNSUPPORTED_SYMBOLS`) is empty -- but the
    invariant it exists to enforce (nothing unsupported is ever drawn as a zero bar) is still
    worth asserting for whatever `no_flow_data` names now or in the future.
    """
    response = client.get("/api/scan/flows", params={"window": 5})
    body = response.json()
    no_flow_symbols = {s["symbol"] for s in body["no_flow_data"]}
    assert no_flow_symbols == set()
    # None of the unsupported symbols appear in the scored list at all -- never a zero bar.
    scored_symbols = {s["symbol"] for s in body["symbols"]}
    assert no_flow_symbols.isdisjoint(scored_symbols)


def test_get_flows_reports_history_since_message_when_window_exceeds_available_history(
    client, session_factory, monkeypatch
):
    # Anchored via `app.api.scan._today` (same convention `test_scan_api.py`'s rotation tests
    # use) rather than the real wall clock, so this never depends on the UTC/NY-local date
    # boundary lining up with when the suite happens to run.
    anchor = dt.date(2026, 9, 10)
    monkeypatch.setattr("app.api.scan._today", lambda: anchor)
    insert_new_rows(
        [_row("XLK", anchor - dt.timedelta(days=1), 651_000_000, 187.0)],
        session_factory=session_factory,
    )

    response = client.get("/api/scan/flows", params={"window": 20})
    body = response.json()
    xlk = next(s for s in body["symbols"] if s["symbol"] == "XLK")
    assert xlk["flow"] is None
    assert xlk["flow_pct"] is None
    assert xlk["message"] is not None and "history since" in xlk["message"]


def test_get_flows_computes_a_real_window_once_enough_history_exists(
    client, session_factory, monkeypatch
):
    """window=5 needs 6 paired days -- SO/NAV are flat for the first 5 and jump on the 6th,
    so the whole window's flow reduces to that one day's change (every other daily flow is 0).
    """
    anchor = dt.date(2026, 9, 10)
    monkeypatch.setattr("app.api.scan._today", lambda: anchor)
    rows = [
        _row("XLK", anchor - dt.timedelta(days=5), 100_000_000, 100.0),
        _row("XLK", anchor - dt.timedelta(days=4), 100_000_000, 100.0),
        _row("XLK", anchor - dt.timedelta(days=3), 100_000_000, 100.0),
        _row("XLK", anchor - dt.timedelta(days=2), 100_000_000, 100.0),
        _row("XLK", anchor - dt.timedelta(days=1), 100_000_000, 100.0),
        _row("XLK", anchor, 110_000_000, 110.0),
    ]
    insert_new_rows(rows, session_factory=session_factory)

    response = client.get("/api/scan/flows", params={"window": 5})
    body = response.json()
    xlk = next(s for s in body["symbols"] if s["symbol"] == "XLK")
    # flow_5 = (110e6 - 100e6) * 110 = 1.1e9 (every other daily flow in the window is 0);
    # flow_pct_5 = 1.1e9 / (100e6 * 100) = 0.11
    assert xlk["flow"] == pytest.approx(1.1e9)
    assert xlk["flow_pct"] == pytest.approx(0.11)
    assert xlk["message"] is None


def test_get_flows_sources_reports_the_newest_stored_as_of_date_per_family(client, session_factory):
    insert_new_rows(
        [
            _row("XLK", dt.date(2026, 9, 8), 651_000_000, 187.0, source="spdr-xlsx"),
            _row("IWM", dt.date(2026, 9, 9), 269_850_000, None, source="ishares-productpage"),
        ],
        session_factory=session_factory,
    )

    response = client.get("/api/scan/flows")
    body = response.json()
    sources = {s["family"]: s["last_as_of_date"] for s in body["sources"]}
    assert sources["spdr"] == "2026-09-08"
    assert sources["ishares"] == "2026-09-09"
