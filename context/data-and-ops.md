# Data and operations

## Provider

Default `PROVIDER=cboe`: the free, unofficial Cboe delayed-quotes endpoint,
`https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json`. No key, 15-minute
delay, returns strike/expiry/right/OI/IV/Greeks and spot — everything the engine needs. Index
underlyings take an underscore prefix (`_SPX.json`), ETFs do not (`SPY.json`, `QQQ.json`,
`GLD.json`, `DIA.json` — GLD and DIA added T38, verified live 2026-09-05: same bare-ticker
URL shape, P.M. settlement, and no engine change).

It is undocumented and has no SLA. Mitigations already in place: poll at most once per symbol
per 15 minutes, keep the provider abstraction so a swap is one class, and **store every
snapshot** so history accumulates from day one — the endpoint serves only "now" and can never
backfill. A capture missed is a hole in the dataset forever.

`PROVIDER=marketdata` is the fallback and requires `MARKETDATA_TOKEN` (free tier: 100
credits/day, 24 h delayed, 1 yr history). `MARKETDATA_TOKEN` defaults to empty so a default
`cboe` user is never blocked by a missing credential; the provider raises a clear,
setting-named error if constructed without it.

## Capture schedule

| When (America/New_York) | What |
|---|---|
| Mon–Fri 16:20 | EOD capture of SPX/SPY/QQQ/GLD/DIA — after the 15-min delay clears the 16:00 close |
| Mon–Fri 20:00 | safety net; no-op if 16:20 already succeeded |
| Mon–Fri every 15 min, 09:45–16:15 | intraday polling (T18), `is_eod=False` — **only when `INTRADAY_ENABLED=true`**; 27 fires a session, and unlike every other job here a missed slot can never be recovered |
| Mon–Fri 16:45 | extended sector/industry ETF capture (T47) |
| Mon–Fri 08:15 | daily bars pre-open refresh (T73) — same job, catches vendors that publish overnight; without it the six Cboe index symbols sit two sessions behind between evening runs |
| Mon–Fri 17:30 | daily bars update (T42) |
| Mon–Fri 17:45 | decision engine record-and-score (T61): writes today's opportunities, scores pending ones against new bars |
| Mon–Fri 18:30 | ETF shares-outstanding flows (T52) |
| Daily 21:00 | retention prune of intraday `gex_by_strike` detail (T32) — an hour after the safety net, daily rather than Mon–Fri because retention is a function of row age |
| every process start | `startup_catchup_job` — recovers a missed EOD without blocking boot |
| on demand | `POST /api/gex/snapshots/capture?underlying=SPX&eod=true` |

The **capture** jobs use `misfire_grace_time=None` and `coalesce=True`: a laptop closed at
16:20 is the normal case for this user, so a run that fires hours late must still fire, once.
The intraday and prune jobs deliberately do **not** — an intraday slot has nothing to rescue
(the endpoint serves only "now", so a late run adds an off-grid reading rather than recovering
the missed one), and a prune skipped tonight deletes the same rows plus a day's worth tomorrow.

Freshness monitoring: `GET /api/gex/health/capture`. "Stale" means **two or more** trading days
behind the last completed trading day — being exactly one day behind is normal for most of
any trading day, since today's EOD row does not exist until 16:20.

## A missed capture is gone permanently

**Options open interest cannot be backfilled.** The free Cboe feed serves the current book and
nothing else: there is no historical endpoint, no vendor archive within this project's cost
constraints, and no way to reconstruct what open interest was at 16:20 on a day that has
passed. A capture that did not happen is not "recoverable later" — it is a hole in the record
forever.

This is why `jobs/catchup.py` exists at all, why anything that risks the 16:20 run is a P0, and
why the September 2026 gap (09-14 to 09-18, five open sessions, the whole 28-symbol universe)
is permanent. QQQ's gamma regime flipped from −2.17bn to +4.80bn entirely inside it, across the
quarterly opex. Both endpoints are in the database; the path between them never will be.

Two things follow, and both are easy to get wrong:

- **Do not plan around recovery.** If a capture is at risk, the choice is to protect it, not to
  note it for backfilling.
- **`gex/backfill.py --recompute` is not this.** It recomputes *derived* rows — levels, the
  per-strike rollup, the expiry rollup — from Parquet files that already exist. It cannot
  create a snapshot that was never captured, and a session missing from `gex.snapshots` stays
  missing however often it runs.

`T104`'s `capture-watch` worker exists because this is unrecoverable: the only available
mitigation is noticing *today* rather than twelve days later.

## On-disk layout

```
$DATA_DIR/chains/<UNDERLYING>/<YYYY>/<MM>/<YYYYMMDD>T<HHMMSS><µµµµµµ>Z.parquet
```

UTC, chronologically sortable, filesystem-safe. `snapshots.parquet_path` stores this
**relative to `DATA_DIR`** with posix separators. Always resolve through
`storage.parquet.resolve_snapshot_path`; never join `settings.DATA_DIR` by hand.

**`DATA_DIR` is anchored to the repo root, not to the current directory.** A relative value
resolves against the repo root in `app/core/config.py`, so `./data` is the same tree whether a
command runs from `backend/` (the documented host commands) or from `/app` (the containers).
An absolute value is left untouched, which is how compose's `/data` bind mount keeps working.

This is not decorative. Before it, a host-run research cycle wrote `backend/data/research`
while the Docker worker wrote `./data/research` -- two parquet caches and two sets of reports,
both live, diverging for a day, with nothing going red. Fixed 2026-09-20; the two trees were
merged newest-wins and `backend/data/` is gone.

**Run the backend from the repo root, or from Docker -- never `uv run uvicorn` from inside
`backend/`.** `DATA_DIR=./data` resolves against the process's CWD, so a backend started from
`backend/` writes Parquet into `backend/data/` while `snapshots.parquet_path` stores the path
*relative to `DATA_DIR`*. The container, whose `DATA_DIR=/data` is bound to the repo-root
`./data`, then resolves those rows to files that are not there. Found 2026-09-11 with 13 such
rows (3 from early runs, 10 from a live T18 test); no data was lost and the files were copied
across, but the trap is silent -- the rows look fine until something opens the Parquet. This is
also the origin of the "orphaned files under `backend/data/`" item in the state review.

`data/` is gitignored. `DATA_DIR` is a bind mount in Docker (`./data` → `/data`) so captures
survive container restarts and stay inspectable from the host. Note there are currently
orphaned Parquet files under `backend/data/` from early runs — see
`docs/state-review-2026-09-05.md`.

## Environment

Copy `.env.example` → `.env` (root, for Docker) and/or `backend/.env` (for running uvicorn on
the host). Keys, all read by `app/config.py`:

| Key | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://gex:gex@localhost:5432/gex` | compose overrides host to `postgres` |
| `DATA_DIR` | `./data` | compose overrides to `/data` |
| `PROVIDER` | `cboe` | or `marketdata` |
| `SYMBOLS` | `SPX,SPY,QQQ,GLD,DIA` | `settings.symbols` splits and strips |
| `TZ` | `America/New_York` | scheduler timezone |
| `RISK_FREE_RATE` | `0.04` | annualized, continuously compounded. A parameter, never fetched |
| `DIVIDEND_YIELD` | `0.013` | continuous; builds the forward for the Greeks |
| `MARKETDATA_TOKEN` | *(empty)* | only for `PROVIDER=marketdata` |
| `INTRADAY_ENABLED` | `false` | T18. Turns on 15-minute polling. Off by default because the scheduler only fires while the process is alive and a missed slot is unrecoverable |
| `INTRADAY_STRIKE_RETENTION_DAYS` | `30` | T32. Days of `gex_by_strike` detail kept for **non-EOD** snapshots; `0` disables. EOD strike detail, `gex_levels` and Parquet are never pruned |

`Settings` uses `extra="ignore"`, so an unknown key in `.env` is silently dropped rather than
crashing boot — spell keys carefully.

**Compose does not read the root `.env` into the container.** The `backend` service declares an
explicit `environment:` block and no `env_file:`, so the root `.env` is used only for
*substitution into* `docker-compose.yml`. A setting added to `app/config.py` must also be added
to that block (as `KEY: ${KEY:-default}`) or the container will never see it. Combined with the
`extra="ignore"` rule above, a misspelled key in the root `.env` fails twice over in complete
silence -- which is exactly what happened to `INTRADAY_ENABLE` (missing `D`) on 2026-09-11.

## Docker

`docker compose up` → postgres:16 (named volume `pgdata`), backend (8001), frontend (5173).
`docker compose down -v` also drops the Postgres volume, which returns the app to the
never-captured empty state — a useful way to exercise the T37 empty states.

Docker Desktop on this Windows host needs to be started manually before compose will work.
A running container can lag the repo: rebuild (`docker compose up --build`) after backend
changes rather than assuming the bind mount covered it.

## CI

`.github/workflows/ci.yml`, on every push and PR, three parallel jobs with
cancel-in-progress concurrency:

1. **backend** — `uv sync --locked --all-groups`, `ruff check .`, `pytest`.
2. **frontend** — `npm ci`, `npm run lint`, `npm test` (Node 22).
3. **docker-build** — buildx builds both images with GHA layer caching, no push.

`uv sync --locked` means `backend/uv.lock` must be committed in step with `pyproject.toml`.

## Cost and licensing constraints

Data budget is **under $50/month** and the app is single-user (owner only), which keeps real-
time data under OPRA non-professional status (~$1.25/month, passed through by the vendor) with
no redistribution license. Phases 1–4 run entirely free on Cboe. Phase 5's real-time path is a
Tradier brokerage account ($0–10/month), confirmed available for a Dominican Republic
resident. See `PLAN.md` §1 for the full source survey and the ThetaData decision that is
deliberately deferred until after Phase 4.
