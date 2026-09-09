"""Tests for `GET /api/symbols` (T47) -- the core/extended universe split at the wire.

Entirely offline and trivial on purpose: `app/api/symbols.py` does no I/O, so these tests only
have to pin the response shape against `settings`, not build any fixture infrastructure.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.symbols import router
from app.config import settings


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_symbols_returns_core_and_extended_lists():
    response = _client().get("/api/symbols")
    assert response.status_code == 200
    body = response.json()
    assert body["core"] == settings.symbols
    assert body["extended"] == settings.extended_symbols


def test_symbols_core_and_extended_are_disjoint():
    """The whole point of the split (T47 brief): a reader must be able to tell a core
    underlying from an extended one. That's only meaningful if the two lists never overlap."""
    response = _client().get("/api/symbols")
    body = response.json()
    assert set(body["core"]).isdisjoint(set(body["extended"]))


def test_symbols_core_is_exactly_the_five_p0_symbols():
    """SYMBOLS (the 16:20 EOD job's input) is untouched by T47 -- pinned here so a future
    change to EXTENDED_SYMBOLS can never accidentally widen this one too."""
    response = _client().get("/api/symbols")
    body = response.json()
    assert body["core"] == ["SPX", "SPY", "QQQ", "GLD", "DIA"]


def test_symbols_extended_includes_a_verified_sector_etf():
    response = _client().get("/api/symbols")
    body = response.json()
    assert "XLK" in body["extended"]
