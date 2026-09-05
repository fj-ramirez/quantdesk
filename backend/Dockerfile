FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project

COPY . .
RUN uv sync

EXPOSE 8001

# Migrate before serving. Compose waits for postgres to be healthy, and alembic is
# idempotent, so this is safe on every start -- without it a fresh volume yields an empty
# schema and every request 500s on "relation snapshots does not exist". Single-user local
# app: auto-migrating on boot is the right trade here, unlike a shared deployment.
CMD ["sh", "-c", "uv run alembic upgrade head && exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8001"]
