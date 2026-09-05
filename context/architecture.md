# Architecture

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
                                                          └─▶ gex_levels, gex_by_strike
                                                                     │
                                                          api/gex.py │ api/chains.py
                                                                     ▼
                                                    frontend/src/api → pages/Dashboard
```

## Layer rules

- **`providers/`** — the only place that knows a vendor's wire format. `OptionChainProvider`
  is an ABC with `fetch_chain(symbol) -> ChainSnapshot`, `name`, `delayed_minutes`.
  `cboe.py` is the default (`PROVIDER=cboe`); `marketdata.py` is the fallback and requires
  `MARKETDATA_TOKEN`. Adding a source (Tradier, ThetaData in later phases) means one new
  class and a config value — never a change to a caller.
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
- **`api/`** — FastAPI routers, all included with `prefix="/api"` in `main.py`, each with its
  own sub-prefix (`/snapshots`, `/health`, `/gex`, `/chains`). Routes read stored rows; they
  do not recompute the engine.
- **`jobs/`** — APScheduler wiring and the capture orchestration. `main.py`'s lifespan is the
  only place `scheduler.start()` is called; `build_scheduler()` is separate so tests can
  inspect registered jobs without starting anything.
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

`docker-compose.yml` runs three services: `postgres:16` (named volume `pgdata`), `backend`
(bind-mounts `./backend` and `./data`), `frontend` (bind-mounts `./frontend`). The backend
container overrides `DATABASE_URL` to the `postgres` hostname and `DATA_DIR` to `/data`;
`.env.example` uses `localhost` and `./data` for running on the host directly.

CORS allows exactly one origin, `http://localhost:5173`. Single-user app, no auth, no
cookies — a narrow allowlist is simpler than wildcarding and just as safe.
