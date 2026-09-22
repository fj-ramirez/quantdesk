"""Which build each service is running, and how the API finds out about the others.

Motivated by a real failure on 2026-09-21: a `docker compose up -d --build` rebuilt the
backend and the three workers and **failed on the frontend**, leaving four containers on the
new commit and one on the old one. Nothing in the running system said so. Reporting a single
version for "the stack" would have hidden exactly that, so every service reports its own.

**How a service knows its own build.** `BUILD_SHA` and `BUILD_TIME` are baked in as build
args (see `backend/Dockerfile` and `scripts/deploy.sh`), because a container has no git
repository to ask. A build that did not receive them says so -- `unknown` -- rather than
guessing from a file date, since a wrong sha is worse than an absent one when the question is
"is this container running my fix?".

**How the API knows about the workers.** The workers serve no HTTP, so each one writes a
small JSON file into `DATA_DIR/run/versions/` at boot and the API reads the directory. Three
alternatives were rejected: a table would need a schema to live in (invariant 8 gives every
module its own and leaves `public` for `alembic_version` alone, and a service registry is not
a module's data); a heartbeat would be background work in the API process (invariant 7); and
inferring the workers' version from the API's own would reproduce the very failure this
exists to catch.

The file is written once per process start, so its `started_at` is also the answer to "when
did this container last restart", which is worth having next to the build it restarted into.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

__all__ = [
    "BUILD_SHA",
    "BUILD_TIME",
    "PROCESS_STARTED_AT",
    "UNKNOWN",
    "ServiceVersion",
    "all_service_versions",
    "own_version",
    "read_service_versions",
    "record_service_version",
    "versions_dir",
]

logger = logging.getLogger("app.core.version")

#: What every field reads when the build did not supply one. A literal rather than `None` so
#: it survives JSON, a log line and a UI cell without three separate empty-value conventions.
UNKNOWN = "unknown"

#: Baked in at image build time; see the module docstring.
BUILD_SHA: str = os.getenv("BUILD_SHA", "").strip() or UNKNOWN
BUILD_TIME: str = os.getenv("BUILD_TIME", "").strip() or UNKNOWN

#: When this process started, fixed at import. A module-level constant and not a startup hook:
#: the API process runs no background work (invariant 7) and import time is within a second of
#: process start for every service here. It answers "when did this container last restart",
#: which is the question that usually follows "which build is it running".
PROCESS_STARTED_AT: str = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ServiceVersion:
    """One service's build, as it reports itself."""

    service: str
    build_sha: str
    build_time: str
    started_at: str

    @property
    def label(self) -> str:
        """`backend: 2026-09-21T20:08:14Z (cf59b11)` -- the one-line form."""
        return f"{self.service}: {self.build_time} ({self.build_sha})"

    def to_dict(self) -> dict[str, str]:
        return {
            "service": self.service,
            "build_sha": self.build_sha,
            "build_time": self.build_time,
            "started_at": self.started_at,
            "label": self.label,
        }


def own_version(service: str) -> ServiceVersion:
    """This process's build, with the moment the process started.

    The name is passed in rather than read from the environment: each worker knows what it
    is, and a `SERVICE_NAME` variable would be one more thing a compose file could get wrong
    silently -- two services sharing a name would overwrite each other's file.
    """
    return ServiceVersion(
        service=service,
        build_sha=BUILD_SHA,
        build_time=BUILD_TIME,
        started_at=PROCESS_STARTED_AT,
    )


def versions_dir() -> Path:
    """`DATA_DIR/run/versions`. Under `run/` rather than beside the Parquet tree so a
    `data-push.sh` sync of captured data never carries one host's process state to another."""
    return Path(settings.DATA_DIR) / "run" / "versions"


def record_service_version(service: str) -> Path | None:
    """Write this process's build, once, at worker boot. Returns the path, or `None`.

    **Never raises.** A worker that cannot write its version file must still capture: the
    Parquet tree is the irreplaceable artifact and a read-only mount, a full disk or a
    permissions mismatch is not a reason to lose a 16:20 chain. The failure is logged and the
    service simply goes unreported, which the API renders as an absence rather than as a
    claim.

    The likely cause on a real deployment is the one this hit on its first run: the containers
    run as uid 10001 and `DATA_DIR` itself is root-owned on the host, so its *existing*
    subdirectories are writable and a *new* one cannot be created. The log line names the
    remedy rather than leaving it to be rediscovered -- it is the same one-time `chown` the
    README's deploy steps already describe for `data/`.
    """
    version = own_version(service)
    path = versions_dir() / f"{service}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written whole and then renamed: the API reads this directory on an unsynchronised
        # schedule, and a half-written file would be a parse error on a healthcheck.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(version.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # The path below is **inside the container**. The previous wording read as a host
        # path and was followed literally on 2026-09-22, creating /data/run/versions on the
        # host root -- a real directory, on the wrong filesystem, fixing nothing. DATA_DIR is
        # a bind mount, so the host location depends on where the stack lives; the docker
        # form below resolves that by itself and cannot be misapplied.
        logger.warning(
            "version: could not record %s at %s (a path INSIDE the container) -- this service "
            "will be missing from GET /health's `services`. Fix, from the stack directory on "
            "the host: `docker compose run --rm -u 0 backend sh -c 'mkdir -p %s && chown -R "
            "10001:10001 %s'`. Do not run `mkdir` on the host path directly unless you "
            "translate it through the bind mount first. Nothing else is affected.",
            service,
            path,
            path.parent,
            path.parent.parent,
            exc_info=True,
        )
        return None
    logger.info("version: %s", version.label)
    return path


def read_service_versions() -> list[ServiceVersion]:
    """Every service that has reported, sorted by name. Unreadable files are skipped.

    A missing directory is the ordinary state of a fresh deployment whose workers have not
    booted yet, not an error.
    """
    directory = versions_dir()
    out: list[ServiceVersion] = []
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        logger.warning("version: could not list %s", directory, exc_info=True)
        return out

    for file in files:
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("version: could not read %s", file, exc_info=True)
            continue
        out.append(
            ServiceVersion(
                service=str(payload.get("service") or file.stem),
                build_sha=str(payload.get("build_sha") or UNKNOWN),
                build_time=str(payload.get("build_time") or UNKNOWN),
                started_at=str(payload.get("started_at") or UNKNOWN),
            )
        )
    return out


def all_service_versions(api_service: str = "backend") -> list[ServiceVersion]:
    """The API's own build plus every worker that has reported one.

    The API reports itself from the environment rather than from a file, so that it never
    depends on having written one -- it runs no background work at all (invariant 7), and a
    startup hook just to record a version would be the thin end of exactly that wedge.
    """
    mine = own_version(api_service)
    others = [v for v in read_service_versions() if v.service != api_service]
    return [mine, *others]
