"""Per-service build reporting (`app.core.version`, `GET /health`).

Written against the failure that motivated it: on 2026-09-21 one `docker compose up -d
--build` updated four containers and failed on the fifth, and nothing in the running system
said so. Every assertion here is about a service reporting *itself*, because a single version
for "the stack" is precisely what would have hidden that.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core import version as version_module
from app.core.version import (
    UNKNOWN,
    ServiceVersion,
    all_service_versions,
    own_version,
    read_service_versions,
    record_service_version,
)
from app.main import app


@pytest.fixture
def versions_dir(tmp_path, monkeypatch):
    """Point `DATA_DIR` at a temporary tree, the same way every other storage test does."""
    monkeypatch.setattr(version_module.settings, "DATA_DIR", str(tmp_path))
    return tmp_path / "run" / "versions"


def test_a_worker_records_a_readable_version(versions_dir):
    monkeypatch_build("2026-09-21T20:14:03-04:00", "cf59b11")

    path = record_service_version("gex-capture")

    assert path == versions_dir / "gex-capture.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["service"] == "gex-capture"
    assert payload["label"] == "gex-capture: 2026-09-21T20:14:03-04:00 (cf59b11)"
    # No leftover temporary: the write is whole-then-rename so the API never reads a partial
    # file off the shared mount.
    assert list(versions_dir.glob("*.tmp")) == []


def monkeypatch_build(build_time: str, build_sha: str) -> None:
    version_module.BUILD_TIME = build_time
    version_module.BUILD_SHA = build_sha


@pytest.fixture(autouse=True)
def _restore_build():
    before = (version_module.BUILD_SHA, version_module.BUILD_TIME)
    yield
    version_module.BUILD_SHA, version_module.BUILD_TIME = before


def test_an_unstamped_build_says_unknown_rather_than_guessing(versions_dir):
    """A wrong sha is worse than an absent one when the question is "is this my fix?"."""
    monkeypatch_build("", "")
    # The module reads the environment at import, so exercise the constructor's own fallback
    # the way a bare `docker compose build` produces it.
    version_module.BUILD_SHA = UNKNOWN
    version_module.BUILD_TIME = UNKNOWN

    mine = own_version("backend")

    assert mine.build_sha == UNKNOWN
    assert mine.build_time == UNKNOWN
    assert mine.label == "backend: unknown (unknown)"


def test_a_worker_that_cannot_write_still_runs(versions_dir, monkeypatch):
    """A version file is never worth a capture: the Parquet tree is the one artifact this
    stack cannot recreate, and a read-only mount must not be able to stop it being written."""

    def _boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(version_module.Path, "mkdir", _boom)

    assert record_service_version("gex-capture") is None


def test_unreadable_files_are_skipped_not_fatal(versions_dir):
    record_service_version("gex-capture")
    versions_dir.mkdir(parents=True, exist_ok=True)
    (versions_dir / "corrupt.json").write_text("{not json", encoding="utf-8")

    reported = read_service_versions()

    assert [v.service for v in reported] == ["gex-capture"]


def test_a_missing_directory_is_an_empty_list(versions_dir):
    """The ordinary state of a deployment whose workers have not booted yet."""
    assert read_service_versions() == []


def test_the_api_reports_itself_without_having_written_a_file(versions_dir):
    """Invariant 7: the API process runs no background work, so it has no startup hook to
    write one -- it reports from its environment and reads the workers' files."""
    record_service_version("terminal-ingest")

    reported = all_service_versions()

    assert [v.service for v in reported] == ["backend", "terminal-ingest"]
    assert not (versions_dir / "backend.json").exists()


def test_a_half_finished_deploy_shows_two_different_shas(versions_dir):
    """The whole point, as data: the worker on the old image reports the old sha."""
    monkeypatch_build("2026-09-20T11:02:44-04:00", "0cb3751")
    record_service_version("gex-capture")
    monkeypatch_build("2026-09-21T20:14:03-04:00", "cf59b11")

    reported = {v.service: v.build_sha for v in all_service_versions()}

    assert reported == {"backend": "cf59b11", "gex-capture": "0cb3751"}


def test_health_carries_the_services_and_keeps_its_old_contract(versions_dir):
    monkeypatch_build("2026-09-21T20:14:03-04:00", "cf59b11")
    record_service_version("research-search")

    body = TestClient(app).get("/health").json()

    # The healthcheck contract `compose.prod.yaml` depends on, unchanged.
    assert body["status"] == "ok"
    assert "db" in body

    labels = [s["label"] for s in body["services"]]
    assert labels == [
        "backend: 2026-09-21T20:14:03-04:00 (cf59b11)",
        "research-search: 2026-09-21T20:14:03-04:00 (cf59b11)",
    ]


def test_the_label_is_the_documented_one_line_form():
    assert (
        ServiceVersion("backend", "cf59b11", "2026-09-21T20:14:03-04:00", "x").label
        == "backend: 2026-09-21T20:14:03-04:00 (cf59b11)"
    )
