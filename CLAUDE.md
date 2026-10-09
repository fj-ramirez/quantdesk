# CLAUDE.md

Personal, single-user market-analysis desk — three modules: **gex** (gamma exposure for SPX,
SPY, QQQ, GLD and DIA options), **research** (EdgeLab — automated trading-edge search) and
**terminal** (xactx — a cross-asset workstation that renders the world as of any past moment).
Analysis and charts, plus **one** order path: the `executor` worker (T151), which trades a
candidate's frozen spec on the configured MT5 account and nothing else (invariant 11).
Python/FastAPI backend, React/Vite frontend, Postgres for computed results, Parquet on disk for
raw chains and OHLCV.

## Commands

| | Backend (`backend/`) | Frontend (`frontend/`) |
|---|---|---|
| Install | `uv sync` | `npm install` |
| Run | `uv run uvicorn app.main:app --reload --port 8001` | `npm run dev` (5173) |
| Test | `uv run pytest` | `npm test` |
| Lint | `uv run ruff check .` | `npm run lint` |
| Build | — | `npm run build` |

`npm run build` is `tsc -b && vite build` and is **not** covered by `npm run lint` or
`npm test`: it type-checks test files and every project reference, so it catches type errors
the other two pass over. CI runs a Docker build of both images, so a green lint and a green
test suite still fail the deploy. Run it before pushing anything that changes a shared type.

Run one research cycle by hand (needs a reachable Postgres — there is no SQLite fallback):
`uv run python -m app.modules.research.nightly --trials 50 --no-update`.

Everything at once: `docker compose up` (postgres + backend + frontend + four workers; the
broker pair needs `--profile broker` and does not run on Docker Desktop) -- this reads `compose.yaml` **plus** `compose.override.yaml`, which is what supplies the dev bind mounts,
hot reload and published ports. Production is the explicit opt-in and never loads the
override: `docker compose -f compose.yaml -f compose.prod.yaml up -d`. See the README's
"Deploying to the homeserver". `make dev|prod|test|lint` wraps the same commands; `make` is
optional and not installed on this Windows host.

The backend listens on **8001**, not 8000. Health: `GET /health` (root level, unprefixed --
it is the container healthcheck's contract); capture freshness: `GET /api/gex/health/capture`.

Since T75 this repo is a **module host**: one API process, one React app, one compose stack,
with GEX as `modules/gex` on both sides and every GEX URL carrying a `/gex` segment
(`/dashboard` -> `/gex/dashboard`, `/api/snapshots` -> `/api/gex/snapshots`). `/` is the module
launcher. Scheduled work runs in its own container (`gex-capture`, `research-search`, `terminal-ingest`,
`capture-watch`, and behind the `broker` compose profile `mt5` + `broker-ingest` + `executor`); **the API
process starts no background work at all.** See `plans/quantdesk/README.md`.

## Layout

```
backend/app/
  core/        config.py (settings), db.py (engine + session factory), schemas.py (schema names)
  main.py      mounts each module's router under /api; no lifespan, starts nothing
  workers/     gex_capture.py, research_search.py, terminal_ingest.py, capture_watch.py (T104),
               broker_ingest.py (T144), executor.py (T151) — one container each
  mcp/         the read-only MCP connector over every schema in core/schemas.SCHEMAS (T82)
  modules/broker/     the Axi MT5 terminal (charter-mt5, T143–T145, T151): client.py talks
                 to the bridge in docker/mt5/; ingest.py (M1 bars, ticks, specs);
                 basis.py + basis_job.py (CFD↔desk basis, rolls, the `broker.levels` view);
                 strategies.py (frozen specs as legs + kill rules) and executor.py (the order path)
  modules/terminal/   xactx, ported in T79; its screens are T80
    api/         board.py, edges.py, calendar.py — board, regime, edges, policy, brief, series,
                 calendar (T139); all take `as_of`
    store/db.py  the ONLY module that knows the engine — a DuckDB-shaped facade over psycopg
    tables.py    the six tables (named tables.py, not models/, because xactx owns models.py)
    analytics/ adapters/ brief.py graph.py policy.py derive.py — the science, carried over
    releases.py  the economic calendar, point-in-time (T139): FOMC meetings + the weekly feed
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
    scan/        pure scan modules: indicators, breakouts, trend, regime, rotation, decisions (T60),
                 outcomes (T61), factors (T93), flows, groups, cross_asset
    jobs/        capture, scheduler (APScheduler), calendar, catchup, outage, retention, bars,
                 intraday_bars, flows, decisions, memory (T88)
    storage/     parquet.py (raw chains), repository.py (snapshot index)
  modules/research/   EdgeLab, ported in T77 from projects/research
    registry.py  the trial registry, now Postgres — same interface, no SQLite fallback
    nightly.py   run_cycle() + the CLI; the worker calls the same function
    jobs/        scheduler (cron | interval | off, no catch-up)
    models/db.py trials, paper_candidates, paper_scores (T108)
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
docs/          one-off reports (schema, validation, reviews); docs/archive/ holds frozen history
plans/         one folder per multi-task initiative — see plans/README.md
```

## Invariants — do not violate without reading the linked doc

1. `app/modules/gex/gex/engine.py` and `greeks.py` are **pure**: no HTTP, DB, filesystem or logging.
2. The dealer sign (+calls / −puts) is **attached once**, as the `sign` column `to_frame`
   builds, and every signed number multiplies by that column exactly once: `contract_gex` (via
   `_notional`), `gamma_profile` and the excluded-contract diagnostics. Nothing re-signs a
   value that is already signed, and nothing derives the sign another way.
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
8. (T76) Every module's tables live in **its own schema** (`gex.`, `research.`, `terminal.`, `broker.`),
   declared once on the module's `Base` via `MetaData(schema=...)` — never per model. `public`
   holds nothing but `alembic_version`. Postgres connections pin `search_path` to `public`, so
   a table is found because it was named, not because `$user` happened to match a schema.
   **One deliberate exception:** the terminal facade (`modules/terminal/store/db.py`) sets
   `search_path` to `terminal` on its own connections, because xactx's ported SQL is
   unqualified and was kept character for character (T79).
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
11. (T151, replacing "no order routing, ever", by the user's decision on 2026-10-09) **Orders
    go through one path only:** `app/workers/executor.py` → `modules/broker/executor.py` → the
    bridge's `order_market` / `close_position`. It trades whichever account the `mt5` terminal
    is logged into, demo or live, with no demo-only lock. The safeguards are code, not
    configuration:
    - only strategies in `modules/broker/strategies.py`, each the frozen spec of a candidate
      that passed its stage-1 gate in `docs/edges/`;
    - every leg written to `broker.order_intents` before it is sent;
    - a stop-loss on every order, enforced by the executor and again by the bridge;
    - a hard volume cap;
    - one position per strategy;
    - kill rules that pause the strategy, with only a person resuming it.

    It is off until both `EXECUTOR_ENABLED=true` and `MT5_ALLOW_TRADING=1` are set. Nothing else
    (the API, a module, a script, the MCP connector, an agent) may place, modify or close an
    order. `tests/test_broker_bridge.py` fails if a trading call appears outside the order path.

## Context index

Read the file whose trigger matches; don't load them all.

| Read this | When you are… |
|---|---|
| [context/architecture.md](context/architecture.md) | orienting, tracing data flow, adding a module or provider |
| [context/gex-engine.md](context/gex-engine.md) | touching `gex/`, Greeks, sign conventions, IV policy, walls, flip point |
| [context/backend.md](context/backend.md) | editing API routes, DB models, migrations, jobs, backend tests |
| [context/frontend.md](context/frontend.md) | editing React components, queries, URL state, theming, MSW mocks |
| [context/data-and-ops.md](context/data-and-ops.md) | dealing with capture schedule, Parquet layout, env vars, Docker, CI |
| [context/decisions.md](context/decisions.md) | about to change behaviour an earlier task settled — the one-line rules and where each was argued |
| [context/workflow.md](context/workflow.md) | planning work, delegating to agents, writing commits, picking the next task |
| [context/mcp-connector.md](context/mcp-connector.md) | touching the MCP server, its tools, or the read-only role it uses |

Reference documents (long, load deliberately):

| Document | Contents |
|---|---|
| [PLAN.md](PLAN.md) | architecture decisions, data-source survey with costs, phase roadmap |
| [TASKS.md](TASKS.md) | **open** work only — open, partial, blocked, conditional — and the next free ID |
| [docs/archive/TASKS-T00-T107.md](docs/archive/TASKS-T00-T107.md) | frozen full history T00–T107 (plus its 2026-09-22 addendum); code comments citing "TASKS.md Txx" for a finished task mean this file |
| [docs/schema.md](docs/schema.md) | normalized chain schema: the five conventions, OCC parsing, settlement |
| [docs/validation.md](docs/validation.md) | engine validated against public vendor GEX figures; every difference attributed |
| [docs/supervision-report.md](docs/supervision-report.md) | retrospective on the agent-delegated build |
| [docs/state-review-2026-09-21.md](docs/state-review-2026-09-21.md) + [its verification](docs/state-review-2026-09-21-verification.md) | the latest data-level review, and which of its inferred causes the code overturned (the 09-05 review is older history) |
| [docs/audit-2026-09-22.md](docs/audit-2026-09-22.md) | logic audit across all three modules; its findings are filed as tasks in `TASKS.md` |
| [plans/README.md](plans/README.md) | one folder per multi-task initiative, each with a *Result* heading per task — the evidence `context/decisions.md` links to |
