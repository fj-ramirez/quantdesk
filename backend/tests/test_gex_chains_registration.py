"""T11 wiring checks on the real `app.main.app`: `/docs` renders, the routers are actually
mounted under `/api/gex`, and CORS is open to the Vite dev origin. Deliberately separate from
`tests/test_main.py` (which owns what the API process does and does not start) so this file
has no merge-conflict surface with whichever agent touches that one.

T75: `_client` used to stub `app.main.startup_catchup_job` and cancel the lifespan's catch-up
task afterwards. Neither exists any more -- the API process runs no background work at all --
so the helper is a bare `TestClient`. The asserted paths gained the `/gex` module segment;
nothing else about these assertions changed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module


def _client() -> TestClient:
    return TestClient(main_module.app)


def test_docs_renders():
    with _client() as client:
        response = client.get("/docs")
        assert response.status_code == 200


def test_openapi_lists_all_four_t11_routes():
    with _client() as client:
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/gex/gex/{underlying}/latest" in paths
        assert "/api/gex/gex/{underlying}/snapshots/{snapshot_id}" in paths
        assert "/api/gex/gex/{underlying}/levels/history" in paths
        assert "/api/gex/chains/{underlying}/latest" in paths
        # OpenAPI tags: routes are grouped so /docs shows them as their own sections.
        assert schema["paths"]["/api/gex/gex/{underlying}/latest"]["get"]["tags"] == ["gex"]
        assert schema["paths"]["/api/gex/chains/{underlying}/latest"]["get"]["tags"] == ["chains"]


def test_cors_allows_the_vite_dev_origin():
    with _client() as client:
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_preflight_for_a_gex_route():
    with _client() as client:
        response = client.options(
            "/api/gex/gex/SPX/latest",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_an_unlisted_origin():
    with _client() as client:
        response = client.get("/health", headers={"Origin": "http://evil.example"})
        assert response.status_code == 200  # not a same-origin request, still succeeds...
        assert "access-control-allow-origin" not in response.headers  # ...but not CORS-exposed
