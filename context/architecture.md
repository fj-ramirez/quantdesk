# Architecture

## Shape, since T75

This repository is a **module host**: one FastAPI process, one React app, one Postgres, one
compose stack, with three modules underneath: `gex`, `research` (EdgeLab, T77) and `terminal`
(xactx, T79), each with its own Postgres schema. Read
`plans/quantdesk/README.md` for the model and `plans/quantdesk/00-monorepo-skeleton.md` for
what T75 moved.

Two rules from that task are load-bearing everywhere below:

- **Modules compose, they do not register themselves.** `app/main.py` imports each module's
  `router.py` by name; `frontend/src/App.tsx` imports each module's `routes.tsx`. A missing
  module is an import error at boot, not a silently absent route.
- **The split is request path vs. background work, not module vs. module.** The API process
  starts nothing. Anything clock-bound is a worker in `app/workers/` with its own container:
  `gex-capture`, `research-search`, `terminal-ingest` and `capture-watch` (T104, which watches
  the capture and alerts on a universe-wide gap).

## The one-paragraph version

A scheduled job fetches an option chain from a provider, normalizes it into a
`ChainSnapshot`, writes the raw contracts to a Parquet file, indexes that file in Postgres,
and immediately computes GEX levels from it into two more Postgres tables. The read API
serves those precomputed rows. The React dashboard is a thin client over that API.

## Data flow

```
Cboe delayed JSON  ──providers/cboe.py──▶  ChainSnapshot (models/chain.py)
                                                │
                    jobs/capture.capture_snapshot│
                                                ├─▶ storage/parquet.write_snapshot  → data/chains/…
                                                ├─▶ storage/repository.add          → snapshots row
                                                └─▶ gex/store.compute_and_store
                                                          │ gex/engine.compute_all
                                                          └─▶ gex_levels, gex_by_strike,
                                                              gex_by_expiry (T101)
                                                                     │
                                              modules/gex/api/gex.py │ api/chains.py
                                                                     ▼
                                  frontend/src/modules/gex/api → modules/gex/pages/Dashboard
```

## Layer rules

- **`providers/`** — the only place that knows a vendor's wire format. `OptionChainProvider`
  is an ABC with `fetch_chain(symbol) -> ChainSnapshot`, `name`, `delayed_minutes`.
  `cboe.py` is the default (`PROVIDER=cboe`); `marketdata.py` is the fallback and requires
  `MARKETDATA_TOKEN`. Adding a source means one new class and a config value — never a change
  to a caller. `thetadata.py` (T26) serves history through a one-session provider that goes
  through `capture_snapshot` like any live capture; it joins the `PROVIDER` registry in T126.
  Ownership convention: a provider passed *into* a function is left open for the caller to
  close; one constructed internally is closed before returning.
- **`models/chain.py`** — Pydantic domain types (`OptionContract`, `ChainSnapshot`, `Right`,
  `Settlement`, `Underlying`, `parse_occ_symbol`). Validators enforce the invariants, so
  everything downstream can assume them. See `docs/schema.md`.
- **`models/db.py`** — SQLAlchemy tables only. Uses portable core types so the identical
  models and migrations run on SQLite (tests) and Postgres (production).
- **`gex/`** — pure math (`greeks.py`, `engine.py`) plus a thin persistence seam
  (`store.py`) and a CLI (`backfill.py`). The purity of `engine.py` is what makes it reusable
  from the capture job, the API and a future backtest loop.
- **`api/`** — FastAPI routers, composed by the module's `router.py` into one
  `APIRouter(prefix="/gex")` that `main.py` mounts at `/api`. It composes ten routers, each with its own sub-prefix
  (`snapshots`, `health`, `gex`, `chains`, `report`, `bars`, `scan`, `symbols`, `decisions`,
  `stream` — see `modules/gex/router.py`). Routes read stored rows; they do not recompute
  the engine.
- **`jobs/`** — APScheduler wiring and the capture orchestration. Since T75 the only caller of
  `scheduler.start()` is `app/workers/gex_capture.py`, in its own container;
  `build_scheduler()` stays separate from starting it so tests can inspect registered jobs
  without starting anything.
- **`storage/`** — Parquet read/write and the `SnapshotRepository` over the index table.

## Storage split, and why

Raw per-contract rows are large (a full SPX chain is ~28k contracts) and are only ever read
whole, by snapshot. That is a columnar-file workload, not a relational one, so they live in
Parquet on disk under `DATA_DIR`. Postgres holds what needs to be queried by range, filtered
and joined: the snapshot index (`snapshots`), the headline levels (`gex_levels`) and the
per-strike series (`gex_by_strike`). Timescale is not warranted at this volume.

Consequence: **`snapshots.parquet_path` is the join between the two halves.** It is stored
relative to `DATA_DIR` with posix separators so the index survives the data directory moving
hosts. Write side: `storage.parquet.to_data_dir_relative_path`. Read side:
`storage.parquet.resolve_snapshot_path` — the only place that convention is decoded.

## Deployment shape

Seven services: `postgres:16`, `backend` (the API — runs `alembic upgrade head`, then uvicorn),
`frontend`, and four workers — `gex-capture` (the scheduler and startup catch-up),
`research-search`, `terminal-ingest` and `capture-watch`. The workers use the same Dockerfile and
target as `backend` with a different command and their own image *tag* — two building services
cannot share one tag without racing the export. `compose.yaml` is the shared base;
`compose.override.yaml` (loaded automatically) adds the dev bind mounts, published ports and
hot reload; `compose.prod.yaml` is the explicit opt-in that hardens it. The backend container
overrides `DATABASE_URL` to the `postgres` hostname and `DATA_DIR` to `/data`, and the workers
share that environment through a YAML anchor so the two cannot drift; `.env.example` uses
`localhost` and `./data` for running on the host directly.

CORS allows exactly one origin, `http://localhost:5173`. Single-user app, no auth, no
cookies — a narrow allowlist is simpler than wildcarding and just as safe.
