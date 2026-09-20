# CLAUDE.md

Personal, single-user market-analysis desk — three modules: **gex** (gamma exposure for SPX,
SPY, QQQ, GLD and DIA options), **research** (EdgeLab — automated trading-edge search) and
**terminal** (xactx — a cross-asset workstation that renders the world as of any past moment).
Analysis and charts only — **no order routing, ever**. Python/FastAPI backend, React/Vite
frontend, Postgres for computed results, Parquet on disk for raw chains and OHLCV.

## Commands

| | Backend (`backend/`) | Frontend (`frontend/`) |
|---|---|---|
| Install | `uv sync` | `npm install` |
| Run | `uv run uvicorn app.main:app --reload --port 8001` | `npm run dev` (5173) |
| Test | `uv run pytest` | `npm test` |
| Lint | `uv run ruff check .` | `npm run lint` |

Run one research cycle by hand (needs a reachable Postgres — there is no SQLite fallback):
`uv run python -m app.modules.research.nightly --trials 50 --no-update`.

Everything at once: `docker compose up` (postgres + backend + frontend + two workers) -- this reads
`compose.yaml` **plus** `compose.override.yaml`, which is what supplies the dev bind mounts,
hot reload and published ports. Production is the explicit opt-in and never loads the
override: `docker compose -f compose.yaml -f compose.prod.yaml up -d`. See the README's
"Deploying to the homeserver". `make dev|prod|test|lint` wraps the same commands; `make` is
optional and not installed on this Windows host.

The backend listens on **8001**, not 8000. Health: `GET /health` (root level, unprefixed --
it is the container healthcheck's contract); capture freshness: `GET /api/gex/health/capture`.

Since T75 this repo is a **module host**: one API process, one React app, one compose stack,
with GEX as `modules/gex` on both sides and every GEX URL carrying a `/gex` segment
(`/dashboard` -> `/gex/dashboard`, `/api/snapshots` -> `/api/gex/snapshots`). `/` is the module
launcher. Scheduled work runs in its own container per module (`gex-capture`, `research-search`); **the
API process starts no background work at all.** See `plans/quantdesk/README.md`.

## Layout

```
backend/app/
  core/        config.py (settings), db.py (engine + session factory), schemas.py (schema names)
  main.py      mounts each module's router under /api; no lifespan, starts nothing
  workers/     gex_capture.py, research_search.py, terminal_ingest.py — one container each
  config/      research.yaml — EdgeLab's search budget and cost model (T77)
  scripts/     migrate_registry.py, migrate_xactx.py — one-shot data imports (T77, T79)
  mcp/         the read-only MCP connector over all three schemas (T82)
  modules/terminal/   xactx, ported in T79; its screens are T80
    api/         board, regime, edges, policy, brief, series — every one takes `as_of`
    store/db.py  the ONLY module that knows the engine — a DuckDB-shaped facade over psycopg
    tables.py    the six tables (named tables.py, not models/, because xactx owns models.py)
    analytics/ adapters/ brief.py graph.py policy.py derive.py — the science, carried over
    cli.py       a debugging side-door; the product is the screen
  modules/research/
    router.py    APIRouter(prefix="/research") — leaderboard, trials, paper, status
    storage/repository.py  read queries + the noise ceiling (never computed client-side)
  modules/gex/
    router.py    APIRouter(prefix="/gex") composing the ten routers below
    api/         routers: snapshots, health, gex, chains, report, bars, scan, symbols,
                 decisions, stream — all under /api/gex
    providers/   OptionChainProvider ABC + cboe.py (default), marketdata.py
    models/      chain.py (Pydantic wire/domain types), db.py (Base, UTCDateTime, tables)
    gex/         greeks.py, engine.py (pure math), store.py (persist), backfill.py (CLI)
    scan/        pure scan modules: indicators, breakouts, trend, regime, rotation, decisions (T60)
    jobs/        capture, scheduler (APScheduler), calendar, catchup
    storage/     parquet.py (raw chains), repository.py (snapshot index)
  modules/research/   EdgeLab, ported in T77 from projects/research
    registry.py  the trial registry, now Postgres — same interface, no SQLite fallback
    nightly.py   run_cycle() + the CLI; the worker calls the same function
    jobs/        scheduler (cron | interval | off, no catch-up)
    models/db.py trials, paper_candidates
    backtest.py strategies.py validation.py robustness.py xs.py paper.py report.py data.py
                 — the science, carried over essentially unchanged
frontend/src/
  shell/         AppFrame, SideRail, CommandPalette, navConfig, Launcher
  modules/gex/       routes.tsx + api/ components/ pages/ state/ mocks/
  modules/research/  leaderboard + paper watchlist, same shape (T78)
  modules/terminal/  change board, regime, transmission graph, policy path, brief (T80)
                 the as-of control lives in the frame, as URL state
  mocks/         composes every module's MSW handlers into one server/worker
  lib/ theme/ components/ui/   shared by every module — formatting, time, theming, primitives,
                 and lib/http.ts (base URL, ApiError, apiFetch)
context/       detailed docs — see the index below
docs/          one-off reports (schema, validation, reviews)
plans/quantdesk/  the module-host initiative (T75–T82)
```

## Invariants — do not violate without reading the linked doc

1. `app/modules/gex/gex/engine.py` and `greeks.py` are **pure**: no HTTP, DB, filesystem or logging.
2. The dealer sign (+calls / −puts) is applied **exactly once**, in `contract_gex`.
   `greeks.gamma()` and `OptionContract.gamma` are unsigned.
3. Open interest `None` means *unknown* → contract excluded. `0` means zero → included.
   Never collapse the two.
4. `captured_at` is tz-aware **UTC** everywhere, enforced at the DB boundary by `UTCDateTime`.
5. Postgres stores computed results and a snapshot *index*; per-contract rows live only in
   Parquet. `snapshots.parquet_path` is relative to `DATA_DIR` — always resolve it via
   `storage.parquet.resolve_snapshot_path`.
6. Provider swaps are a config change (`PROVIDER`), never an edit to callers.
7. (T75) The API process runs **no background work**. `app/main.py` has no lifespan; anything
   clock-bound belongs in `app/workers/` with its own container. Two schedulers means every
   capture fires twice.
8. (T76) Every module's tables live in **its own schema** (`gex.`, `research.`, `terminal.`),
   declared once on the module's `Base` via `MetaData(schema=...)` — never per model. `public`
   holds nothing but `alembic_version`. Postgres connections pin `search_path` to `public`, so
   a table is found because it was named, not because `$user` happened to match a schema.
9. (T77) EdgeLab's honesty rules are the product and travel with it: the noise ceiling, the
   doubled-cost gate, the walk-forward gate and the futures roll-gap caveat. A leaderboard that
   drops the noise ceiling is worse than none, because it looks authoritative. And there is
   **one** trial registry — `Registry` raises rather than falling back to a local SQLite file,
   because two writers against two stores fork the history with nothing going red.
10. (T79) The terminal module's **point-in-time rule**: a revision *adds a row* and never
    overwrites one. `as_of` is part of `terminal.observations`' primary key, and `as_of_basis`
    records per row how that `as_of` was established — the per-row column is the authority, not
    the series' dominant basis. Anything that makes `as_of` updatable (an upsert, a
    "correction", a dedupe) destroys the only thing this module has that a price feed does not.

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
| [context/mcp-connector.md](context/mcp-connector.md) | touching the MCP server, its tools, or the read-only role it uses |

Reference documents (long, load deliberately):

| Document | Contents |
|---|---|
| [PLAN.md](PLAN.md) | architecture decisions, data-source survey with costs, phase roadmap |
| [TASKS.md](TASKS.md) | the numbered task list agents execute (T00–T37); status of every phase |
| [docs/schema.md](docs/schema.md) | normalized chain schema: the five conventions, OCC parsing, settlement |
| [docs/validation.md](docs/validation.md) | engine validated against public vendor GEX figures; every difference attributed |
| [docs/supervision-report.md](docs/supervision-report.md) | retrospective on the agent-delegated build |
| [docs/state-review-2026-09-05.md](docs/state-review-2026-09-05.md) | current known gaps and the prioritized fix list |
| [plans/README.md](plans/README.md) | per-tool design plans for multi-task initiatives; `continuation/` T42–T56, `ui-ux-refresh/` T62–T69, `continuous-feed/` T70–T72 (and the re-specs of T18–T23, T32) |
