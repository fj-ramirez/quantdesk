# GEX Trading App — Agent Task List

How to use this file:

- Each task is self-contained. Give the agent the task block plus the instruction: "Read PLAN.md first. Work only inside the paths listed. Do not change the public interfaces defined in earlier tasks. Run the tests before reporting done."
- Default model is **Sonnet**. Tasks marked **Opus** involve math, architecture, or judgment where a wrong call propagates into every later task. Tasks marked **Opus (review)** are short review passes on Sonnet output.
- Run tasks in dependency order. Tasks with the same dependencies can run in parallel in separate worktrees.
- Single-user app, Dominican Republic resident, data budget under $50/month. Tradier confirmed available for this country.

Legend: `ID · Model · Depends on`

---

## Phase 0 — Scaffold

### T00 · Sonnet · —
**Repository scaffold**

Create the layout from PLAN.md section 2. Deliverables:
- `backend/pyproject.toml` using `uv`, Python 3.12, deps: fastapi, uvicorn, sqlalchemy, psycopg[binary], alembic, pydantic-settings, apscheduler, httpx, numpy, pandas, pyarrow, scipy, pytest, pytest-asyncio, ruff.
- `backend/app/__init__.py`, `backend/app/main.py` with a `/health` route, `backend/app/config.py` (pydantic-settings reading `.env`: `DATABASE_URL`, `DATA_DIR`, `PROVIDER`, `SYMBOLS=SPX,SPY,QQQ`, `TZ=America/New_York`).
- `frontend/` via `npm create vite@latest -- --template react-ts`, add `@tanstack/react-query`, `echarts`, `echarts-for-react`, `lightweight-charts`, `vitest`, `eslint`.
- `docker-compose.yml`: `postgres:16`, `backend`, `frontend`. Named volume for Postgres and a bind mount for `DATA_DIR`.
- `.env.example`, `.gitignore`, `README.md` with run instructions, `Makefile` (or `justfile`) with `dev`, `test`, `lint`.
- `git init` and an initial commit.

Acceptance: `docker compose up` serves `/health` on 8000 and the Vite app on 5173. `uv run pytest` and `npm test` pass with one placeholder test each.

---

## Phase 1 — Ingestion (EOD)

### T01 · Opus · T00
**Normalized chain schema and provider interface**

Design `backend/app/providers/base.py` and `backend/app/models/chain.py`. This is the contract every later task depends on.

- `OptionContract` (pydantic or dataclass, frozen): `occ_symbol`, `root` (e.g. `SPX`, `SPXW`, `SPY`), `underlying` (`SPX`, `SPY`, `QQQ`), `expiry` (date), `settlement` (`AM`/`PM`), `strike` (Decimal or float), `right` (`C`/`P`), `bid`, `ask`, `last`, `volume`, `open_interest`, `iv`, `delta`, `gamma`, `vega`, `theta`, `multiplier` (100), `last_trade_time` (optional).
- `ChainSnapshot`: `underlying`, `spot`, `captured_at` (tz-aware UTC), `source` (provider name), `delayed_minutes`, `contracts: list[OptionContract]`.
- `OptionChainProvider` abstract class: `name`, `async fetch_chain(underlying: str) -> ChainSnapshot`, `delayed_minutes`.
- `parse_occ_symbol(s: str) -> (root, expiry, right, strike)` handling roots of 1–6 chars, and SPX vs SPXW (SPX monthlies are AM-settled on the third Friday; SPXW are PM-settled). Document the rule for `settlement`.
- Decide and document: IV stored as decimal (0.18) not percent. (Resolved in T01, and see the corrected note under T02: Cboe's *per-contract* `iv` is already decimal and must NOT be scaled; only the index-level `iv30` is percent-like.)
- Write `backend/tests/test_occ_symbol.py` with cases: `SPX260918C00200000`, `SPXW260904P07700000`, `SPY260904C00500000`, `QQQ261218P00400000`.

Acceptance: interface documented in module docstrings; tests pass; a short `docs/schema.md` explains every field and unit.

### T02 · Sonnet · T01
**Cboe delayed-quotes provider**

Implement `backend/app/providers/cboe.py`.

- URL pattern: `https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json`, where index symbols get an underscore prefix (`_SPX`). Use a browser-like `User-Agent`. `httpx.AsyncClient` with 30 s timeout and 3 retries with backoff.
- Response shape: top-level `timestamp`, `data.current_price`, `data.options[]` each with `option` (OCC symbol), `bid`, `ask`, `iv`, `open_interest`, `volume`, `delta`, `gamma`, `vega`, `theta`, `last_trade_price`, `last_trade_time`.
- Map to `ChainSnapshot` using `parse_occ_symbol` from T01. Set `delayed_minutes=15`.
- **IV units — corrected against live data 2026-09-04, supersedes the note in T01 below.** Cboe's *per-contract* `iv` is **already a decimal fraction** (ATM SPX 2026-09-18 call quoted `iv: 0.1061`; full chain range 0.054–7.97, mean 0.243). **Do not divide it by 100.** The percent-like field is the index-level `data.iv30` (`11.242` = 11.24 %), which the schema does not carry. Map a vendor `iv` of `0.0` to `None` — the schema rejects it, since it is a sentinel, not a measurement.
- **Timestamps — Cboe mixes timezones in one payload.** The top-level `timestamp` is naive **UTC**; every `last_trade_time` is naive **America/New_York**. Both verified 2026-09-04. Attach the correct tzinfo per field; a blanket assumption is wrong for half of them.
- Save a real response for each of SPX, SPY, QQQ as fixtures in `backend/tests/fixtures/cboe/` (trim SPX to a few hundred contracts to keep the repo small, but keep at least three expiries including one SPX and one SPXW).
- Tests: parse each fixture; contract count matches; a known contract's fields map correctly; timestamp is tz-aware.

Acceptance: `uv run python -m app.providers.cboe SPX` prints spot, contract count, and expiry count from a live call.

### T03 · Sonnet · T01
**MarketData.app fallback provider**

Implement `backend/app/providers/marketdata.py` against the option chain endpoint (`https://api.marketdata.app/v1/options/chain/{symbol}/`) with token from `MARKETDATA_TOKEN`. Map to `ChainSnapshot`. Fixture-based tests. Register both providers in `backend/app/providers/__init__.py` behind a `get_provider(name)` factory reading `PROVIDER` from config.

- **Corrected against the live docs 2026-09-04.** `mode=cached` is documented as **paid-plan only** and returns `402` on Free Forever, so the "1 credit per call, 100 credits/day covers three symbols" premise above does not hold on the free tier. The free default is `mode=historical` — the prior *closed session* — which matches the 24 h latency PLAN.md §1 already lists for this source. `delayed_minutes` is therefore **1440**, not 15. This provider is a break-glass fallback for when Cboe fails, not an equivalent second source.
- MarketData.app takes **plain `SPX`** with no underscore prefix (unlike Cboe's `_SPX`), and returns both `SPX` and `SPXW` roots in one call. Its per-contract `iv` is **already decimal**, same as Cboe — do not rescale.
- **Status: built but never run against the real API** (no account). Fixtures under `backend/tests/fixtures/marketdata/` are synthesized from the documented schema and tagged with a `_provenance` field. Verify with `MARKETDATA_TOKEN=<token> uv run python -m app.providers.marketdata SPX` before relying on it.

Acceptance: switching `PROVIDER=marketdata` in `.env` makes the same CLI from T02 work.

### T04 · Sonnet · T01
**Snapshot storage: Parquet + Postgres index**

- `backend/app/storage/parquet.py`: `write_snapshot(snapshot) -> Path` under `DATA_DIR/chains/{underlying}/{YYYY}/{MM}/{captured_at_iso}.parquet`, and `read_snapshot(path) -> ChainSnapshot`. One row per contract, snapshot metadata in Parquet file metadata.
- SQLAlchemy models in `backend/app/models/db.py`: `snapshots` table (`id`, `underlying`, `captured_at`, `source`, `spot`, `contract_count`, `parquet_path`, `is_eod` bool). Alembic migration.
- `SnapshotRepository` with `add`, `latest(underlying)`, `list(underlying, start, end, eod_only)`.
- Tests using a temporary directory and a SQLite URL for the repo tests (keep the SQL portable).

Acceptance: round-trip test: fetch fixture → write → read → equal.

### T05 · Sonnet · T02, T04
**Scheduler and capture endpoint**

- `backend/app/jobs/scheduler.py`: APScheduler `AsyncIOScheduler` started on FastAPI lifespan. Job `capture_eod` runs Mon–Fri at 16:20 America/New_York for each symbol in `SYMBOLS`, marks `is_eod=True`. Skip US market holidays using a small hardcoded list for 2026–2027 in `backend/app/jobs/calendar.py` (document that it must be updated yearly).
- `POST /api/snapshots/capture?underlying=SPX&eod=false` triggers a capture immediately.
- `GET /api/snapshots?underlying=SPX&limit=30` lists snapshots.
- Structured logging (json) of each capture: symbol, contracts, spot, duration, error if any. Failures must not crash the scheduler.

Acceptance: manual capture works end to end against the live Cboe endpoint and a row appears in Postgres with a Parquet file on disk.

### T06 · Opus (review) · T02–T05
**Phase 1 review**

Review the diff for: timezone handling (all DB timestamps UTC, scheduler in NY time), IV unit consistency, SPX/SPXW merging correctness, retry behaviour, and any hidden coupling between providers and storage. Fix small issues directly; write findings for larger ones as new task entries at the end of this file.

---

## Phase 2 — GEX engine

### T07 · Opus · T01
**Greeks module**

`backend/app/gex/greeks.py`, pure NumPy, vectorized over arrays.

- Black-Scholes for SPY/QQQ (spot, strike, T, r, q, sigma) and Black-76 on the forward for SPX (index, no dividends in the option itself; use forward = spot × exp((r−q)T) with a configurable dividend yield). Functions: `d1`, `d2`, `price`, `delta`, `gamma`, `vega`, `vanna`, `charm`.
- Time to expiry in years must account for settlement: PM-settled expires at 16:00 NY, AM-settled at 09:30 NY. Provide `time_to_expiry(now, expiry, settlement)` with a floor (e.g. 1 minute) so 0DTE contracts at 15:59 do not divide by zero.
- Risk-free rate: accept as parameter; default from config `RISK_FREE_RATE=0.04`. Do not fetch it.
- Tests: compare against hand-computed reference values (put in the test file with the formula used), put-call parity, gamma symmetry between call and put at same strike, vectorized vs scalar equality, and gamma → 0 as T → large.

Acceptance: all tests pass; docstrings state every convention (annualization 365 vs 252: use calendar days / 365).

### T08 · Opus · T07
**GEX engine core**

`backend/app/gex/engine.py`, pure functions over a `ChainSnapshot` (or a DataFrame built from it), no I/O.

- `contract_gex(df, spot, use_vendor_gamma=False)`: gamma × OI × multiplier × spot² × 0.01, sign +1 calls, −1 puts. Also `abs_gex`. Recompute gamma with T07 by default; vendor gamma optional for cross-check.
- `by_strike(df, filter)`, `by_expiry(df)`. Expiry filter enum: `ALL`, `ZERO_DTE`, `THIS_WEEK`, `MONTHLY_ONLY`, `EX_ZERO_DTE`, plus an explicit expiry list.
- `gamma_profile(df, spot, grid=±10% in 0.1% steps)`: for each grid spot, recompute gamma for every contract at that spot with its own IV, sum signed dollar gamma. Return arrays (spot_grid, total_gex).
- `flip_point(profile)`: nearest sign change to current spot via linear interpolation; return `None` if no sign change in range.
- `key_levels(strike_gex)`: call wall, put wall, max absolute strike, and top-5 positive/negative strikes.
- `compute_all(snapshot, filters) -> GexResult` dataclass bundling everything, serializable to JSON.
- Tests with a synthetic 20-contract chain where the answer is known by construction, plus one test on the SPX fixture from T02 checking that net GEX is positive and within an order of magnitude of 50 B USD (the value observed on 2026-09-04).

Acceptance: `compute_all` on the full SPX fixture runs under 2 s.

### T09 · Sonnet · T08, T04
**Persist computed levels**

- Table `gex_levels`: `snapshot_id`, `filter`, `net_gex`, `call_wall`, `put_wall`, `max_abs_strike`, `flip_point`, `spot`, `computed_at`. Table `gex_by_strike`: `snapshot_id`, `filter`, `strike`, `call_gex`, `put_gex`, `net_gex`. Alembic migration.
- `compute_and_store(snapshot_id)` service, called automatically at the end of every capture in T05 for filters `ALL`, `ZERO_DTE`, `EX_ZERO_DTE`.
- Backfill CLI `uv run python -m app.gex.backfill` that computes for any snapshots lacking levels.

Acceptance: after a capture, levels rows exist; backfill is idempotent.

### T10 · Opus · T08, T02
**Validate against public GEX figures**

On a trading day, capture SPX and SPY and compare the engine's net GEX, call wall, put wall, and flip point against the free public pages of at least two vendors (e.g. Unusual Whales greek-exposure pages, SpotGamma free levels, or similar). Write `docs/validation.md` with a table: metric, ours, vendor A, vendor B, difference, and an explanation for each material difference (sign convention, expiry set, OI timing, multiplier). Adjust engine defaults only if a discrepancy is traced to a bug, and add a regression test for any fix.

Acceptance: doc committed; net GEX sign and walls agree with at least one vendor within the explained tolerances.

---

## Phase 3 — API and dashboard

### T11 · Sonnet · T09
**Read API**

FastAPI routers under `/api`:
- `GET /gex/{underlying}/latest?filter=ALL` → `GexResult` JSON (levels + by_strike + by_expiry + profile).
- `GET /gex/{underlying}/snapshots/{snapshot_id}?filter=` → same for a past snapshot.
- `GET /gex/{underlying}/levels/history?filter=&start=&end=&eod_only=true` → list of level rows.
- `GET /chains/{underlying}/latest?expiry=` → raw contracts for one expiry.
- Pydantic response models; OpenAPI tags; CORS for the Vite origin.
- Tests with the SPX fixture loaded into a temp DB.

Acceptance: `/docs` renders; all endpoints covered by tests.

### T12 · Sonnet · T00
**Frontend shell and data layer**

- App layout: top bar with symbol switcher (SPX/SPY/QQQ), expiry filter (All, 0DTE, Ex-0DTE, This week, Monthly), snapshot selector (latest or a date), theme toggle. Routes: `/` Dashboard, `/history`, `/settings`.
- `src/api/client.ts` typed fetch wrapper; TanStack Query hooks for each T11 endpoint with types generated from the OpenAPI JSON (`openapi-typescript`).
- Global state via URL search params (symbol, filter, snapshot), not a store.
- Mock server (MSW) with fixture JSON so the frontend can be built before T11 is live.

Acceptance: `npm run dev` shows the shell with mocked data; `npm test` passes.

### T13 · Sonnet · T12
**GEX by strike chart**

`GexByStrike` component using ECharts: horizontal or vertical bars, calls positive and puts negative per strike, net line overlay, current spot marked, call wall and put wall highlighted, flip point marked. Zoom to ±5% of spot by default with a range slider. Tooltip shows call, put, net GEX in $M/$B with proper formatting. Responsive.

Acceptance: renders from mock data and from the live API; Storybook not required, but a `stories/` style demo page is fine.

### T14 · Sonnet · T12
**Gamma profile chart and key levels panel**

- `GammaProfile`: ECharts line of total GEX vs. hypothetical spot, zero line, flip point marker, current spot marker, two series (All, Ex-0DTE).
- `KeyLevels`: card listing spot, net GEX, flip, call wall, put wall, max abs strike, distance from spot in points and percent, snapshot time and data delay.

Acceptance: both components render with mock and live data.

### T15 · Sonnet · T12, T11
**Price chart with levels and level history**

- `PriceChart`: Lightweight Charts candlesticks from a new endpoint `GET /api/prices/{underlying}?days=30` (add it to the backend; source daily OHLC from the Cboe delayed quote for the underlying, or from MarketData.app candles when configured). Overlay horizontal lines for call wall, put wall, flip.
- `/history` page: table and small line chart of flip, call wall, put wall over time versus close, from the T11 history endpoint.

Acceptance: history page shows at least the rows accumulated since Phase 1 went live.

### T16 · Sonnet · T11–T15
**Dashboard assembly and polish**

Compose the Dashboard page: KeyLevels on top, GexByStrike and GammaProfile side by side, PriceChart below, with responsive layout for a laptop and a phone. Loading and error states. Number formatting utility shared across components. Keyboard shortcut for symbol switching. Lighthouse performance check.

### T17 · Opus (review) · T11–T16
**Phase 3 review**

Review API/frontend contract consistency, error handling, and whether URL state fully drives the views (deep links work). Check that every chart uses the same sign convention and units. Fix small issues directly.

---

## Phase 4 — Delayed intraday

### T18 · Sonnet · T05, T09
**Intraday polling job**

Add scheduler job `capture_intraday`: every 15 minutes from 09:45 to 16:15 NY on trading days, all symbols, `is_eod=False`, compute levels after each capture. Ensure the EOD job still runs at 16:20 and that the polling cadence never exceeds one request per symbol per 15 minutes (this is the free source's informal limit). Config flag `INTRADAY_ENABLED`.

### T19 · Sonnet · T18, T11
**Server-Sent Events stream**

`GET /api/stream/{underlying}` SSE endpoint that emits an event whenever a new snapshot's levels are stored. Frontend hook `useLiveLevels` that invalidates the relevant queries on each event. Show a "last updated" indicator and a delay badge in the top bar.

### T20 · Sonnet · T18, T14
**Intraday timeline view**

Page `/intraday`: for a chosen date, a chart of flip point, call wall, put wall and spot across the session's snapshots, plus a slider to scrub the GexByStrike chart through the day's snapshots.

---

## Phase 5 — Real-time (Tradier)

### T21 · Opus · T07, T08, T18
**Real-time recompute design**

Write `docs/realtime.md` before any code: how the day's OI is frozen at the first capture, how streaming quotes update spot and per-contract IV, how IV is derived when the stream provides only prices (Newton or Brent solve against T07 pricing, with fallbacks), recompute cadence (target 2–5 s, throttled), memory layout (NumPy arrays keyed by contract index), and what happens on reconnect or gaps. Define the `StreamingProvider` interface: `subscribe(contracts)`, `async iter_quotes()`. State the subset of contracts to stream (e.g. strikes within ±7% of spot for expiries within 45 days) to stay under vendor limits.

### T22 · Sonnet · T21
**Tradier provider (REST + WebSocket)**

- REST: `GET /v1/markets/options/chains?symbol=SPX&expiration=&greeks=true` for the chain (all expirations from `/v1/markets/options/expirations`), mapped to `ChainSnapshot`. Sandbox base URL for tests, production URL from config. Token from `TRADIER_TOKEN`.
- WebSocket: create a session via `/v1/markets/events/session`, then stream quotes for the subscribed OCC symbols. Implement `StreamingProvider` from T21.
- Reconnect with backoff; heartbeat; metrics counters.
- Tests with recorded fixtures; a live smoke script that streams 30 seconds and prints message counts.

### T23 · Opus · T21, T22
**Real-time GEX loop**

Implement the design from T21 in `backend/app/gex/realtime.py`. Publish results through the T19 SSE channel with an event type `realtime`. Frontend: when a realtime stream is active, charts update in place without flicker (ECharts `setOption` with `notMerge=false`). Config flag `REALTIME_ENABLED`.

Acceptance: during market hours, the dashboard updates every few seconds and CPU stays under one core.

### T24 · Opus (review) · T21–T23
**Phase 5 review**

Review the IV solver for stability on deep OTM and 0DTE contracts, reconnect behaviour, and that frozen OI is the previous day's (not stale from two days ago on a Monday). Fix or file.

---

## Phase 6 — Research (optional)

### T25 · Opus · T09, T18
**Backtest harness**

`backend/app/research/`: load all EOD levels and next-day OHLC; compute for each day whether the next session's range stayed within call wall / put wall, distance to flip vs. realized range, and 0DTE-only vs. all-expiry level quality. Output a Markdown report and CSV. Notebook optional. Design the module so a ThetaData history loader can be added later without changing the analysis code.

### T26 · Sonnet · T25
**ThetaData history loader (only if the $80/month plan is purchased)**

Implement `backend/app/providers/thetadata.py` for historical EOD chains with OI and Greeks from the local Theta Terminal REST API, writing Parquet snapshots in the same layout as T04 so T25 works unchanged.

---

## Cross-cutting tasks (any time after T00)

### T27 · Sonnet · T00
**CI**

GitHub Actions: lint + test for backend and frontend on push, Docker build check. Cache `uv` and `npm`.

### T28 · Sonnet · T05
**Ops**

Backup script for `DATA_DIR` and a `pg_dump` cron; a `/api/health` that reports last successful capture per symbol and alerts (log at ERROR) if the EOD capture is more than one trading day old.

---

## Model assignment summary

| Model | Tasks |
|---|---|
| Opus | T01, T07, T08, T10, T21, T23, T25 |
| Opus (review) | T06, T17, T24 |
| Sonnet | T00, T02–T05, T09, T11–T16, T18–T20, T22, T26, T27, T28 |

Parallelizable groups once their dependency is done: {T02, T03, T04} after T01; {T12} alongside all of Phase 1; {T13, T14} after T12; {T27, T28} anytime.
