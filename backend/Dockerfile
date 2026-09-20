# Two targets from one file, so dev and prod cannot drift:
#
#   --target dev      what compose.override.yaml builds. Dev dependencies, uvicorn --reload,
#                     source arriving via a bind mount rather than from the image.
#   --target runtime  the default and what ships. No dev dependencies, non-root, no reloader,
#                     source baked in so a deploy is an image you can roll back to rather
#                     than "whatever happened to be on disk".

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # uv defaults to hardlinking from its cache into the venv, which warns and falls back on
    # every build because the cache and /app are on different layers. Copying is what it ends
    # up doing anyway; asking for it directly just removes the warning.
    UV_LINK_MODE=copy

# Dependency manifests only, so the expensive resolve+install layer is reused whenever
# application code changes but the lockfile does not.
COPY pyproject.toml uv.lock ./


# --- dev -----------------------------------------------------------------------------------
FROM base AS dev

# Dev dependencies included: pytest and ruff are run inside this container.
#
# The cache mount keeps uv's download cache on the builder rather than in the image layer.
# Without it the cache is written inside the RUN and sealed into the layer, where it is an
# exact byte-for-byte duplicate of the virtualenv it just populated -- measured at 435 MB of
# each on this project, so nearly half the image is a copy of the other half.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

COPY . .

EXPOSE 8001

# Migrate before serving. Compose waits for postgres to be healthy, and alembic is
# idempotent, so this is safe on every start -- without it a fresh volume yields an empty
# schema and every request 500s on "relation snapshots does not exist". Single-user local
# app: auto-migrating on boot is the right trade here, unlike a shared deployment.
CMD ["sh", "-c", "uv run alembic upgrade head && uv run python -m app.core.ro_role && exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload --reload-dir app"]


# --- runtime -------------------------------------------------------------------------------
FROM base AS runtime

# `--no-dev` omits the [dependency-groups] dev group in pyproject.toml (pytest, pytest-asyncio,
# ruff). `--frozen` refuses to re-resolve, so the image is built from uv.lock exactly as
# committed rather than from whatever is newest on the day it is built.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Put the virtualenv first on PATH so `alembic`, `uvicorn` and `python` resolve to it directly.
# The binaries are invoked rather than `uv run` deliberately: `uv run` re-validates and may
# write to the environment, which the non-root user below cannot do.
#
# This also has to survive the anonymous-volume trick the dev stage relies on. In dev, compose
# mounts a volume over /app/.venv so the host's Windows-built venv cannot shadow the
# container's; the production image has no bind mount and no anonymous volume, so the venv
# baked in here is the one that runs. Nothing about this stage depends on that masking.
ENV PATH="/app/.venv/bin:$PATH"

COPY . .

# Non-root. Fixed uid/gid rather than whatever the base image hands out, because /data is a
# bind mount from the host and the host directory's ownership has to match something known:
#     sudo chown -R 10001:10001 /srv/docker/gex/data
# The app writes the Parquet tree there; everything else in the image is read-only to it.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app \
    && mkdir -p /data \
    && chown -R app:app /data

USER app

EXPOSE 8001

# Same migrate-then-serve contract as dev, without the reloader (which watches the filesystem
# and costs CPU for nothing when the source cannot change) and without `uv run`.
#
# T76 adds the middle step. `alembic upgrade head` creates `quantdesk_ro` and its grants but
# deliberately sets no password -- privileges are versioned schema state, a secret is not --
# so `app.core.ro_role` applies LOGIN and `QUANTDESK_RO_PASSWORD` here, on every boot. That
# makes rotating it an env edit plus a restart rather than a new migration. It is a no-op when
# the variable is unset and it exits 0 even when it fails, so it can never gate uvicorn.
CMD ["sh", "-c", "alembic upgrade head && python -m app.core.ro_role && exec uvicorn app.main:app --host 0.0.0.0 --port 8001"]
