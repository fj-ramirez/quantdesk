# GEX Trading App

Personal, single-user gamma exposure (GEX) analysis app for SPX, SPY, and QQQ options.
Analysis and charts only — no order routing. See `PLAN.md` for architecture and `TASKS.md`
for the build plan.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy, Postgres, managed with `uv`.
- Frontend: React + TypeScript + Vite, TanStack Query, ECharts, Lightweight Charts.
- Storage: Postgres for computed results, Parquet files on disk for raw option chain
  snapshots.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (manages Python 3.12 automatically)
- Node.js 20+ and npm
- Docker Desktop (for `docker compose up`)

`make` is not required — a `Makefile` is provided for CI/Docker convenience, but every
target below also has the raw command listed, which is what you need on a machine
without `make` installed (e.g. plain Windows without a Unix toolchain).

## Quick start (Docker)

```
cp .env.example .env
docker compose up
```

- Backend health check: http://localhost:8001/health
- Frontend dev server: http://localhost:5173

Bring it down with `docker compose down` (add `-v` to also drop the Postgres volume).

## Running locally without Docker

### Backend

```
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8001
```

Serves on http://localhost:8001, health check at `/health`. Config is read from
`backend/.env` (copy `.env.example` there, or set env vars directly) via
`app/config.py`. Keys: `DATABASE_URL`, `DATA_DIR`, `PROVIDER`, `SYMBOLS`, `TZ`.

### Frontend

```
cd frontend
npm install
npm run dev
```

Serves on http://localhost:5173.

## Tests

```
cd backend && uv run pytest
cd frontend && npm test
```

Or, via Makefile (if you have `make`): `make test`.

## Lint

```
cd backend && uv run ruff check .
cd frontend && npm run lint
```

Or: `make lint`.

## Dev (Docker, all services)

```
docker compose up
```

Or: `make dev`.

## Project layout

```
backend/app/
  providers/   option chain provider interface + implementations
  models/      SQLAlchemy models
  gex/         GEX math engine (pure functions)
  api/         FastAPI routers
  jobs/        APScheduler jobs
  storage/     Parquet read/write
backend/tests/
frontend/src/
  api/         TanStack Query hooks / API client
  components/  chart and panel components
  pages/       route-level pages
```

## Notes

- `DATABASE_URL` differs between contexts: `.env.example` points at `localhost` for
  running the backend directly on the host; `docker-compose.yml` overrides it to the
  `postgres` service hostname when running inside Docker.
- `DATA_DIR` is a bind mount (`./data`) so captured Parquet snapshots survive container
  restarts and are inspectable from the host.
