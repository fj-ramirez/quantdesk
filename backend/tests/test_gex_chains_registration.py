"""T11 wiring checks on the real `app.main.app`: `/docs` renders, the new routers are actually
mounted under `/api`, and CORS is open to the Vite dev origin. Deliberately separate from
`tests/test_main.py` (which owns the lifespan/startup-catchup wiring) so this file has no
merge-conflict surface with whichever agent touches that one.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module


def _client(monkeypatch) -> TestClient:
    async def fake_startup_catchup_job():
        return None

    monkeypatch.setattr(main_module, "startup_catchup_job", fake_startup_catchup_job)
    client = TestClient(main_module.app)
    return client


def test_docs_renders(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/docs")
        assert response.status_code == 200
        client.app.state.startup_catchup_task.cancel()


def test_openapi_lists_all_four_t11_routes(monkeypatch):
    with _client(monkeypatch) as client:
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/gex/{underlying}/latest" in paths
        assert "/api/gex/{underlying}/snapshots/{snapshot_id}" in paths
        assert "/api/gex/{underlying}/levels/history" in paths
        assert "/api/chains/{underlying}/latest" in paths
        # OpenAPI tags: routes are grouped so /docs shows them as their own sections.
        assert schema["paths"]["/api/gex/{underlying}/latest"]["get"]["tags"] == ["gex"]
        assert schema["paths"]["/api/chains/{underlying}/latest"]["get"]["tags"] == ["chains"]
        client.app.state.startup_catchup_task.cancel()


def test_cors_allows_the_vite_dev_origin(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        client.app.state.startup_catchup_task.cancel()


def test_cors_preflight_for_a_gex_route(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.options(
            "/api/gex/SPX/latest",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        client.app.state.startup_catchup_task.cancel()


def test_cors_rejects_an_unlisted_origin(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/health", headers={"Origin": "http://evil.example"})
        assert response.status_code == 200  # not a same-origin request, still succeeds...
        assert "access-control-allow-origin" not in response.headers  # ...but not CORS-exposed
        client.app.state.startup_catchup_task.cancel()
