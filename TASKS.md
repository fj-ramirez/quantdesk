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
- `compose.yaml` (+ `compose.override.yaml` for dev, `compose.prod.yaml` for the homeserver): `postgres:16`, `backend`, `frontend`. Named volume for Postgres in dev, a bind mount under `./data/` in prod, and a bind mount for `DATA_DIR` in both.
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

**Second bullet done 2026-09-11** (the user pointed at the "Table/chart mounts here (T15)"
placeholder that had been on `/history` since T12's scaffold). `LevelHistory` (ECharts line
chart: flip point, call wall, put wall and spot on one axis) and `LevelHistoryTable` now render
there, both fed by the existing `useLevelsHistory` rows.

Per the `dataviz` skill, invoked before writing the chart: colours are imported from
`GexByStrike`'s `THEME_COLORS` rather than re-chosen, so calls stay blue and puts stay red
across both charts; spot takes the app accent and flip takes primary ink, matching how
`GexByStrike` already draws its derived net line. `validate_palette.js` passes all six checks in
light; in dark it passes CVD/chroma/contrast and fails only the lightness band on `#c084fc`,
the app's existing dark accent -- kept deliberately, since giving spot a different colour here
would break the one-system requirement. Nulls are gaps, never zeros. Verified by rendering: both
themes screenshotted in a real browser, no console errors, no horizontal overflow.

**First bullet is obsolete, not outstanding.** It called for a new
`GET /api/prices/{underlying}?days=30` sourcing daily OHLC from the Cboe delayed quote. T42
superseded that entirely: `daily_bars` and `GET /api/bars/{symbol}` are the price source now,
and T74 added intraday bars on top. A `PriceChart` with level overlays would be worth building
on *that* data, as its own task, rather than resurrecting this bullet's endpoint.

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

> **Superseded 2026-09-11 by [plans/continuous-feed/](plans/continuous-feed/README.md).** T18,
> T19 and T20 keep their IDs; their full specs, design decisions, verified facts and likely
> first-contact failures now live in
> [02-intraday-polling.md](plans/continuous-feed/02-intraday-polling.md). Dispatch from there,
> not from the three blocks below. T18's dependencies changed: it now depends on **T32 and
> T71** (retention and capture idempotency), and the whole tier's value depends on **T70**, an
> always-on host — a missed intraday slot is unrecoverable on this source.

### T18 · Sonnet · T32, T71 (was T05, T09)
**Intraday polling job**

**Done 2026-09-11.** `INTRADAY_ENABLED` (default False), `capture_intraday_job` with flag /
trading-day / window guards, conditional registration, and a misfire policy inverted from the
capture jobs (300 s grace vs `None`) because an intraday slot has nothing to rescue. 11 new
tests; suite 938 passed. Verified live inside the window on a trading Friday: two rounds 45 s
apart captured all five symbols, SPX 29,162 contracts with levels computed, and DIA's unrefreshed
payload was correctly deduped. Two live findings recorded in the plan's Result section,
including a correction to how often the content key actually fires. See
[02-intraday-polling.md](plans/continuous-feed/02-intraday-polling.md).

Add scheduler job `capture_intraday`: every 15 minutes from 09:45 to 16:15 NY on trading days, all symbols, `is_eod=False`, compute levels after each capture. Ensure the EOD job still runs at 16:20 and that the polling cadence never exceeds one request per symbol per 15 minutes (this is the free source's informal limit). Config flag `INTRADAY_ENABLED`.

### T19 · Sonnet · T18, T11
**Server-Sent Events stream**

**Done 2026-09-11.** `app/events.py` (in-process broker), `app/api/stream.py`
(`GET /api/stream/{underlying}`), a publish from the capture path after levels commit, and the
`useLiveLevels` hook plus a `LiveIndicator` in `ContextBar`. 24 new tests; suites 953 backend /
343 frontend. Verified live against a running backend: `ready` on connect, a `levels` frame on a
real capture, keep-alives while idle, and a logged unsubscribe on disconnect. See the Result
section in [02-intraday-polling.md](plans/continuous-feed/02-intraday-polling.md).

`GET /api/stream/{underlying}` SSE endpoint that emits an event whenever a new snapshot's levels are stored. Frontend hook `useLiveLevels` that invalidates the relevant queries on each event. Show a "last updated" indicator and a delay badge in the top bar.

### T20 · Sonnet · T18, T14
**Intraday timeline view**

Page `/intraday`: for a chosen date, a chart of flip point, call wall, put wall and spot across the session's snapshots, plus a slider to scrub the GexByStrike chart through the day's snapshots.

---

## Phase 5 — Real-time (Tradier)

> Status and the 2026-09-11 vendor re-survey (including why Alpaca was evaluated and rejected
> as a primary source) are in
> [plans/continuous-feed/04-realtime-paid.md](plans/continuous-feed/04-realtime-paid.md). The
> three specs below are unchanged. Still blocked on a funded Tradier account — and the trigger
> for opening one is now empirical: measure, from T18's own captured series, how far the
> 15-minute-lagged flip point sits from where it actually was.

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

## Tasks added by the T06 Phase 1 review (2026-09-04)

These came out of reviewing T02–T05 together. T29 is the important one: it closes the gap between "the pipeline works" and "the pipeline can be left alone for months", which is the whole premise of starting capture early.

### T29 · Sonnet · T04, T05
**Startup catch-up for a missed EOD capture**

APScheduler uses an in-memory job store, so `misfire_grace_time=None` only rescues a run whose *process was alive* (laptop asleep). If the backend is not running at 16:20 ET — laptop shut down, reboot, `docker compose down`, a deploy — the run is never scheduled and never fires, and the free Cboe source cannot backfill it. For a personal laptop app this is the dominant miss mode, and it is completely silent: no log line, no failed job, no row.

- On FastAPI lifespan startup: if today (NY) is a trading day, now is past 16:20 NY, and no `is_eod=True` snapshot exists for today for a symbol, run `capture_eod` for it immediately. Cboe keeps serving the settled chain through the evening, so a catch-up until roughly 23:59 ET still captures the correct close.
- Add a second "safety net" cron (e.g. 20:00 NY) with the same no-row-yet guard, so a process that starts at 16:05 and stays up through a 16:20 crash still gets the day.
- Add a `GET /api/health/capture` returning, per symbol, the last capture time and whether today's EOD row exists — one URL that answers "is my dataset still whole?".

Acceptance: with the clock faked past 16:20 on a trading day and an empty DB, startup produces an `is_eod=True` row per symbol; starting twice does not double-capture.

### T30 · Sonnet · T04, T05, T09
**Settle what `snapshots.parquet_path` actually means**

`app/models/db.py` documents the column as "relative to `DATA_DIR` (posix separators) so the index stays valid if `DATA_DIR` moves between hosts". It is not: `SnapshotRepository.add` stores whatever `write_snapshot` returned, which is `DATA_DIR`-joined — CWD-relative with the default `DATA_DIR=./data` (verified live: `data/chains/SPX/2026/09/…parquet`) and absolute under Docker's `DATA_DIR=/data`. Rows written on the host and in the container are therefore mutually unreadable, and any reader must guess the base. Nothing reads the column yet, which makes this the cheapest possible moment to fix it.

- Store the path relative to `DATA_DIR` and add a single `resolve_snapshot_path(row)` helper that all readers (T09 backfill, T11 read API) use.
- Test: a row written with one `DATA_DIR` resolves correctly after `DATA_DIR` changes.
- Reconsider echoing a filesystem path in the `GET /api/snapshots` response body at all.

### T31 · Sonnet · T02, T05
**Alert on partial-chain degradation**

Providers skip-and-log unparseable contracts, which is the right policy, but the only signal is a WARNING. If Cboe renames a root or changes the OCC format, 30 % of SPX could vanish from every snapshot with GEX quietly wrong and captures still reporting `ok=True`. (T06 fixed the 100 %-skipped case, which now raises.)

- Carry `skipped` and `listed` out of the provider on the snapshot and into `CaptureResult`, the structured log line and the `snapshots` row.
- Log at ERROR when skipped exceeds a small threshold (e.g. 1 %), and flag any capture whose contract count deviates more than ~30 % from that symbol's trailing median.

### T32 · Opus · T09  — full spec: [plans/continuous-feed/01-capture-integrity.md](plans/continuous-feed/01-capture-integrity.md)
**Retention policy for `gex_by_strike`**

> Re-scoped 2026-09-11. The dependency direction was backwards: T32 **gates** T18, it does not
> follow it. Model raised to Opus — this is a data-retention judgment call, not wiring. The
> plan file carries measured volumes (~216k rows/day at 27 captures), the recommended policy,
> and the reason pruning is safe (`gex_by_strike` is a cache recomputable from Parquet).

**Done 2026-09-11.** `app/jobs/retention.py`, `INTRADAY_STRIKE_RETENTION_DAYS` (default 30,
`0` disables), and a daily 21:00 ET `retention_prune` job. Kept forever: EOD strike detail,
every `gex_levels` row, every `snapshots` row, every Parquet file. 13 new tests; backend suite
927 passed. Run live against the real Postgres (38,650 strike rows, 25 non-EOD snapshots, all
inside the window) and correctly did nothing. Full rationale and the resolved judgment calls
are in the Result section of
[plans/continuous-feed/01-capture-integrity.md](plans/continuous-feed/01-capture-integrity.md).

Flagged by T09: `gex_by_strike` is the volume driver at ~800 rows per filter per capture (2 non-empty filters × 3 symbols today). T18's 15-minute intraday polling multiplies that by ~26 sessions-worth per day per symbol. Nothing partitions or prunes it. Decide a retention rule — e.g. keep EOD strike detail forever, drop intraday strike detail after N days while keeping the `gex_levels` summary row — and implement it before T18 ships, not after the table is large.

---

## Tasks added by the T10 validation and supervisor checks (2026-09-04)

### T33 · Opus · T07, T08, T10
**Fit the carry term from put-call parity instead of guessing it**

`docs/validation.md` §6.2 established, from put-call parity on real market quotes, that our carry is mis-set: parity implies `r − q ≈ 3.6 %` against our configured `4.0 % − 1.3 % = 2.7 %`. This is worth roughly **8 % of SPX net GEX** and is the single largest source of the remaining gap against the one vendor that corroborates our magnitude (ours +48.29 B vs their +43.6 B — an 8 % correction lands almost exactly on their figure). It is a mis-set parameter, not a bug, which is why T10 correctly did not "fix" it by tuning.

- Derive the implied forward per expiry from put-call parity on liquid near-the-money pairs, and use it in place of a single global `RISK_FREE_RATE − DIVIDEND_YIELD`. The engine already prices SPX on the forward, so this is a change of input, not of model.
- Fall back to the configured constants when an expiry has no usable pair (wide spreads, no volume, deep-dated).
- Keep it a pure function in `backend/app/gex/`; no I/O.
- Report the before/after net GEX for a live SPX chain and re-run the T10 comparison rows.

Acceptance: parity residual on the fitted expiries drops materially; net GEX moves toward the vendor figure; a regression test pins the fitted forward for a stored fixture.

### T34 · Sonnet · T02, T05, T14
**Cboe's `timestamp` is payload-generation time, not data-effective time**

Verified by the supervisor on 2026-09-04: at 17:55 ET — nearly two hours after the 16:00 close — the top-level `timestamp` read `17:54:46 ET` and kept advancing on every request, while `data.current_price` stayed frozen at the 7718.6001 close. The field is "when the CDN built this payload", not "when this data was effective".

Consequences, in order of severity:

1. **The freshness badge lies after the close.** `T14`'s `KeyLevels` and the top bar render "As of {captured_at} · Delayed 15m", so an evening view of Friday's settled chain claims to be 15 minutes old when it is hours old. This is user-facing incorrectness in exactly the place the app promises honesty about staleness.
2. **`captured_at` does not mean what `docs/schema.md` and `cboe.py` say it means** ("effective time of the data"). The NY *date* is still right, so history queries are unaffected, but the time component is not the data's.
3. **The `(underlying, captured_at)` duplicate check can almost never fire**, since the timestamp advances on every call. T05's guard is therefore near-dead code; T29's `is_eod`-per-day guard is what actually prevents double captures.

- Decide what `captured_at` should hold — the vendor timestamp clamped to the last market minute (16:15 ET for a delayed feed after the close), or the vendor timestamp plus a separate `effective_at`. Update `docs/schema.md` to match reality either way.
- Make `delayed_minutes` (or a derived staleness value) reflect actual age when the market is closed, so the badge reads "at Friday's close" rather than "delayed 15m".
- Reconcile with T29: an evening catch-up capture is legitimately the day's close and must stay `is_eod=True`.

Acceptance: a snapshot captured post-close reports an honest age in the API and the UI badge; a test pins the behaviour with a frozen clock on both sides of 16:00 ET.

---

## Tasks added by supervisor verification of T11/T16 (2026-09-04)

### T35 · Sonnet · T29
**The backend no longer starts when Postgres is unreachable**

Regression introduced by T29's startup catch-up. Verified both directions by the supervisor: with Postgres down, uvicorn logs `Waiting for application startup.` → the scheduler starts → and then it **hangs indefinitely**, never logging `Application startup complete` and never accepting a connection on its port. With Postgres up, the same command is ready instantly. Before T29 the app served `/health` with no database at all, which is how T00's acceptance worked.

The likely cause is that `catch_up_missed_eod`'s `has_eod_snapshot_today` performs a **synchronous** DB call inside the `asyncio.create_task(...)` coroutine, so a blocking psycopg connect (with an OS-level TCP timeout) starves the event loop that uvicorn needs to finish its startup. Fire-and-forget scheduling is not enough on its own — the work also has to leave the loop.

- Run the catch-up's blocking DB and HTTP work off the event loop (`asyncio.to_thread`, as `capture.py` already does for the repository), and/or give the connection an explicit short timeout.
- The app must become ready and serve `/health` and `/docs` even with no database reachable, degrading the catch-up rather than the process.
- Add a regression test that starts the app with an unreachable `DATABASE_URL` and asserts startup completes.

Acceptance: `uv run uvicorn app.main:app --port <p>` with Postgres stopped reaches "Application startup complete" and answers `/health` within a few seconds.

### T36 · Sonnet · T14, T16
**Dashboard visual defects found in real-data screenshots**

Confirmed by the supervisor from headless-Chromium screenshots of the assembled dashboard against live API data. The numbers are all correct; the presentation is not.

1. **The gamma profile x-axis includes zero, which makes the chart unreadable.** The profile grid spans ±10 % of spot (≈6,947–8,490 for SPX at 7,718.6), but the axis renders **0 → 10,000**, compressing the entire curve into a near-vertical sliver about 15 % of the plot width. The zero crossing — the single most important feature of this chart, and the reason it exists — cannot be read at all. ECharts value axes default to `scale: false`, which forces the axis through zero; it needs `scale: true` or an explicit min/max fitted to the grid. **This is a functional defect, not cosmetic.**
2. **The top bar runs together and overflows.** Labels and controls have no separation ("Expiry`[All]`Snapshot`[Latest]`As of 7:48 PM ET · Delayed 15m"), the nav renders as "DashboardHistorySettings" with no separators, and on a 390 px viewport the freshness text overflows off the right edge. The symbol switcher is also centred oddly, and the current symbol's disabled styling reads as "unavailable" rather than "selected".
3. **Wall marker labels are clipped** in `GexByStrike` — they render as "ll w" and "t w" instead of "Call wall" / "Put wall".

Note the intended behaviour that is *not* a bug: `GammaProfile` always shows its own All and Ex-0DTE series regardless of the selected expiry filter (per T14), so it can legitimately display a flip point while `KeyLevels` shows an em-dash under `filter=ZERO_DTE`. Consider labelling the panel so that reads as deliberate.

Acceptance: screenshots at 1280 px and 390 px showing the profile curve filling its plot area with a legible zero crossing, a top bar that neither overflows nor runs together, and unclipped wall labels.

### T37 · Sonnet · T11, T16
**"No data yet" is an empty state, not an error — and never show raw JSON**

Hit by the user on first run. Switching to a symbol that has never been captured renders, verbatim in the page:

```
Failed to load SPY GEX: {"detail":"no snapshot captured yet for SPY"}
```

Three things wrong, in order:

1. **It is not a failure.** A symbol with no snapshot yet is the expected state on first run, after `docker compose down -v`, or for any symbol the user has not captured. Presenting it as an error makes a working app look broken on the very first thing a new user does.
2. **Raw JSON reaches the UI.** `client.ts` interpolates the response body into the message. No user should ever see `{"detail": ...}`. Parse the error envelope and surface the message, or a mapped friendly string.
3. **There is no way out.** The page states a problem and offers no affordance. The fix is one POST the user cannot discover from here.

- Distinguish "no data yet" (the backend returns a clean 404 with a specific `detail`) from a genuine transport or server failure, and render an empty state rather than an error for the former.
- The empty state should say what will happen on its own (the EOD capture runs 16:20 ET on trading days, and T29 catches up on startup) and offer a "Capture now" button hitting `POST /api/snapshots/capture?underlying=…`, which takes about 2 s.
- Genuine failures keep an error presentation, but with the parsed message, never the raw body.
- Apply the same treatment on `/history`, which has the same problem for a symbol with no level rows.

Acceptance: with an empty database, loading the dashboard for each symbol shows an empty state with a working capture affordance and no JSON; a real backend failure (stop the API) still shows a proper error.

---

## Tasks added by supervisor instrument review (2026-09-05)

### T38 · Sonnet · T02, T11, T16
**Add GLD and DIA as tracked instruments**

The user asked for gold and the Dow. Both were verified live by the supervisor on 2026-09-05 before this task was written, so **do not re-litigate feasibility — build it.** Measured facts, from running the real payloads through the existing `CboeProvider._parse_payload` and `compute_all` unchanged (the root was temporarily aliased, since `Underlying` is a closed enum):

| | contracts | with OI > 0 | expiries | net GEX | abs GEX | data quality |
|---|---|---|---|---|---|---|
| SPY (reference) | 12,456 | 8,181 | — | −1.845 B | 38.663 B | — |
| **GLD** | 7,546 | 4,219 | 29 | **+2.265 B** | 5.303 B | 0 extreme-IV, 0 missing OI |
| **DIA** | 5,028 | 2,720 | 21 | **−0.009 B** | 1.037 B | 0 extreme-IV, 0 missing OI |

Both are served by the Cboe endpoint at the **bare-ticker ETF URL** (`.../options/GLD.json`, `.../options/DIA.json` — no underscore prefix; that is for index roots only), each with a single vendor root equal to the ticker. Both are P.M.-settled, so `AM_SETTLED_ROOTS` stays `{"SPX"}` and settlement is already correct by default. Data quality is *better* than SPX: zero extreme-IV exclusions and zero missing open interest on both, and `net_gex_iv_unfiltered` equals `net_gex` exactly, so the IV policy is immaterial here.

**No engine, Greeks, schema, storage or migration change is required.** `snapshots.underlying` is `String(16)` with no enum or check constraint, so nothing in Postgres constrains the symbol set — do not write a migration.

Deliverables:

- `backend/app/models/chain.py`: add `GLD` and `DIA` to `Underlying`, and both roots to `_ROOT_TO_UNDERLYING`. The enum docstring already names this as the intended extension point; follow it exactly and add nothing else.
- `backend/app/providers/cboe.py`: `_VENDOR_SYMBOL` entries mapping both to their bare tickers.
- `backend/app/providers/marketdata.py`: **docstring only.** It uses the canonical `Underlying` value verbatim as the URL path segment, so there is no mangling table to update. Do not add one.
- Config: `SYMBOLS=SPX,SPY,QQQ,GLD,DIA` in `.env.example` and `compose.yaml`. Note in the PR/commit body that the user's own root `.env` is gitignored and must be updated by hand, or the new symbols will not be captured on their machine.
- `frontend/src/api/types.ts`: add `'GLD'` and `'DIA'` to `UNDERLYINGS`.
- `frontend/src/mocks/handlers.ts`: **this is the only thing that breaks the build.** Two `Record<Underlying, GexResult>` maps are exhaustive over the union, and `isUnderlying` hardcodes the three literals, so widening the union is a compile error until fixtures exist. Add `gex-gld.json`, `gex-gld-zero-dte.json`, `gex-dia.json`, `gex-dia-zero-dte.json` under `mocks/fixtures/`, generated from real captures and trimmed to match the existing ~13 KB / ~3.5 KB files.
- `frontend/src/components/layout/TopBar.tsx`: the symbol switcher goes from three buttons to five. T36 already had to fix this bar's layout once — check it at a narrow width rather than assuming it reflows.
- `backend/tests/fixtures/cboe/gld.json` and `dia.json`, trimmed to roughly the 75 KB of `spy.json`/`qqq.json`. **Do not commit the raw payloads** — they are 3.3 MB and 2.2 MB.
- Update the symbol-set assertions in `test_health.py`, `test_scheduler.py`, `test_catchup.py`, `test_capture.py`, and add parser coverage for both roots in `test_cboe.py` / `test_occ_symbol.py`.
- Docs: `PLAN.md` §1 currently scopes the app to "US index options — SPX, SPY, QQQ". Amend it to record that GLD (a commodity ETF) and DIA are now tracked, and why. Update the underlying/root tables in `docs/schema.md` (lines ~37, ~38, ~85, ~142) and the one-line scope sentence in `README.md`. Also update `CLAUDE.md` (opening line and the invariants list), `context/data-and-ops.md` (the `SYMBOLS` row and the Cboe section) and `context/architecture.md` where they name the three-symbol set.

**The DIA carry caveat — read this before touching anything.** `DIVIDEND_YIELD` is a single global 0.013, the S&P trailing yield, applied to every symbol. It is wrong for both new instruments: GLD pays no dividend at all (it carries a ~0.40 % expense drag), and DIA's yield is its own. Measured impact of setting `q = 0` instead:

- GLD: net GEX +2.265 B → +2.322 B (+2.5 %), flip 381.45 → 380.65. Bias, but the signal survives it.
- **DIA: net GEX −0.009 B → +0.008 B. The carry assumption flips the sign.** DIA's net is 0.9 % of its 1.037 B gross, so the headline "dealers are long/short gamma" reading and the flip point (532.58, essentially at a 532.34 spot) are noise-dominated at the current parameter.

Do **not** try to fix this here — fitting the carry per expiry from put-call parity is T33, it is Opus work, and it fixes all five symbols at once. What this task must do is record the limitation honestly: add a short subsection to `docs/validation.md` stating that DIA's net GEX and flip point are not trustworthy until T33 lands, with the numbers above. GLD's walls (415 / 335) and DIA's walls (540 / 533) are per-strike readings and are unaffected by the carry parameter, so they stay usable either way.

Constraints: work only in the paths listed; do not change the public interfaces from earlier tasks; do not touch `app/gex/engine.py` or `greeks.py`. Never kill processes by image name — only PIDs you started; work around an occupied port instead.

Acceptance: `POST /api/snapshots/capture?underlying=GLD` and `…=DIA` each persist a snapshot with a non-zero contract count and stored levels for all three default filters; the dashboard symbol switcher shows five symbols and renders GLD and DIA end to end against the mocks; `uv run pytest`, `npm test`, `npm run lint` and `ruff check .` all pass; `docs/validation.md` carries the DIA carry caveat.

---

## Tasks added by the report-view request (2026-09-05)

### T39 · Opus · T08, T11
**Report analytics: the derived figures a written report needs**

The user wants an "options intelligence" report view (T40 renders it). They supplied
`context/example-report.md` and a screenshot as a **layout reference**. Read them for section
structure and tone only.

**Do not reproduce that file's numbers.** The supervisor reconciled it against the very GLD
chain it claims to describe (same spot, $406.77) and it does not survive contact with the
data:

| Metric | The example claims | Actual, our engine | |
|---|---:|---:|---|
| Call OI | 106,705 | 4,304,029 | wrong |
| Put OI | 271,163 | 1,945,056 | wrong |
| P/C ratio (OI) | 2.54 → "BEARISH" | **0.45** | **inverted** |
| P/C ratio (volume) | 1.08 | 0.50 | wrong |
| Max pain | $410 | $400 full chain, $408 at ≤ 7 DTE | wrong |
| IV environment | 17.9 % | 32.0 % OI-weighted, 22.9 % ATM ≤ 45 DTE | wrong |
| Call / put wall | 405 / 395 | 415 / 335 | wrong |

GLD carries 2.2 calls per put in open interest; the example asserts the reverse and derives a
bearish reading from it. It is also internally inconsistent — it lists 407 and 406 as
*support* while listing 405 as *resistance*, and repeats "Break below 407 / Risk: CRITICAL"
against three different strikes. Every number in our report comes from our own engine.

Build a **new pure module `backend/app/gex/report.py`**. Same contract as `engine.py`: no
HTTP, no database, no filesystem, no logging, deterministic given its inputs. It consumes a
`GexResult` and the `to_frame` DataFrame and returns a frozen `ReportResult` dataclass.
`engine.py` and `greeks.py` are otherwise **not to be modified**, with one sanctioned
exception named below.

Compute:

- **Max pain** — the strike minimising total intrinsic value of all open contracts at expiry,
  over the same expiry scope as the filter in play. Verified reachable from the frame today
  (`strike`, `right`, `open_interest`).
- **Put/call ratios and volume/OI totals** — split by right, from `volume` and
  `open_interest`. Both are already frame columns.
- **IV regime** — an ATM ~30-day implied vol, interpolated from near-the-money contracts
  bracketing 30 DTE. **Do not label it LOW/NORMAL/HIGH from a single snapshot.** There is no
  basis for a band without history. Return the number always, and a regime label only when
  at least 20 prior snapshots of the same symbol exist to compare against; otherwise return
  the label as `None` and let T40 render "insufficient history". This is the same honesty
  rule as T34's staleness badge and T37's empty state — do not invent a threshold.
- **Dealer positioning** — from the sign of net GEX, gated on `|net_gex| / abs_gex`.
  Measured on live chains: SPY 4.8 %, GLD 42.7 %, **DIA 0.9 %**. Below a documented ratio
  floor the label must read *noise-dominated*, not a direction. `docs/validation.md` §9
  already establishes that DIA's net GEX sign flips under a plausible carry correction, so a
  report that calls DIA "short gamma" would be asserting something the validation document
  says we cannot support. Cite §9 in the code comment.
- **Support / resistance levels** — from `top_positive` / `top_negative` and the walls, with
  distance from spot as a percentage. Resistance must never sort below support; if the
  computed sets overlap, that is a real condition (gamma concentrated on both sides of spot)
  and must be labelled, not silently reordered the way the example file does.
- **Premium-selling candidates** — OTM contracts beyond the walls within a DTE window, with
  mid price, IV and DTE. **This is the one sanctioned `engine.py` change:** add `bid` and
  `ask` to `FRAME_COLUMNS` and populate them in `to_frame`. They exist on `OptionContract`
  and in Parquet but are not currently in the frame. Additive only — add the two columns and
  nothing else, and confirm every existing engine test still passes.
- **Playbook and risk alerts** — deterministic triggers derived from the computed levels
  (break above call wall, break below put wall, the range between, proximity to flip). Every
  number must trace to a computed level. No invented targets or stops.

Also render the report as **plain text** (`render_text(result) -> str`) following the section
order of `context/example-report.md`, so the report exists outside the browser. Keep the
renderer separate from the computation.

Expose it as `GET /api/report/{underlying}?filter=` in a new `backend/app/api/report.py`,
returning the structured result; add `?format=text` for the rendered text. Reuse the stored
snapshot the way `api/gex.py` does — do not recompute from Parquet if the levels are already
persisted, and do not add a table.

`PLAN.md` §2's "Purpose: analysis and charts only. No order routing." line needs amending:
the user explicitly asked for the playbook and premium-selling sections on 2026-09-05, after
the supervisor flagged that they cut against that line. Record that the app now emits trade
*suggestions* while still never routing an order. Do not quietly leave the scope line
contradicted.

Acceptance: `report.py` is import-clean of I/O; unit tests pin max pain, the P/C ratios and
the ATM IV against a fixture chain with hand-checked values; a DIA fixture asserts the
positioning label is the noise-dominated one; the IV regime label is `None` on a
single-snapshot database; `GET /api/report/GLD` returns numbers matching a direct
`compute_all` + `report` call; `render_text` output is snapshot-tested.

---

### T40 · Sonnet · T39
**Report view**

Render T39's output as a new `/report` route, matching the supplied screenshot's layout.

- Header `{SYMBOL} Analysis Results`, then three summary cards: **Current Price**,
  **Volatility** (the ATM IV and its regime label, or "insufficient history"), **Market
  Sentiment** (P/C ratio and the positioning label).
- **Top Resistance Levels** and **Top Support Levels** as chips, red and green respectively,
  each showing the strike and its distance from spot.
- A collapsible **View Full Report** panel containing T39's rendered text in a monospace
  block, scrollable, with a copy-to-clipboard affordance.
- Reuse the existing URL state (`state/urlState.ts`) for symbol and filter so
  `/report?symbol=GLD` deep-links, exactly as the dashboard and history pages do. Add the
  route to `AppShell`'s nav.
- Reuse `lib/format.ts` and `theme/vizPalette.ts`. The red/green chips must come from the viz
  palette, not inline hex, so both themes stay correct.
- Empty and error states follow T37: a symbol with no snapshot renders the empty state with a
  capture affordance, never raw JSON.
- Add MSW handlers and fixtures for the new endpoint.

The report contains trade suggestions. Label the premium-selling and playbook sections as
screening output computed from the current chain, not as recommendations, and surface the
data-freshness badge (T34) on the report page too — a playbook drawn from a stale chain is
worse than no playbook.

Acceptance: `/report?symbol=GLD` and `?symbol=DIA` render every section end to end against
the mocks; DIA visibly shows the noise-dominated positioning label rather than a direction;
`npm test`, `npm run lint` and `tsc -b` pass.

---

### T41 · Sonnet · T39, T40
**CFD level translation: report levels in the instrument the user actually trades**

The user executes in a CFD account, so a GLD report's strikes are not the numbers on their
screen — they trade **XAUUSD**, quoted in USD per troy ounce, while GLD is a share worth a
fraction of an ounce. The same mismatch applies to **DIA vs US30** (DIA tracks ~1/100 of the
Dow), **SPX/SPY vs US500** and **QQQ vs NAS100**. Build this as a general mapping, not a
gold special case.

**There is no free automated source for the ratio.** The supervisor checked on 2026-09-05:
stooq now sits behind a JavaScript proof-of-work challenge, and SPDR's GLD page renders its
NAV client-side so ounces-per-share is not in the HTML. Do not add a provider for this and do
not hardcode a ratio — **the user supplies it**, and the design below is built around that
rather than treating it as a limitation.

### How the ratio is anchored

The user types the CFD spot their platform shows. The ratio is then derived from the two
observed spots, which is self-calibrating and cannot go stale:

    k = cfd_spot / underlying_spot          # e.g. XAUUSD 4412.50 / GLD 406.77
    cfd_level = underlying_level * k

This is deliberately *not* a stored ounces-per-share constant. GLD's gold backing erodes
continuously with the trust's expense ratio, index-CFD ratios drift with dividends, and any
constant we persisted would silently rot. Anchoring on two simultaneous spots absorbs all of
that.

- Accept an optional `cfd_spot` query parameter on `GET /api/report/{underlying}`. Absent, the
  report is exactly what it is today — this must stay entirely optional.
- The frontend supplies it from a field on the report page, held in URL state
  (`state/urlState.ts`) so `?symbol=GLD&cfd=4412.50` deep-links, the same way `filter` does.
- Keep a `CFD_INSTRUMENTS` mapping of underlying → display name (`GLD` → `XAUUSD`, `DIA` →
  `US30`, `SPX`/`SPY` → `US500`, `QQQ` → `NAS100`) so the UI can label the field and the
  converted block correctly. Adding an instrument must be one line here.

### What converts, and what must not

This is the part to get right; a wrong conversion here produces confident, wrong trade levels.

**Convert (they are prices in the underlying's units):** spot, call wall, put wall, flip
point, max pain, every support/resistance level, every playbook trigger, target and stop, and
the strike of each premium-screen row.

**Never convert:**

- **Dollar GEX magnitudes** — net, absolute and per-strike. These are US dollars of *dealer
  delta in GLD options* per 1 % move. There is no dealer gamma in the CFD; a "XAUUSD net GEX"
  figure would be meaningless. Leave them in the report unchanged and unconverted.
- **Premium prices** in the premium-selling screen. Those are GLD option premiums. Translate
  the strike so the user can see where it sits in gold terms, but the premium stays a GLD
  option premium and must never render as a CFD price.
- **Implied volatility.** It is GLD's implied vol. Related to gold's, not identical. Keep it
  labelled as the underlying's.

**The invariant worth testing:** percentage distances from spot are *identical* in both units,
because this is a pure scaling. GLD's 415 call wall is +2.02 % from a 406.77 spot, and its
translation is +2.02 % from the translated spot. A test should assert exactly that — it is what
proves the mapping is a scaling and not something subtler.

### Honesty requirements

Translated levels are **GLD option levels expressed in gold terms, not levels with their own
gamma behind them**. The UI and the text render must both say so. Also surface:

- GLD trades 09:30–16:00 ET; XAUUSD trades nearly around the clock. A level computed from a
  GLD close maps onto a market that keeps moving after that close — pair this with the
  existing T34 freshness badge rather than duplicating it.
- GLD can trade at a premium or discount to its NAV, so the ratio is a snapshot of this
  moment, not a constant.
- Echo back the implied ratio and the spot pair it came from, so the user can eyeball it
  against their platform in one glance.

When `cfd_spot` is absent, render nothing converted and no placeholder numbers — same rule as
the null IV regime label and T37's empty states. Do not invent a default ratio.

### Deliverables

- Extend `backend/app/gex/report.py` (still pure) with the conversion and the instrument map.
  A converted block hangs off the existing result rather than replacing any field, so an
  unconverted report is byte-identical to today's.
- Include the converted levels in `render_text`, in the playbook and levels sections — the
  user asked specifically for *trade plans* to carry them, and the text render is what gets
  copied out of the app.
- Report page: a labelled CFD-spot input (label from `CFD_INSTRUMENTS`, e.g. "XAUUSD spot"),
  URL-backed, with the converted levels shown alongside the native ones rather than replacing
  them. Both numbers visible; the user reasons in GLD and executes in XAUUSD.
- Tests: the percentage invariant above; that GEX magnitudes, premiums and IV are untouched;
  that an absent `cfd_spot` leaves the response unchanged; that a zero, negative or
  non-numeric `cfd_spot` is rejected rather than producing infinities.

Acceptance: `GET /api/report/GLD?cfd_spot=4412.50` returns converted levels whose percentage
distances match the unconverted ones exactly, with GEX magnitudes unchanged; the report page
shows an XAUUSD field for GLD and a US30 field for DIA; the copied text report carries the
translated playbook; `uv run pytest`, `ruff check .`, `npm test`, `npm run lint` and `tsc -b`
all pass.

---

## Tasks added by the continuation brainstorm (2026-09-09)

The user's assets are fading breakouts; they want to see which markets have continuation and
where money rotates between sectors. Full specs live in `plans/continuation/` (one file per
tool, same block shape as here). IDs T42–T56 are reserved. T57 (rotation in-progress week label), T58 (daily flow series endpoint) and T59 (VanEck/Invesco/USCF flow sources) were filed on 2026-09-09/10; T60 (decision engine) and T61 (decision track record) on 2026-09-10;
T62–T69 (UI/UX refresh) on 2026-09-10 in `plans/ui-ux-refresh/`; T70–T72 (continuous feed) on
2026-09-11 in `plans/continuous-feed/`. Next free ID is **T73**.

UI added 2026-09-09: the page tasks were too thin to dispatch, so `07-ui.md` now carries the
full specs for every page, a shared UI kit (T55) that all pages build on, and an overview page
(T56). T46 is folded into T44 because both backends already exist.

| ID | Model | Depends on | Task | Plan |
|---|---|---|---|---|
| T42 | Sonnet | T01, T05, T31 | Daily bars: provider ABC + Yahoo (Stooq died behind a JS proof-of-work wall, verified 2026-09-09), `daily_bars` table, 17:30 ET job, backfill CLI, `/api/bars` | [00-foundation-daily-bars.md](plans/continuation/00-foundation-daily-bars.md) |
| T43 | Sonnet | T42 | Breakout ledger: pure `app/scan/breakouts.py` + `/api/scan/breakouts` | [01-breakout-ledger.md](plans/continuation/01-breakout-ledger.md) |
| T44 | Sonnet | T55 | `/scan` page: breakouts and trend views (absorbs T46) | [07-ui.md](plans/continuation/07-ui.md) |
| T45 | Opus | T42, T43 | Trend/chop scorer: ADX, ER, CHOP, variance ratio, RV, IV/RV, rank composite | [02-trend-chop-scorer.md](plans/continuation/02-trend-chop-scorer.md) |
| T46 | — | — | folded into T44 | — |
| T47 | Sonnet | T02, T38 | Extend option capture to sector/industry ETFs via a separate 16:45 ET job | [03-regime-board.md](plans/continuation/03-regime-board.md) |
| T48 | Opus | T42, T45, T47 | Regime metrics: wall spacing, room beyond, 0DTE share, verdict with reasons | same |
| T49 | Sonnet | T48, T55 | `/regime` page | [07-ui.md](plans/continuation/07-ui.md) |
| T50 | Opus | T42 | Rotation math: weekly RRG approximation, relative returns, sector-level breadth | [04-sector-rotation.md](plans/continuation/04-sector-rotation.md) |
| T51 | Sonnet | T50, T55 | `/rotation` page | [07-ui.md](plans/continuation/07-ui.md) |
| T52 | Sonnet | T42 | ETF flows: issuer shares-outstanding survey, then ingest for supported families | [05-etf-flows.md](plans/continuation/05-etf-flows.md) |
| T53 | Sonnet | T52, T55 | `/flows` page | [07-ui.md](plans/continuation/07-ui.md) |
| T54 | Sonnet | T42, T45, T55 | Cross-asset strip: Cboe index-history bars provider, term structure, VRP, correlation; strip visual spec in 07-ui.md | [06-cross-asset-regime.md](plans/continuation/06-cross-asset-regime.md) |
| T55 | Sonnet | T43, T45, T47 | Scan UI kit: route-aware TopBar, `useScanParams`, ScanTable/Sparkline/StatusChip/EmptyState, recorded MSW fixtures, stub routes | [07-ui.md](plans/continuation/07-ui.md) |
| T56 | Sonnet | T44, T49, T51, T54 | `/overview` page: tape strip, where continuation is, open now | [07-ui.md](plans/continuation/07-ui.md) |

**All of T42–T56 shipped, 2026-09-09/10.** Every task in the table above is merged, with both
suites green (812 backend, 263 frontend). Follow-on work found while verifying it is filed as
T57 (rotation in-progress week label) and T58 (daily flow series endpoint). The open decisions
left for the user -- universe width among them -- are collected in
`plans/continuation/README.md`.

---

## Model assignment summary

| Model | Tasks |
|---|---|
| Opus | T01, T07, T08, T10, T21, T23, T25, T33, T39, T42, T45, T48, T50 |
| Opus (review) | T06, T17, T24 |
| Sonnet | T00, T02–T05, T09, T11–T16, T18–T20, T22, T26–T32, T34–T38, T40–T44, T47, T49, T51–T56 (T46 folded into T44) |

Parallelizable groups once their dependency is done: {T02, T03, T04} after T01; {T12} alongside all of Phase 1; {T13, T14} after T12; {T27, T28} anytime.

Sequencing note (2026-09-05): T38 adds GLD and DIA and is independent of everything in flight, so it can run alongside Phase 4 work. It does, however, raise T33's priority: DIA's net GEX is 0.9 % of its gross and the global carry parameter flips its sign, so DIA ships with a documented caveat until the carry is fitted from parity.

TODO (T47, 2026-09-09): `catch_up_missed_eod` (`app/jobs/catchup.py`) still covers only
`settings.symbols`. Extending it to `settings.extended_symbols` too is not the one-line change
T47's brief allowed for -- the only caller that could wire it up is
`capture_eod_safety_net_job` in `app/jobs/scheduler.py`, and that job is on T47's explicit
do-not-modify list (P0 guardrail: nothing may risk delaying or altering the core EOD capture's
safety net). A missed 16:45 extended capture today has no catch-up at all -- it is simply gone
until the next trading day's 16:45 run, unlike the core five which get both the 20:00 safety
net and the startup catch-up. Fixing this properly means either a second, extended-only safety
net job (its own id, its own trigger, no shared code path with `capture_eod_safety_net_job`,
same shape as `capture_extended_job` itself) or convincing whoever owns T47's guardrail that
extending the existing safety net is safe. Left for a future task rather than guessed at here.

Sequencing note (2026-09-04): T29 and T30 run before T11. A dashboard over a dataset with silent holes is worth less than a smaller dataset that can be trusted, and T30 is cheapest while nothing reads `parquet_path` yet. Do not run two Opus agents concurrently — it exhausts the session rate limit.

---

## T57 · Sonnet · T50, T51
**Label the in-progress week on the rotation page**

Found while verifying T50 live on 2026-09-09 (a Wednesday). `/api/scan/rotation`'s newest
trail point is dated **`2026-09-11`** — the coming Friday, a date that has not happened — and
its value is computed from Wednesday's close. `weekly_closes` resamples `W-FRI` and labels each
bin with its week-*ending* Friday, which is correct for a completed week and becomes a
future-dated, partial-week point for the current one.

Nothing in the response or the page says so. Grepping `rotation.py`, `api/scan.py` and
`Rotation.tsx` for "partial", "in-progress" or "to date" finds only unrelated matches about
rolling-window warm-up.

An RRG tail whose head moves during the week is normal and *should* keep updating — the bug is
not the value, it is presenting it under a future date with no marker. Nor should it be dropped:
the current week is the most decision-relevant point on the chart.

**Do:** carry a per-point (or per-response) flag saying the newest week is still open, and in
the UI render that week's label as the week's own range or "week to date" rather than a bare
future Friday. Reuse T34's freshness vocabulary rather than inventing a second one; the same
question ("as of when, honestly?") already has an answer in this codebase.

Paths: `backend/app/scan/rotation.py` (the flag, still pure — derive it from the data's own last
daily date versus the bin's Friday, never from `datetime.now()` inside a pure module),
`backend/app/api/scan.py`, `frontend/src/pages/Rotation.tsx`, `frontend/src/api/types.ts`, tests
both sides, `docs/validation-scan.md`.

Acceptance: a fixture whose last daily bar is a Wednesday marks that week open and renders it as
a range/"to date"; a fixture ending on a Friday close marks nothing open; the numbers themselves
are unchanged from today's (assert against the existing fixtures); `uv run pytest`,
`ruff check .`, `npm test`, `npm run lint` and `tsc -b` all pass.

**Verified while filing this (2026-09-09):** T50's math itself is correct. `rs_ratio_approx` and
`rs_momentum_approx` were recomputed by hand from `/api/bars` for XLK, XLE, XLU and XLRE over
the last three weeks and matched the API to **1.3e-13** on both coordinates, including the
`ddof=0` convention `_rolling_zscore` documents. This task is about the label, not the number.

---

## T58 · Sonnet · T52, T53
**Daily flow series endpoint, so the flows sparkline is the one 07-ui.md specifies**

Found while reviewing T53 (2026-09-10). `07-ui.md`'s `/flows` spec asks for "a sparkline of
**cumulative flow over 60 days** per fund". The only endpoint that exists,
`GET /api/scan/flows?window=`, returns **one aggregate per window** — there is no daily series
behind it, and building one is backend work, which T53 was scoped out of.

T53 shipped `FlowSparkline` plotting the three real window aggregates (5d/20d/60d) instead,
documented in its own docstring and flagged in its report rather than passed off as the
specified widget. That is the right call for a frontend-only task and the wrong end state: three
points spaced by window length is a different object from a 60-point cumulative series, and it
cannot show *when* money arrived, which is the whole point of the sparkline.

**Do:** add a daily series to the flows API — `GET /api/scan/flows/{symbol}/series?days=60`, or a
`series` field on the existing response, whichever fits `app/api/scan.py`'s existing shape better
— returning per-day `flow` and cumulative flow from `etf_shares_outstanding`. `app/scan/flows.py`
already computes per-day flows internally on the way to its aggregates (`flow_t = (SO_t −
SO_{t−1}) × NAV_t`); this is largely exposing what it already derives, not new math. Then point
`FlowSparkline` at it.

**The honest constraint that shapes this:** there is no backfill. The table accumulates from the
day T52's job first ran (2026-09-09), so a 60-day series will not exist until roughly December
2026, and until then the endpoint must return what it has with `history_since` rather than
padding. Do not build this expecting full sparklines on day one — build it so the sparkline fills
in correctly as history accrues, and so the short-history case is what it renders today.

Paths: `backend/app/scan/flows.py`, `backend/app/api/scan.py`, `frontend/src/components/flows/
FlowSparkline.tsx`, `frontend/src/api/types.ts`/`queries.ts`/`client.ts`, MSW fixtures, tests both
sides, `docs/validation-scan.md`.

Acceptance: the series endpoint returns per-day and cumulative flow for a symbol with history and
an honest short-history response for one without; `FlowSparkline` renders a real cumulative series
where one exists and the short-history state otherwise, never a padded or interpolated line; a
hand-built fixture's cumulative values match a hand computation; `uv run pytest`, `ruff check .`,
`npm test`, `npm run lint` and `tsc -b` all pass.

---

## T59 · Sonnet · T52, T53
**Shares-outstanding sources for VanEck, Invesco and USCF**

The user asked for these on 2026-09-10 (decision 2 in `plans/continuation/README.md`).
`docs/etf-flows-sources.md` marked four funds unsupported because all three issuers render the
value client-side: **SMH** and **GDX** (VanEck), **QQQ** (Invesco), **USO** (USCF). They are the
four holes in `/flows`, and two of them — QQQ and GDX — are instruments the user actually
watches.

What the survey established, so this task does not repeat it:

- VanEck `https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/` returns 200 (278 KB)
  and the only occurrence of "shares outstanding" is inside the prose definition of NAV. The
  older `.../overview/` path 302s to an empty body.
- Invesco `https://www.invesco.com/qqq-etf/en/about.html` returns 200 (208 KB) carrying only the
  label wired for client-side fill: `{"fundDetailsLabel":"Shares Outstanding","fundDetailsType":"ShareOutstanding"}`.
- USCF `https://www.uscfinvestments.com/uso` returns 200 (48 KB) with the table skeleton empty:
  `<th>Shares Outstanding</th><td data-key="so"></td>`.

**The job is to find the endpoint each page's own JavaScript calls** — open the page, watch the
network requests, and identify the JSON the value arrives in. Then write one fetcher per family
behind the existing `SharesOutstandingProvider` ABC, exactly as `SpdrAllFundsProvider` and
`ISharesProductPageProvider` are written, and register them in `app/jobs/flows.py`.

Constraints, unchanged from T52 and binding here:

- **No page requiring a login, and nothing that sets an anti-bot cookie or serves a captcha.**
  If a family needs that, mark it unsupported in `docs/etf-flows-sources.md` with the evidence
  and move on — three working families beat four with one that breaks weekly.
- Rows key on the **issuer's own stated as-of date**, never the run date. Same rule as T52.
- Every fetcher is tested against a **recorded fixture**, never the live site.
- Assert the magnitude on first fetch and record the unit per family — issuers report shares in
  ones, thousands or millions and the survey found no consistency.
- These are undocumented internal JSON endpoints and can change without notice. Each fetcher
  fails per-symbol with the symbol named, never taking the job down, and the health block's
  per-family staleness is what surfaces a break.

Update `docs/etf-flows-sources.md` in place — move each family from unsupported to supported
with its endpoint and parsing notes, or record why it stays unsupported. Remove the symbols that
gain a source from the `no_flow_data` list feeding `/flows`.

Paths: `backend/app/providers/etf_flows.py`, `backend/app/jobs/flows.py`, `backend/app/api/scan.py`
(only the `no_flow_data` list), `docs/etf-flows-sources.md`, tests + recorded fixtures.

Acceptance: for every family that gains a fetcher, a live run inserts a row with the issuer's own
as-of date and a second run inserts nothing; each fetcher parses its recorded fixture and reports
a named per-symbol failure on a body missing the value; any family that stays unsupported has its
evidence written up; `uv run pytest` and `ruff check .` pass.

---

## T60 · Opus · T48, T45, T43
**Decision engine: opportunities with entry, stop, target, thesis and invalidation**

The user asked for this on 2026-09-10: "create a decision engine based on provided data to
suggest opportunities, include entry point, sl, tp, thesis and invalidation." Built and
verified live the same day.

What shipped:

- `backend/app/scan/decisions.py` — pure module on the same contract as `app.scan.regime`.
  Consumes a `RegimeRow`, the persisted by-strike ladder, the trend composite and the breakout
  summary; emits `Opportunity` records (`fade` at a wall under long gamma, `continuation` at
  spot under short gamma or next to the flip) with entry/stop/target/target_2, reward/risk,
  thesis and invalidation sentences, a structure hint read off IV/RV, warnings, and a 0–100
  score whose five components (regime alignment 35, positioning conviction 20, reward/risk 20,
  trend 15, breakout base rate 10) are listed on the record. Status `active`/`watch`/`rejected`;
  `DecisionResult.no_trade_reasons` is non-empty exactly when no opportunity is emitted (stale
  chain, noise-dominated GEX, no ATR, no walls, no direction). Every ATR multiple is a named
  constant with no live calibration yet — see the module docstring.
- `backend/app/api/decisions.py` — `GET /api/decisions?filter=&min_score=` and
  `GET /api/decisions/{underlying}`. Runs on `app.api.scan.build_regime_rows`, the `/regime`
  pipeline factored out for reuse (the `/regime` route now consumes it too), so the regime a
  suggestion cites is byte-identical to the board. Breakout summaries come from the bars that
  pipeline already fetched.
- `frontend` — `/decisions` page: ranked table over the shared `ScanTable`, detail panel with
  the levels and their labels, thesis, invalidation, structure, warnings and score breakdown;
  a "No trade" aside listing the declined symbols with their reasons verbatim; `min_score` in
  the URL via `useScanParams`. Live fixture recorded in-process (`fixtures/scan/README.md`).

Verified live (2026-09-10, in-process against the dev Postgres): 25 opportunities across the
28 optioned symbols in ~10 s (the same IV-lookup cost `/regime` pays); most of the universe
read short gamma with a negative 5-day return, so 15 of the 25 were `CONTINUATION_DOWN`;
four stale sector chains and one noise-dominated (EEM) correctly produced no trade.

Known gaps, deliberately left: no calibration of any threshold (the engine needs a few weeks
of stored rows first — a natural T61: persist `DecisionResult` per capture and score outcomes
against subsequent bars); max pain is not an input (it lives only in Parquet, which this route
never opens); no intraday refresh (Phase 4).

Paths: `backend/app/scan/decisions.py`, `backend/app/api/decisions.py`, `backend/app/api/scan.py`
(`build_regime_rows` refactor only), `backend/app/main.py`, `backend/tests/test_scan_decisions.py`,
`backend/tests/test_decisions_api.py`, `frontend/src/pages/Decisions.tsx` (+ test),
`frontend/src/components/decisions/*`, `frontend/src/api/{types,client,queries}.ts`,
`frontend/src/state/urlState.ts`, `frontend/src/theme/vizPalette.ts`, `frontend/src/mocks/*`,
`context/backend.md`, `context/frontend.md`, `CLAUDE.md`.

Acceptance (all run): `uv run pytest` (869 passed), `uv run ruff check .`, `npm test`,
`npm run lint`, `tsc -b`; the live in-process run above.

---

## T61 · Opus · T60
**Decision track record: persist every opportunity and score it against later bars**

The user asked on 2026-09-10, right after T60 landed: "did you include an opportunity status?
like if it went to profit or loss? we might need that record to sharpen the engine later."
T60 had not; this adds it.

What shipped:

- `decisions` table (Alembic `b2d4f6a8c0e1`, `models/db.py::Decision`): one row per emitted
  opportunity per `(snapshot_id, filter, key)`, the suggested levels frozen, the full
  opportunity as JSON, and the outcome columns. Insert-when-unseen, never upsert.
- `app/scan/outcomes.py` — pure evaluator. A fade fills when its wall is touched within 5 bars
  (gap-through fills at the open); a continuation fills at the next open, never the recorded
  spot. Stop is checked before target on every bar; an ambiguous bar is a stop. Gaps through
  the stop exit at the open. Expired at the close after 10 bars. Everything in R (multiples of
  `|entry - stop|`), with MFE/MAE tracked for later calibration. `summarize_outcomes` gives
  hit/win rates (withheld below five resolved) and avg/total R overall, by setup, by grade.
- `app/jobs/decisions.py` + scheduler job at **17:45 ET** (after the 17:30 bars job): record
  today, score every pending row. Never raises; ~10 s in a worker thread.
- `GET /api/decisions/history`, `POST /api/decisions/record`; a Track record block on
  `/decisions` with the summary, the ledger and a Record now button.

Verified live 2026-09-10: migration applied to the dev Postgres, first run recorded 25 rows,
all pending (no bars after the decision date exist yet). The first real outcomes appear after
the 2026-09-11 bars land.

Calibration is the point of this table and is still to come (T62 candidate): once ~100 rows
are resolved, re-fit `FADE_STOP_BUFFER_ATR`, `VOLATILITY_STOP_ATR`, `MAX_HOLD_BARS` and the
score weights against `result_r`, `mfe_r`, `mae_r`. Until then no threshold moves.

Paths: `backend/app/scan/outcomes.py`, `backend/app/storage/decisions_repository.py`,
`backend/app/jobs/decisions.py`, `backend/app/jobs/scheduler.py`, `backend/app/api/decisions.py`,
`backend/app/api/scan.py` (snapshot_id on `RegimeBuild`), `backend/app/models/db.py`,
`backend/alembic/versions/b2d4f6a8c0e1_*`, tests (`test_scan_outcomes.py`, `test_decisions_job.py`,
`test_decisions_api.py`, `test_scheduler.py`), `frontend/src/components/decisions/TrackRecord.tsx`,
`frontend/src/pages/Decisions.tsx` (+ test), `frontend/src/api/*`, `frontend/src/mocks/*`,
`context/backend.md`, `context/data-and-ops.md`.

Acceptance (all run): `uv run pytest` (889 passed), `uv run ruff check .`, `npm test`,
`npm run lint`, `tsc -b`; the live in-process run above.

---

## T62–T68 · Sonnet · proposed UI/UX refresh

The user asked on 2026-09-10 to retain the tool’s information while substantially improving its UI/UX. The proposed workbench redesign, task specifications, dependencies, invariants, and acceptance criteria are in plans/ui-ux-refresh/README.md. T62 is the approval-gated UX baseline; T63–T68 implement the shell, primitives, Today/Analyze/Review workspaces, and final visual/accessibility QA.

T62 done 2026-09-10 (read-only inventory + wireframes; no screenshot tool available in this environment, substituted with code inspection — see plans/ui-ux-refresh/01-ux-baseline.md). Wireframes approved by the user 2026-09-10, unchanged. T63 done 2026-09-10 (shell/nav rebuild, verified independently — see plans/ui-ux-refresh/README.md Result section). T64 done 2026-09-10 (shared UI primitives + Decisions/Opportunities migrated as the representative page, verified independently). T65 done 2026-09-10 (Overview + Opportunities polish, verified independently). T66 done 2026-09-10 (GEX Explorer/Scan/Regime/Rotation/Flows migrated, KeyLevels' 9 inline-style clusters rebuilt, verified independently). T67 done 2026-09-10 (Report's 82 inline-style clusters rebuilt, duplicated selects removed, History/Settings polished; found and documented that the plan's "History capture action" acceptance line was a wrong premise — History never had one — verified independently). T68 done 2026-09-10 (real Playwright+axe rendered pass across all 11 routes; found and fixed a broken production build, page-level horizontal-overflow bugs on 3 pages, and several accessibility violations; measured bundle-size delta ~+6 kB gzip, no new dependency; verified independently). T62-T68 complete — see plans/ui-ux-refresh/README.md's Result section for full detail.

T69 done 2026-09-10: compact-first density pass on Overview/GEX Explorer/Report after the user's own live-build review (plans/ui-ux-refresh/02-first-pass-review) — freshness collapsed to one status line, RegimeStrip split into a compact Overview variant, Dashboard lost its redundant tip/metric and gained a narrow-width chart toggle, Report's five heavy sections became collapsed-by-default disclosures. Verified independently. Plan T62-T69 complete — see plans/ui-ux-refresh/README.md's Result section for full detail.

---

## T70-T72 · continuous feed (2026-09-11)

The user asked "what would be the approach to have a continuous data feed?". Full plan in
[plans/continuous-feed/](plans/continuous-feed/README.md): the two-feed model (OI is published
once a day by OCC and no vendor sells it intraday, so "continuous GEX" means re-pricing a
frozen surface), three tiers cheapest-first, and the dependency graph.

Most of this work already had numbers. Phase 4's T18-T20 and Phase 5's T21-T23 keep theirs and
are re-specified in the plan files; T32 is re-scoped there and its dependency direction
corrected. Only three tasks are new.

| ID | Model | Depends on | Task | Plan |
|---|---|---|---|---|
| T70 | user decision, then Sonnet | - | Always-on host for the scheduler; private git remote first | [00-always-on-host.md](plans/continuous-feed/00-always-on-host.md) |
| T71 | Sonnet | T05 | Capture idempotency: content hash, unique constraint, EOD promotion | [01-capture-integrity.md](plans/continuous-feed/01-capture-integrity.md) |
| T72 | Sonnet | T19 | Live-spot overlay against a frozen surface (free, no new vendor) | [03-live-spot-overlay.md](plans/continuous-feed/03-live-spot-overlay.md) |

Dispatch order: **T70** first and independently - it gates the *value* of everything else
without gating any of the code, because a missed intraday slot is unrecoverable on the free
source. Then **T32** and **T71** in parallel, both gating **T18**, then **T19** and **T20**,
then **T72**.

### T70 · user decision, then Sonnet · -
**Always-on host for the scheduler**

> **Deferred 2026-09-11 by the user's decision: stay on the laptop for now.** Nothing else in
> the initiative is blocked. The cost is that any session with the lid closed has unbackfillable
> holes, which is why `INTRADAY_ENABLED` defaults to False and should be switched on
> deliberately. The private git remote is still worth doing alone (state-review P0). See the
> Decision section in
> [00-always-on-host.md](plans/continuous-feed/00-always-on-host.md).

APScheduler's job store is in-memory, so jobs fire only while the process lives. T29's
catch-up rescues a missed daily EOD because Cboe still serves the settled chain that evening;
nothing can rescue a missed intraday slot, because the endpoint serves only "now". A laptop
closed at 11:00 is a permanent hole in that session.

Judgment call for the user: small VPS (~$4-6/mo), a Pi/spare box on the LAN, or status quo.
Hosting cost is not data spend, so it does not touch the $50/month guardrail - say so
explicitly either way. Hard prerequisite: **there is no git remote**; you cannot deploy what
you cannot push, and creating one also closes state-review P0. Requirement, not suggestion:
the app has no auth and holds single-user-licensed market data, so it must not be exposed
publicly - loopback plus Tailscale/WireGuard or an SSH tunnel.

Acceptance: the host survives a reboot unattended; a capture fires on a day the user's laptop
was never opened; `GET /api/health/capture` answers from the host.

### T71 · Sonnet · T05
**Capture idempotency and content dedupe**

Two defects that are harmless at one capture a day and daily events at 27. First, `snapshots`
has no uniqueness at all - only a non-unique index - so a re-fire, a catch-up overlap or two
stacks running during T70's migration each write a full duplicate row and Parquet file.
Second, and subtler: T34 established that Cboe's `timestamp` is payload-generation time, not
data-effective time, so **you cannot dedupe on it** - it is exactly the field that keeps
advancing when the data underneath is frozen. Dedupe on a content hash instead, stored on the
snapshot row. On an EOD capture that matches an earlier intraday row, promote the existing
row's `is_eod` rather than inserting - T29's catch-up and the capture-health endpoint both key
on "an is_eod row exists for today".

Acceptance: calling `capture_snapshot` twice against the same fixture payload leaves exactly
one `snapshots` row, one Parquet file, and one set of `gex_levels` rows.

**Done 2026-09-11.** `app/storage/fingerprint.py` (new), the `content_hash` column,
`uq_snapshots_underlying_captured_at` replacing the old non-unique index, a two-key duplicate
check with `duplicate_reason` in the structured log, and migration `c7a1e93b5d02`. 25 new
tests; backend suite 914 passed. Migration run live against the real Postgres and round-tripped
both directions; a duplicating insert is now rejected with `IntegrityError`. Found while
implementing that `is_eod` promotion already existed from T05 for the timestamp key -- T71
extended it to the content key rather than inventing it, and the plan file is corrected to say
so. See the Result section in
[01-capture-integrity.md](plans/continuous-feed/01-capture-integrity.md).

### T72 · Sonnet · T19
**Live-spot overlay against a frozen surface**

The increment the original roadmap missed, and the best value-per-hour in the initiative: free,
no vendor, no account, no licence. OI is fixed for the session by construction and IV moves
slowly; spot is the fast input, and spot is not OPRA data. So freeze the whole surface at the
last 15-minute capture and move only spot, giving a live answer to "how far am I from the flip
point right now". Distance-to-level is arithmetic on `gex_levels` rows that already exist; net
GEX at live spot interpolates the 201-point `gamma_profile` that `compute_all` already computes
and currently discards - cached in process, never recomputed on a tick.

The decision the task turns on: **two clocks, shown as two clocks** ("spot 14:32:05 ·
structure 14:15"). A smoothly moving marker invites the reading that the walls are live too,
and they are not - which matters most on exactly the fast tape where the view is most wanted.

Acceptance: feeding a changed spot moves the marker and the distance readouts without a new
capture, the structure stamp does not move, and killing the quote source drops back to the
captured spot with a visible reason.


---

## T73 · Opus · T42, T54
**Pre-open bars refresh, so the VIX family is not two sessions behind**

Reported by the user 2026-09-11: the Opportunities page was showing bars through Sep 9 on Sep 11.

Diagnosed: 119 of 125 symbols were current to 2026-09-10. The six stale ones were exactly the
Cboe index series routed to `app.providers.cboe_index` by `BAR_PROVIDER_GROUPS` -- `^VIX`,
`^VIX9D`, `^VIX3M`, `^VIX6M`, `^VVIX`, `^SKEW`. `GET /api/scan/cross-asset` reads those, so it
was reporting `as_of 2026-09-09` with VIX 16.46 while the real Sep 10 close was 17.84 -- and
that strip is the "bars" the user was looking at.

The source was fine when probed directly at 13:50 ET: the CSV served Sep 10 for every one of
the six. So the row existed by then but not when the 17:30 ET job asked for it on Sep 10.
Either Cboe publishes the session's row after 17:30, or that particular run failed for these
six; the old container's logs were gone, so this is not settled, and it does not need to be --
both causes have the same fix and the same symptom.

Why it looked two days stale rather than one: `update_one_symbol` re-fetches `last - 5 days`
and upserts, so the gap self-heals on the *next* evening run. Between 17:30 on day D-1 and
17:30 on day D, the VIX family therefore sat at D-2 while every Yahoo symbol sat at D-1.

Fixed by adding a second trigger, `bars_update_preopen`, at 08:15 NY Mon-Fri on the same job
function and the same 5-day overlap. Before the open, long after any overnight publication, so
a pre-session read is at worst one session behind instead of two. `upsert_bars` no-ops on
unchanged values, so running the universe twice a day costs one extra pass and no duplicate
rows. Distinct job id from the 17:30 run, which is untouched.

The six symbols were also backfilled by hand at the time (`inserted=1, updated=3-4` each), so
cross-asset went to `as_of 2026-09-10` immediately rather than waiting for a scheduled run.

**Still open:** this reduces the worst case but does not make the VIX family same-session
current, because the Cboe CSV may simply not carry day D during day D. If the regime strip
ought to show *today's* VIX, that needs a different source for those six (Yahoo serves `^VIX`
and `^VIX3M`, though not obviously the whole family) -- a separate decision, not this fix.
955 backend tests pass.

---

## T74 · Opus · T42, T18
**Intraday bars: 5-minute series plus the in-progress daily bar**

The user asked for continuous bars on 2026-09-11 after seeing "bars through Sep 10" during the
Sep 11 session — correct, since `daily_bars` holds settled sessions only, but indistinguishable
from a genuinely missed day.

Scope agreed with the user: **both** halves (store the series *and* surface the in-progress
daily bar), on **six symbols** (SPX/SPY/QQQ/GLD/DIA/^VIX) rather than the 125-symbol scan
universe — six requests per poll is ~72/hour, the universe would be ~1,500, and losing Yahoo to
throttling would take the daily bars pipeline down with it.

Full design, verified facts and outcome in
[plans/continuous-feed/05-intraday-bars.md](plans/continuous-feed/05-intraday-bars.md).

**Done 2026-09-11.** New `intraday_bars` table + migration `179bee3e9455`,
`YahooBarProvider.fetch_intraday_bars`, `upsert_intraday_bars`/`read_intraday_bars`,
`app/jobs/intraday_bars.py` with a five-minute job (09:30–16:05 NY, gated on
`INTRADAY_BARS_ENABLED`), and `GET /api/bars/{symbol}/intraday`. 24 new tests; suite 979 passed.

Two facts measured live and worth keeping: **Yahoo's intraday data is effectively real-time**
(at 15:12:11 ET the last SPY row was stamped 15:12:11), which also settles the open question in
[03-live-spot-overlay.md](plans/continuous-feed/03-live-spot-overlay.md); and **the trailing row
of an intraday payload is a synthetic live quote, not a bucket** — unaligned timestamp,
volume 0. It is split out by the provider and never stored.

`daily_bars` is deliberately untouched: a partial row there would poison ATR, realized vol,
breakout levels and every same-day join. The in-progress daily bar is aggregated from the
buckets at read time and typed so it cannot pass as a settled one.

**Next:** the frontend still reads the settled daily series. Wiring `session_bar` into the
symbol views and charting the 5-minute series is UI work, not data work, and is the obvious
follow-on. Next free ID is **T108** (T83, T84 and T85 are logged under the quantdesk initiative
below; T86-T89 under capture-memory, T90-T97 under decision-inputs, T98 on its own, and
T99-T107 under desk-integrity after it).

---

# quantdesk — three apps, one desk (T75–T82)

Full specs in [plans/quantdesk/](plans/quantdesk/). This repo is a clone of `gex-trading`
(history preserved, origin detached); the original stays frozen on disk as the reference copy.
GEX becomes one module of three, sharing one FastAPI process, one React app and one Postgres
database with a schema per module. Opened 2026-09-19.

Read [plans/quantdesk/README.md](plans/quantdesk/README.md) first -- it carries the module
model, the schema decision, the dependency graph and the dispatch order.

## T75 · Opus · —

Monorepo skeleton. Move GEX into `app/modules/gex` and `frontend/src/modules/gex`, extract
`app/core/` and `app/workers/`, reprefix the API to `/api/gex`, add the launcher route.
Changes no behaviour. Spec: [plans/quantdesk/00-monorepo-skeleton.md](plans/quantdesk/00-monorepo-skeleton.md).

**Done 2026-09-19** (`ff05e2e`). 990 backend / 357 frontend tests green, both linters clean,
26 routes with only `/health` outside `/api/gex`, `alembic upgrade head` verified against a
real Postgres, and `capture_eod` confirmed as `mon-fri 16:20` NY with next fire Monday
2026-09-21. Two container-boot bugs were found and fixed that the unit suite could not see.
Full account under the *Result* heading in the plan file.

## T76 · Opus · T75

Postgres schemas `gex` / `research` / `terminal`, GEX's tables moved out of `public`, and the
`quantdesk_ro` read-only role the MCP connector will use. Spec:
[plans/quantdesk/01-postgres-schemas.md](plans/quantdesk/01-postgres-schemas.md).

**Done 2026-09-19.** 1,003 backend tests green (13 added), both linters clean, and every
acceptance check run against the live lab Postgres: seven tables moved with row counts and
sequences intact, `downgrade -1` a clean inverse, a from-scratch upgrade reaching the identical
state, `--autogenerate` producing an empty diff, and `quantdesk_ro` able to SELECT everywhere
and refused INSERT/UPDATE/DELETE/CREATE/ALTER. Schema placement went on `Base.metadata` rather
than per-model `__table_args__`, and the role's privileges (migration) were split from its
password (`app/core/ro_role.py`, applied at boot) — both judgment calls are argued under the
plan file's *Result* heading, along with the `$user`-resolves-to-the-`gex`-schema trap that
made autogenerate want to recreate every table. Full account there.

## T83 · Sonnet · T76

`alembic heads` and `alembic history` die with `ModuleNotFoundError: No module named
'app.models'`. Two frozen revisions import the pre-T75 path; T75's alias for them lives in
`alembic/env.py`, which those two commands never run (`upgrade`, `downgrade`, `current` and
`revision` do, so containers and CI are unaffected — this is a developer-facing wart only).

Found during T76, logged rather than fixed silently. The fix must not edit
`alembic/versions/**`: a migration records what was applied to a real database, and rewriting
one to match code that did not exist when it ran is how the next rename earns the identical
edit. Options worth weighing: a deliberate, documented `app/models/__init__.py` compatibility
shim; or teaching the two commands to load `env.py` first. Whichever lands needs a test, since
the unit suite never invokes those subcommands.

## T77 · Opus · T76

Research module: port EdgeLab, SQLite registry (134,377 trials) to the `research` schema,
search becomes a scheduled worker container (APScheduler, cron or interval). The Windows
scheduled task stays, repointed at Postgres. Spec:
[plans/quantdesk/02-research-module.md](plans/quantdesk/02-research-module.md).

**Done 2026-09-19.** 1,040 backend tests green (37 added), both linters clean. 134,377 trials
and 23 paper candidates migrated with counts exact and 100 sampled hashes re-verified; the
ported report is byte-identical to the original's on the same data (`noise_ceiling: 5.6` both
sides, diff = timestamp + cache age only); a cycle against Postgres skips already-tried
combinations; a full cycle runs inside the `research-search` container. `run_nightly.ps1` is
repointed at the ported module and `deploy/edgelab.service` retired in place. Judgment calls
(scoped lint ignores to keep the science diffable, `RESULTS_DIR` deleted rather than repointed,
Alembic taking a list of metadatas) are argued under the plan file's *Result* heading.

## T78 · Sonnet · T77

`/api/research/*` and the leaderboard page, noise ceiling included in the payload. Spec: same
file as T77.

**Done 2026-09-19.** 1,060 backend tests (20 added) and 377 frontend tests (20 added) green,
both linters clean, production build clean. Four endpoints under `/api/research`, a leaderboard
page with per-row ceiling verdicts, filters sourced from the data, a trial drawer carrying the
IS/OOS split, and the paper watchlist ordered by promotion date. The ceiling ships in the same
payload as the rows and the denominator is the whole registry, so filtering cannot lower it --
both enforced by tests. Two shared things were hoisted out of `modules/gex` on the way
(`lib/http.ts`, `src/mocks/`); research got its own `ResearchFrame` rather than half-building
T81's module switcher. Full account under the plan file's *Result -- T78* heading.

## T79 · Opus · T76

Terminal module: port xactx, DuckDB (28 MB, six tables) to the `terminal` schema, preserving
point-in-time semantics exactly. Ingestion becomes a worker; the CLI stays. Spec:
[plans/quantdesk/03-terminal-module.md](plans/quantdesk/03-terminal-module.md).

**Done 2026-09-19.** 1,068 tests green offline (6 Postgres-gated skips), 1,074 with a database,
linter clean. 218,915 observations migrated: 209,328 (series, date) pairs, 3,268 of them revised,
every vintage preserved and every value bit-identical. The payrolls worked example reproduces
exactly -- latest-known 157032, `as_of=2024-02-15` 157700, and a pre-publication `as_of` returns
zero rows rather than approximating. The schema's portability claim held: `DOUBLE` ->
`DOUBLE PRECISION` was the only DDL edit. The engine swap is a facade in `store/db.py`, so 25
SQL call sites kept their strings character for character. `pytz` dropped (zero uses); `duckdb`
is now a dev-only dependency for the one-shot migration. Full account under the plan file's
*Result -- T79* heading.

## T84 · Sonnet · T79

Port the xactx test suite. `test_board.py`, `test_brief.py`, `test_point_in_time.py`,
`test_loader.py`, `test_graph.py`, `test_derive.py`, `test_policy.py` and the rest are built on a
`Store(tmp_path / "test.duckdb")` fixture and did not come across in T79. They need a fixture
that gives each test a throwaway Postgres schema (create, `alembic`-less `create_all` from
`tables.py`, drop), plus the `_needs_pg` skip guard `tests/test_terminal_store.py` already uses
so the offline suite stays offline.

This is the honest gap in T79 and should not sit for long: those tests encode the loader's
same-vintage-two-values refusal, the derive unit checks and the graph's sign-conflict logic, and
none of that is currently covered in this repo. What T79 does cover is `translate_sql` (the new
code) and the point-in-time invariant against the real migrated data.

## T80 · Sonnet · T79

`/api/terminal/*` and the board -- change board, regime, transmission graph, policy path,
brief -- with a global as-of control. Spec: same file as T79.

**Done 2026-09-19.** 1,074 backend / 398 frontend tests green (21 added), both linters clean,
production build clean. Six `as_of`-aware endpoints and five screens: change board, regime,
transmission graph, policy path and brief. The as-of control lives in the module frame as URL
state, so setting it re-renders every screen and a view of the world is a shareable link; a
pinned past moment is flagged in a permanent coloured bar. Live board reads 38 of 75 series
scored with the whole rates curve 2.3-3.2 sigma against compressed vol. Two pandas/Pydantic
serialisation traps and a GET-that-writes were found by running it; the last is logged as T85.
Full account under the plan file's *Result -- T80* heading.

## T85 · Sonnet · T80

`GET /api/terminal/brief` writes to the database. The ported `brief.section_affects` calls
`graph.register()` and `graph.estimate_all()`, so generating the brief registers edge definitions
and recomputes the whole transmission graph. That was reasonable when `brief` was a CLI command
run after ingesting; for an HTTP GET it means the endpoint cannot be cached, cannot be served
from a replica, cannot be read by the `quantdesk_ro` role, and re-estimates the full panel on
every page load.

Fix in `brief.py`: `section_affects` should read stored `edge_stats` at the requested `as_of`,
exactly as `app/modules/terminal/api/edges.py` already does. Then change the endpoint's
`connect(read_only=False)` back to `read_only=True` -- the comment there marks the spot.

## T81 · Sonnet · T78, T80

Launcher and module shell: a card per module showing its current state and health, plus the
module switcher. Spec: [plans/quantdesk/04-launcher-shell.md](plans/quantdesk/04-launcher-shell.md).

## T82 · Opus · T76

MCP connector: a read-only stdio server over the three schemas, driven from the Claude CLI on
the subscription rather than API credits. Spec:
[plans/quantdesk/05-mcp-connector.md](plans/quantdesk/05-mcp-connector.md).

**Done 2026-09-20.** 1,094 tests green (34 added), linter clean. Nine tools plus a schema
resource, verified over a real stdio MCP handshake. `quantdesk_ro` is the safety boundary and the
tests assert that a write phrased to pass the SQL guard (`WITH ... DELETE ... RETURNING`) is
refused by Postgres. The startup read-only assertion was wrong on first run -- it probed with a
temp table, which `PUBLIC` may always create -- and now asks Postgres's privilege functions.
Caveats are payload: the leaderboard tool carries the noise ceiling and reports how many rows
clear it. Two acceptance items are **not** verified and are named under the plan file's *Result*
heading: a second non-Claude client, and `claude mcp list`.

---

# capture-memory — the capture worker's heap (T86–T89)

Full specs in [plans/capture-memory/](plans/capture-memory/). Opened 2026-09-21 after "the
deployed services keep increasing memory usage". Measurement narrowed it to one service:
`gex-capture`'s heap went 125 MB → 626 MB in four hours while `backend`, `research-search`,
`terminal-ingest` and `postgres` stayed flat. The growth decelerates, which points at an
allocator water mark rather than a leak, but that is not yet settled.

Read [plans/capture-memory/README.md](plans/capture-memory/README.md) first -- it carries the
measurements, the anon-versus-page-cache distinction that made the first reading misleading,
and **the gate**: an overnight sample across a closed market decides whether this is retention
(T86, T87, T88) or a real leak (T86, T89, then T87). Do not dispatch past T86 without reading
it.

## T86 · Sonnet · —

Containment: `mem_limit` on every service in `compose.prod.yaml` (none has one today, so any
one of them can take the whole 5.7 GB host) and `MALLOC_ARENA_MAX=2` on the shared environment
anchor. Unconditional -- both branches of the gate need it. Spec:
[plans/capture-memory/00-containment.md](plans/capture-memory/00-containment.md).

**Done and deployed 2026-09-21.** All five capped services read back a non-zero
`HostConfig.Memory`, postgres `0`, `MALLOC_ARENA_MAX=2` live in the containers. Two acceptance
items need a trading session and are named under the plan file's *Result* heading: capture
`duration_seconds` against the recorded pre-change baseline, and the anon trajectory under the
cap. `research-search` and `terminal-ingest` carry their limit in their existing `deploy` block
because compose refuses a project where the two forms disagree -- also recorded there.

## T87 · Opus · T86

A capture materializes 62,944 Pydantic contract models, writes them to Parquet, then
immediately re-reads the file to build all 62,944 again while the first set is still alive
(`gex/store.py:110`, called from `jobs/capture.py:186`). Give `compute_and_store` an optional
`snapshot=` and pass the object already in hand -- but only on a fresh write, never on the
duplicate path, so a snapshot's levels stay reproducible from its stored Parquet. Spec:
[plans/capture-memory/01-single-materialization.md](plans/capture-memory/01-single-materialization.md).

**Done 2026-09-21 (code; deploy pending).** 1,158 backend tests green (4 added), ruff clean.
Equivalence is asserted over a chain carrying both `open_interest=None` and `open_interest=0`,
including that the `0` contract reaches the per-strike rows and the `None` one does not. The
backfill-reproducibility check and the heap number both need the homeserver; see the plan
file's *Result* heading.

## T88 · Sonnet · T87

Return freed memory to the OS: `malloc_trim` plus Arrow's `release_unused()`, on an
APScheduler job-executed listener so it fires between cycles and covers every job. Guarded, with
a no-op fallback for the Windows dev host. Spec:
[plans/capture-memory/02-return-to-os.md](plans/capture-memory/02-return-to-os.md).

**Done 2026-09-21 (code; deploy pending).** 1,164 backend tests green (6 added), ruff clean.
`app/modules/gex/jobs/memory.py` plus a listener on `EVENT_JOB_EXECUTED | EVENT_JOB_ERROR`,
logging RSS either side, the delta (`null`, never `0`, where unreadable) and how long the call
took -- the last so the "is this blocking the event loop" question has a measurement. Whether
it produces a sawtooth or only a lower staircase needs a session on the homeserver.

## T89 · Sonnet · gate

**Conditional -- only on the leak branch.** `tracemalloc` diffing consecutive cycles, plus
Arrow's allocated bytes and a `gc` type histogram to cover tracemalloc's C-extension blind spot.
Deliverable is a named allocation site, not a fix. Spec:
[plans/capture-memory/03-tracemalloc.md](plans/capture-memory/03-tracemalloc.md).

---

# decision-inputs — what the trade path reads (T90–T96)

Full specs in [plans/decision-inputs/](plans/decision-inputs/). Opened 2026-09-21 out of
[docs/market-research-eval.md](docs/market-research-eval.md), the market-research agent's own
answer to "which sources would polish your choices?"

**The eval's list was verified against the database and the code before any of it was planned,
and it did not survive intact.** Three items describe things that already exist (RSP breadth,
index-level RV/VRP, the `intraday_bars` "outage" — that table is writing again as of today).
Two of its factual claims are wrong: the correlation percentiles are read backwards, and
`etf_shares_outstanding` is not collapsing. Read
[plans/decision-inputs/README.md](plans/decision-inputs/README.md) first -- it carries the
verification table, and where it and the eval disagree, it is right.

## T90 · Sonnet · —

**P0, and not from the eval.** The nightly terminal sequence aborts at the `policy` step and
never reaches `edges`. `settlements` is a required positional (`cli.py:666`), the worker calls
`cli.main(["policy"])` with none, argparse raises `SystemExit`, and `_run_sequence`'s
`except Exception` cannot catch it. Today's scheduled run stopped after `derived`; `edge_stats`
is still stamped with yesterday's manual run. Spec:
[plans/decision-inputs/00-nightly-abort.md](plans/decision-inputs/00-nightly-abort.md).

**Done 2026-09-21 (code; deploy pending).** 1,118 backend tests green (5 added), ruff clean.
`policy` is out of `SEQUENCE` and announced at WARNING via a new `UNSCHEDULED_STEPS`; the
per-step guard is `except (Exception, SystemExit)`; and `cli.main` now returns a code for a
usage error instead of raising. Tests went into `test_terminal_cli_contract.py`, which exists
because of the same class of bug. **The acceptance criterion that matters is unverified until
the homeserver is deployed to** -- a `graph` batch from a scheduled run, and `edge_stats.as_of`
advancing past `2026-09-20T19:24:32`. Full account under the plan file's *Result* heading,
including a second, independent failure it turned up (no `fred`/`treasury` batch today).

**Verified on the homeserver 2026-09-21.** Deployed, and the sequence run by hand reached
`edges`: `terminal.edge_stats` advanced to `2026-09-22 00:10:04+00` across 50 rows and a
`graph` batch landed. Five edges remain uncomputable for want of an input series -- T91 and
T96.

## T97 · Opus · T90

The same abort one level down, found while verifying T90. `XA_FRED_API_KEY` is empty on the
homeserver, so building the FRED adapter raised out of `cmd_ingest` entirely -- and `treasury`
sorts after `fred`, so a missing key for one vendor stopped a keyless one. `cmd_ingest` now
records a source failure and continues, still exiting non-zero, with each batch's status
scoped to what that source did. Spec and result:
[plans/decision-inputs/00-nightly-abort.md](plans/decision-inputs/00-nightly-abort.md).

**Done and deployed 2026-09-21.** 1,167 backend tests green (3 added), ruff clean. Verified on
the homeserver: `treasury` ran again and advanced 2026-09-18 -> 2026-09-21 while `fred` was
still keyless, and `fred` appeared as a named failure rather than an absence. The user then set
`XA_FRED_API_KEY` on the server; the next run filled all 44 fetchable series with zero
failures.

## T91 · Sonnet · T90

`eq.rut`, `eq.msci_em` and `cmdty.gold` are declared graph nodes with **zero** observations,
blocking three edges -- including `credit.hy.oas -> eq.rut`. IWM, EEM and GLD each hold 1,262
daily bars in `gex.daily_bars`, current to today. Feed the nodes from data already captured,
through a named cross-module adapter. Spec:
[plans/decision-inputs/01-empty-nodes.md](plans/decision-inputs/01-empty-nodes.md).

**Done and deployed 2026-09-21.** 1,179 backend tests green (12 added), ruff clean. A fifth
ingest source, `prices`, reading `gex.daily_bars` -- 1,262 observations each for `eq.rut`,
`eq.msci_em` and `cmdty.gold`, and the graph went from 10 edges estimated to 13.
`credit.hy.oas -> eq.rut` (the edge the eval's Trade C needed) is significant at t -10.4.
Only the two `policy.ff.meeting_1` edges remain, which is T96. See the plan file's *Result*.

## T92 · Sonnet · —

Relative volume: `daily_bars.volume` is populated (149,560 rows, 120 symbols) and no scan
module reads it. A sixth pure indicator beside `atr` and `realized_vol`, null-safe for the five
index symbols that correctly have no volume. Spec:
[plans/decision-inputs/02-relative-volume.md](plans/decision-inputs/02-relative-volume.md).

**Done 2026-09-21.** 1,127 backend / 403 frontend tests green (9 added), both linters clean.
`relative_volume` is the sixth pure indicator; the current bar is excluded from its own
baseline and a test pins that specifically. Surfaced on `TrendComponents` (latest bar) and
`BreakoutEvent` (the event bar), through both API models and `types.ts`. A reading only -- it
does not enter the trend composite, so no symbol's score moved, and **no UI column was added
yet**. Turned up a latent bug worth its own task: `variance_ratio` raises `ZeroDivisionError`
on a perfectly flat close series, breaking `score_symbol`'s "never raises" contract. Full
account under the plan file's *Result* heading.

## T93 · Opus · —

Factor cap: measure how correlated the decision set's own candidates are, and stop emitting one
bet as seventeen tickers. Needs no new data. Spec:
[plans/decision-inputs/03-factor-cap.md](plans/decision-inputs/03-factor-cap.md).

**Done 2026-09-21.** 1,154 backend / 403 frontend tests green (27 added), both linters clean.
New pure module `scan/factors.py`; the cap marks and explains, never removes. Three decisions
beyond the spec: the comparison is on the **side-adjusted** correlation (two correlated names
traded opposite ways are a hedge, not a duplicate), two opportunities on one symbol never
duplicate each other, and `rejected` rows are not candidates. Run against the eval's own
"these eight are one trade": **false -- 6.96 independent bets of 8**, mean pairwise correlation
0.0214. The real cluster is energy (XLE/XOP 0.926). No UI renders it yet. Full account under
the plan file's *Result* heading.

## T94 · Sonnet · T91

Sector-level transmission edges. The graph's four equity nodes are all index-level; every trade
the desk makes is in a sector or industry ETF, all of which are already in `daily_bars`. Spec:
[plans/decision-inputs/04-sector-edges.md](plans/decision-inputs/04-sector-edges.md).

## T95 · Sonnet · —

Crude term structure: `cmdty.wti` is a single series, so contango versus backwardation is
unanswerable. The source survey is the task, and a negative result closes it. Spec:
[plans/decision-inputs/05-crude-term-structure.md](plans/decision-inputs/05-crude-term-structure.md).

## T96 · Opus · T90

Event calendar and the implied policy path. `terminal.releases` has 0 rows; `policy.py` is
finished, tested, and has never produced a row because its input is a hand-supplied CME file
that their terms forbid fetching. Calendar first (free sources, solved problem), then an OIS
source survey. Spec:
[plans/decision-inputs/06-calendar-and-policy-path.md](plans/decision-inputs/06-calendar-and-policy-path.md).

---

# T98 — which build is each container running

Not part of an initiative; asked for directly on 2026-09-21, the same day a
`docker compose up -d --build` updated four containers and failed on the fifth with nothing
in the running system saying so.

## T98 · Opus · —

`GET /health` gains a `services` array — one entry per container, each with a `label` in the
form `backend: 2026-09-21T20:14:03-04:00 (cf59b11)` and the same facts as fields. The
launcher at `/` renders it under a quiet "Build" heading.

**Per service, never one number for the stack**, because a stack-wide version is exactly what
would have hidden that day's failure. `BUILD_SHA`/`BUILD_TIME` are build args (a container has
no git repository to ask), the API reports itself from its environment, each worker writes a
small JSON file into `DATA_DIR/run/versions/` at boot because it serves no HTTP, and the
frontend's stamp is compiled into its bundle because nothing else can know it. An image built
without the args says `unknown` rather than guessing: a wrong sha is worse than an absent one
when the question is "is this container running my fix?".

`scripts/deploy.sh` is what computes and passes them — compose can only interpolate the
environment, so something has to run `git`. The bare compose command still works and produces
honestly-unstamped images.

**Done and deployed 2026-09-21.** 1,188 backend tests green (9 added), 407 frontend (4 added),
both linters clean, `npm run build` clean.

---

# desk-integrity — what the desk asserts versus what it measured (T99–T107)

Full specs in [plans/desk-integrity/](plans/desk-integrity/). Opened 2026-09-21 out of
[docs/state-review-2026-09-21.md](docs/state-review-2026-09-21.md) and its source-confirmed
companion
[docs/state-review-2026-09-21-verification.md](docs/state-review-2026-09-21-verification.md).

Every item is the same failure in a different surface: **the desk states something it did not
measure, in a voice that sounds measured.** A wall named from its position rather than its
gamma; an empty aggregate written as `0`; a five-session outage nothing watched; a track
record hardcoded into a prompt file; an MCP note asserting a column is null when it never is.
None of these is a crash — all of them produce output that looks like the good kind.

**Read the verification document before dispatching anything.** The original review's inferred
causes for `F1` and `F3` are both wrong in ways that change the fix, and its `F1` fix would
reintroduce a bug the code already handles deliberately. The rule it produced: a data-only
review is a list of symptoms, not a list of fixes.

`T99`, `T100` and `T101`/`T102` share `engine.py` and `regime.py` and are three separate Opus
dispatches -- sequential, never parallel.

## T99 · Opus · —

**The P0.** A wall is named by its gamma, never by its position relative to spot. Six emitted
decisions carry the wrong wall name, with confident prose asserting it; one of the six is a
genuine trade error, not just a mislabel, and the resolved pair has contaminated the track
record. Adds a fifth decision key, **`GAMMA_PIN`** -- spot resting on the largest
positive-gamma strike is a magnet, not a wall -- scored in its own right. Spec and result:
[plans/desk-integrity/00-wall-identity.md](plans/desk-integrity/00-wall-identity.md).

**Done 2026-09-21.** 1,207 backend tests green (19 added), 408 frontend (1 added), both
linters clean. `WALL_MIN_ABS_FRACTION` landed at 1e-4 rather than the 1e-2 first proposed --
measured against 859 stored rows, where 1e-2 would have nulled a real $44.8mn 0DTE wall. The
historic `gex.gex_levels` recompute is done.

**Deployed 2026-09-22.** QQQ `ZERO_DTE` now has zero level rows with
`|put_wall_gex| < 1000`, where it previously named walls carrying -1.87, -5.48e-37 and
-2.17e-20 dollars; 13 rows correctly report no put wall at all. No full-chain wall was lost.

## T100 · Opus · T99

An empty aggregate is null, not zero. Two 09-21 snapshots with full chains recorded
`net_gex = 0` where nothing was measurable. The column is already nullable; `KeyLevels.net_gex`
is typed `float`, so this is a type widening and a consumer audit. Spec and result:
[plans/desk-integrity/01-null-aggregates.md](plans/desk-integrity/01-null-aggregates.md).

**Done 2026-09-21.** 1,212 backend tests green (5 added, 5 rewritten), 408 frontend, both
linters clean. The audit found four consumers beyond the one the spec named -- including the
Pydantic wire schema and the frontend type, both of which were enforcing the bug, and a text
renderer that raised `TypeError` on the first null.

## T101 · Opus · T100

The expiry dimension, written at capture time. On 09-21 the QQQ 740 wall was +662mn and the
desk could not say what fraction survived that Friday -- the first thing anyone asks about a
wall. A rollup, not a per-contract table: invariant 5 holds. Spec and result:
[plans/desk-integrity/02-expiry-and-session.md](plans/desk-integrity/02-expiry-and-session.md).

**Done 2026-09-21.** 1,219 backend tests green (7 added), 408 frontend, both linters clean.
The shape was decided by measurement: a full strike-by-expiry table would have written ~2.61M
rows on 09-21 alone against the 266,050 this database holds in total, so the split is four
horizon columns on `gex_by_strike` (zero new rows) plus a `gex_by_expiry` table (~5.2k/day).
`backfill --recompute` added, which is also the mechanism T99's outstanding recompute needed.

**Deployed 2026-09-22.** Migration applied, backfill swept back to the oldest snapshot in
the database. Measured on the real row: the QQQ 740 wall of +661.9mn was **28.2% expiring that
Friday, 71.8% surviving** -- the sentence F5 says the desk could not produce at any price.

## T102 · Sonnet · T101

`gex.snapshots` gains `session_date`: the trading session the chain belongs to, distinct from
`captured_at`. Makes the weekend-capture rule enforceable in SQL instead of documented in
prose. Spec and result:
[plans/desk-integrity/02-expiry-and-session.md](plans/desk-integrity/02-expiry-and-session.md).

**Done 2026-09-21.** 1,227 backend tests green (8 added), 408 frontend, both linters clean.
Unlike T101's columns this one is a pure function of `captured_at` and the trading calendar, so
the migration backfills all 346 existing rows itself -- no Parquet reopened, no separate pass.

**Deployed 2026-09-22.** 346 of 346 rows filled. Snapshot 178, the Sunday capture, now reads
its true session of Friday 09-18, and `session_date` is on the wire.

## T103 · Sonnet · —

ATM and 30-day constant-maturity IV persisted per snapshot, from inputs already in memory at
capture. Unlocks every rich/cheap question the desk currently cannot answer, and fills
`RegimeRow.iv_rv_ratio`, which has always been null. Spec:
[plans/desk-integrity/03-iv-persistence.md](plans/desk-integrity/03-iv-persistence.md).

## T104 · Sonnet · —

Something has to watch `/api/gex/health/capture`, and tell **Telegram** when it goes quiet.
September quarterly opex week is missing entirely -- five open sessions, the whole 28-symbol
universe -- and the endpoint built to catch exactly that was never polled. Alert on the
universe-wide gap, never on ordinary per-symbol staleness. Spec and result:
[plans/desk-integrity/04-capture-alerting.md](plans/desk-integrity/04-capture-alerting.md).

**Done 2026-09-22, not yet deployed.** 1,243 backend tests green (11 added). Detection always
runs and logs; Telegram delivery is opt-in via `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` and off
by default, because the desk must not require an account -- and because a notifier that
hard-depends on an external service fails the same way the thing it watches does. Keyed on
T102's `session_date`, its first consumer. Six of the eleven tests assert it does *not* fire.

## T105 · Sonnet · —

MCP row shape. `gex_levels("QQQ")` returns ~9,000 tokens, truncated at 100 rows, to answer a
question whose answer is three rows. Latest-per-symbol by default, multi-symbol, and
`gex_decisions` widened to be terminal for the common question. The notes stay -- they cost
~60 tokens and they are the point. Spec:
[plans/desk-integrity/05-mcp-ergonomics.md](plans/desk-integrity/05-mcp-ergonomics.md).

## T106 · Sonnet · T105

Two MCP tools: desk status (the freshness check the skill mandates and hand-writes every
session) and track record (per key, with the standard error beside the mean, and the key list
derived from the data -- `T99` adds `GAMMA_PIN` in parallel). Retires the hardcoded block that
`T107` deletes. Spec and result:
[plans/desk-integrity/05-mcp-ergonomics.md](plans/desk-integrity/05-mcp-ergonomics.md).

**Done 2026-09-22.** Both tools land. `gex_track_record` derives its keys with
`GROUP BY ROLLUP(key)` and scores on `result_r IS NOT NULL`, both pinned by tests that read its
own source -- so T99's `GAMMA_PIN` appears without a code change and the denominator cannot
silently double. `desk_status` carries the three freshness rules beside the numbers they
qualify. The call-count measurement still needs a working connector.

## T107 · Sonnet · T106

Three documented facts the data contradicts: `cmdty.gold` has 1,262 observations and the docs
say it has never had data; the `ust_cc.*` lag is inverted; `vol.vix` and `vol.skew` stall
together. Plus the hardcoded track record -- deleted, not updated, because updating it re-arms
the same trap. Spec and result:
[plans/desk-integrity/06-doc-drift.md](plans/desk-integrity/06-doc-drift.md).

**Done 2026-09-22.** All four claims re-verified live at edit time. `cmdty.gold` has 1,262
observations back to 2021 and was already fixed in `universe.py` -- only the skill still lied.
`ust_cc` is a documentation error only: the board's staleness is per-series with no hardcoded
assumption, checked. `vol.vix`/`vol.skew` were one row behind and are now filled; the cause is
neither "one series" nor "a shared source" but upstream files that update later than their
siblings. The track record block is deleted in favour of `gex_track_record` -- and every SQL
fence is gone from the skill, which is the deeper half of F7d.
