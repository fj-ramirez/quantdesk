# Data and operations

## Provider

Default `PROVIDER=cboe`: the free, unofficial Cboe delayed-quotes endpoint,
`https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json`. No key, 15-minute
delay, returns strike/expiry/right/OI/IV/Greeks and spot — everything the engine needs. Index
underlyings take an underscore prefix (`_SPX.json`), ETFs do not (`SPY.json`).

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
| Mon–Fri 16:20 | EOD capture of SPX/SPY/QQQ — after the 15-min delay clears the 16:00 close |
| Mon–Fri 20:00 | safety net; no-op if 16:20 already succeeded |
| every process start | `startup_catchup_job` — recovers a missed EOD without blocking boot |
| on demand | `POST /api/snapshots/capture?underlying=SPX&eod=true` |

Both cron jobs use `misfire_grace_time=None` and `coalesce=True`: a laptop closed at 16:20 is
the normal case for this user, so a run that fires hours late must still fire, once.

Freshness monitoring: `GET /api/health/capture`. "Stale" means **two or more** trading days
behind the last completed trading day — being exactly one day behind is normal for most of
any trading day, since today's EOD row does not exist until 16:20.

## On-disk layout

```
$DATA_DIR/chains/<UNDERLYING>/<YYYY>/<MM>/<YYYYMMDD>T<HHMMSS><µµµµµµ>Z.parquet
```

UTC, chronologically sortable, filesystem-safe. `snapshots.parquet_path` stores this
**relative to `DATA_DIR`** with posix separators. Always resolve through
`storage.parquet.resolve_snapshot_path`; never join `settings.DATA_DIR` by hand.

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
| `SYMBOLS` | `SPX,SPY,QQQ` | `settings.symbols` splits and strips |
| `TZ` | `America/New_York` | scheduler timezone |
| `RISK_FREE_RATE` | `0.04` | annualized, continuously compounded. A parameter, never fetched |
| `DIVIDEND_YIELD` | `0.013` | continuous; builds the forward for the Greeks |
| `MARKETDATA_TOKEN` | *(empty)* | only for `PROVIDER=marketdata` |

`Settings` uses `extra="ignore"`, so an unknown key in `.env` is silently dropped rather than
crashing boot — spell keys carefully.

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
