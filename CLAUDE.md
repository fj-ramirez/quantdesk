# CLAUDE.md

Personal, single-user **gamma exposure (GEX)** analysis app for SPX, SPY, QQQ, GLD and DIA
options.
Analysis and charts only — **no order routing, ever**. Python/FastAPI backend, React/Vite
frontend, Postgres for computed results, Parquet on disk for raw chains.

## Commands

| | Backend (`backend/`) | Frontend (`frontend/`) |
|---|---|---|
| Install | `uv sync` | `npm install` |
| Run | `uv run uvicorn app.main:app --reload --port 8001` | `npm run dev` (5173) |
| Test | `uv run pytest` | `npm test` |
| Lint | `uv run ruff check .` | `npm run lint` |

Everything at once: `docker compose up` (postgres + backend + frontend). `make dev|test|lint`
wraps the same commands; `make` is optional and not installed on this Windows host.

The backend listens on **8001**, not 8000. Health: `GET /health`; capture freshness:
`GET /api/health/capture`.

## Layout

```
backend/app/
  providers/   OptionChainProvider ABC + cboe.py (default), marketdata.py
  models/      chain.py (Pydantic wire/domain types), db.py (SQLAlchemy tables)
  gex/         greeks.py, engine.py (pure math), store.py (persist), backfill.py (CLI)
  api/         routers: snapshots, health, gex, chains — all mounted under /api
  jobs/        capture, scheduler (APScheduler), calendar, catchup
  storage/     parquet.py (raw chains), repository.py (snapshot index)
frontend/src/  api/ components/ pages/ state/ theme/ mocks/
context/       detailed docs — see the index below
docs/          one-off reports (schema, validation, reviews)
```

## Invariants — do not violate without reading the linked doc

1. `app/gex/engine.py` and `greeks.py` are **pure**: no HTTP, DB, filesystem or logging.
2. The dealer sign (+calls / −puts) is applied **exactly once**, in `contract_gex`.
   `greeks.gamma()` and `OptionContract.gamma` are unsigned.
3. Open interest `None` means *unknown* → contract excluded. `0` means zero → included.
   Never collapse the two.
4. `captured_at` is tz-aware **UTC** everywhere, enforced at the DB boundary by `UTCDateTime`.
5. Postgres stores computed results and a snapshot *index*; per-contract rows live only in
   Parquet. `snapshots.parquet_path` is relative to `DATA_DIR` — always resolve it via
   `storage.parquet.resolve_snapshot_path`.
6. Provider swaps are a config change (`PROVIDER`), never an edit to callers.

## Context index

Read the file whose trigger matches; don't load them all.

| Read this | When you are… |
|---|---|
| [context/architecture.md](context/architecture.md) | orienting, tracing data flow, adding a module or provider |
| [context/gex-engine.md](context/gex-engine.md) | touching `gex/`, Greeks, sign conventions, IV policy, walls, flip point |
| [context/backend.md](context/backend.md) | editing API routes, DB models, migrations, jobs, backend tests |
| [context/frontend.md](context/frontend.md) | editing React components, queries, URL state, theming, MSW mocks |
| [context/data-and-ops.md](context/data-and-ops.md) | dealing with capture schedule, Parquet layout, env vars, Docker, CI |
| [context/workflow.md](context/workflow.md) | planning work, delegating to agents, writing commits, picking the next task |

Reference documents (long, load deliberately):

| Document | Contents |
|---|---|
| [PLAN.md](PLAN.md) | architecture decisions, data-source survey with costs, phase roadmap |
| [TASKS.md](TASKS.md) | the numbered task list agents execute (T00–T37); status of every phase |
| [docs/schema.md](docs/schema.md) | normalized chain schema: the five conventions, OCC parsing, settlement |
| [docs/validation.md](docs/validation.md) | engine validated against public vendor GEX figures; every difference attributed |
| [docs/supervision-report.md](docs/supervision-report.md) | retrospective on the agent-delegated build |
| [docs/state-review-2026-09-05.md](docs/state-review-2026-09-05.md) | current known gaps and the prioritized fix list |
