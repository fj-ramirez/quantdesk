# Project state review — 2026-09-05

**Scope:** independent check of the repo after the agent build described in `docs/supervision-report.md`, against `PLAN.md` and `TASKS.md`.
**Method:** ran both test suites, lint and the production build; probed the live Docker stack (API, Postgres, container logs); read the core backend and frontend modules; re-read the orphaned Parquet files on disk; rebuilt the backend image. Nothing in the repo or the running stack was modified, except that the `gex-trading-backend` image tag was rebuilt (the running container still uses the old image).

---

## 1. Verdict

The supervision report is accurate. Every checkable claim in it held up. The code that exists is in good shape: 389 backend tests and 50 frontend tests pass, ruff and eslint are clean, the production build succeeds, and the live API reproduces the externally validated SPX numbers.

The problems are operational, not in the code that was written:

1. **Friday 2026-09-04's end-of-day capture is missing for all three symbols**, and the catch-up logic cannot recover it because it only ever looks at "today". The data is still recoverable this weekend (see §4, P0-1).
2. **The running backend container predates the Docker fix** that was committed three minutes after it started. The "fresh `docker compose up` works" claim has never been exercised by a running stack.
3. **The repo has no git remote.** CI has never run, and the only copy of the code and of the captured data is this laptop.
4. Phase 3 is not finished: T15 (price chart and history page) and T17 (Phase 3 review) are not done, and T37 (raw JSON error state) is confirmed open.

---

## 2. Claims in the supervision report, checked

| Claim | How checked | Result |
|---|---|---|
| 40 commits, 389 backend tests, 50 frontend tests | `git log`, `uv run pytest`, `npm test` | Confirmed exactly |
| Lint clean, build OK | `ruff check`, `npm run lint`, `npm run build` | Confirmed (4 eslint warnings, 0 errors) |
| T00–T14, T16, T27, T29, T30, T34, T35, T36 merged | git log + code reading | Confirmed |
| T15, T17, T28, T31, T32, T33, T37 not done | code reading | Confirmed. `Dashboard.tsx` has a `TODO(T15)`; `History.tsx` is a stub; `CaptureResult` has no `skipped`/`listed`; no retention code; carry is still the global `r − q` |
| SPX net GEX +48.3B, walls 7800/7500, flip 7661.7 | `GET /api/gex/SPX/latest` on the live stack | Net +48.16B, walls 7800/7500, flip 7662.3. Consistent; the small drift is six fewer hours to expiry on a later capture |
| T34 `effective_at` clamps to 16:15 ET after the close | live API | `effective_at = 2026-09-04T20:15:00Z` for a 23:44 ET capture. Correct |
| T36 axis fix | `GammaProfile.tsx` | `scale: true` on both value axes; wall labels moved out of the pin. Not screenshot-verified by me |
| Nine agent worktree branches, all merged | `git log master..branch` for each | 0 unmerged commits on all nine; branches still exist |
| "Postgres exists in two places: a native volume on 5432 and a separate compose volume" | `Get-Service`, `Get-Process`, `netstat` | **Not accurate.** There is no native Postgres service or process. Port 5432 is Docker's. The "two places" were two generations of the compose volume (the T37 text mentions `docker compose down -v`), which is what orphaned the host-side Parquet files (§3.1) |

---

## 3. Observations

### 3.1 Friday's EOD capture is missing and the catch-up cannot recover it (high)

The compose database holds exactly three snapshots, all manual (`is_eod=false`), captured between 01:57 and 02:04 ET on Saturday. `GET /api/health/capture` reports `stale: true` for SPX, SPY and QQQ and logs an ERROR per symbol on every call. There is no EOD row for Friday 2026-09-04, a trading day.

Why the T29 catch-up did not help: `catch_up_missed_eod` checks whether **today** is a trading day and whether **today's** EOD row exists. The container started on Saturday, so it logged `catchup_skipped: not a trading day` and stopped. But Cboe still serves Friday's settled chain over the weekend (the Saturday SPX capture has spot 7718.6001, the Friday close, and 55 live expiries). A backend that was down at 16:20 Friday and restarted Saturday loses the day even though the data is available. `last_completed_trading_day()` already computes the right target date; the catch-up just does not use it.

A second gap underneath: snapshots have no `session_date` column. Which trading session a row represents is inferred from the NY calendar date of the vendor timestamp. A weekend catch-up capturing Friday's chain would get a Saturday `captured_at` and be bucketed as Saturday. `effective_data_time()` already derives the correct close day; it should be persisted.

Recoverable now:

- `backend/data/chains/` holds **12 un-indexed Parquet files from Friday** written by host-side runs before the compose volume was recreated: SPX at 14:48, 14:50, 14:52 ET (intraday, 0DTE alive: real intraday data the project otherwise has none of), SPX at 17:33 to 19:48 ET (close, spot 7718.6001), SPY at 17:33 and 17:51 ET, QQQ at 17:51 ET. The compose stack reads `./data`, not `./backend/data`, so these are invisible to it.
- Compose snapshot 1 (SPX, 23:44 ET Friday, spot at the close) is a valid Friday EOD row that is merely flagged `is_eod=false`.

### 3.2 The running container is older than the Docker fix (high)

The backend container was created at 01:56:31 ET on 2026-09-05. The commit "Make a fresh docker compose up actually work" (856dfbe), which adds `alembic upgrade head` to the Dockerfile CMD, landed at 01:59:16 ET. The container's CMD is the old `uv run uvicorn ...` with no migration. Its logs show `relation "snapshots" does not exist` 500s at startup; the schema was then applied by hand.

I rebuilt the image tag: the new image's CMD is correct. The container has not been recreated, so the fix has never run in a live stack. A true fresh-checkout test (`docker compose down -v && docker compose up`) also has not been done, and must not be done until §3.1's data is recovered, since `-v` drops the database.

### 3.3 No remote, no CI run, no backup (high)

`git remote -v` is empty. The CI workflow committed in T27 has never executed. T28 (backup of `DATA_DIR` and `pg_dump`) is not done. The captured chains are gitignored and exist only under `./data` and `./backend/data` on this machine. The whole premise of Phase 1 is that history accumulates from day one; today one disk failure erases it.

### 3.4 First scheduled EOD run is Tuesday 2026-09-08 (medium)

Monday 2026-09-07 is Labor Day and is in the holiday list. The 16:20 job has never fired in the compose stack; every capture so far was manual. Tuesday is the first real test. The stack must be up at 16:20 ET, or be started that evening before midnight ET (the catch-up handles that case correctly as written).

### 3.5 T37 confirmed: raw JSON in the UI (medium)

`client.ts` throws `ApiError` with the raw response body as message; `Dashboard.tsx` renders `Failed to load {symbol} GEX: {error.message}` and `History.tsx` renders a generic failure. A symbol with no snapshot shows `{"detail":"no snapshot captured yet for SPY"}` verbatim, framed as an error, with no capture affordance. Currently all three symbols have a snapshot so the user will not see it until the next `down -v`.

### 3.6 `captured_at` is not when the fetch happened, and nothing records that (medium)

The three Saturday captures were fetched at 01:57, 02:04 and 02:04 ET (container log). Their vendor timestamps are 23:44 ET, 02:00 ET and 23:44 ET. So Cboe's payload timestamp went stale by over two hours for SPX and QQQ but not for SPY. T34 assumed the timestamp "advances on every request"; overnight it does not. There is no `fetched_at` column, so after the fact nobody can tell when a capture actually ran or how stale the vendor payload was. Cheap to add; the capture path already has the wall-clock time.

Related: SPY and QQQ evening captures carry after-hours spot (SPY 770.19 and 769.62 vs the 769.72 close; QQQ 717.5 and 718.04 vs 717.93). SPX is an index and freezes at the close. Immaterial to GEX (the `S²` term moves by 0.06%) but worth a note in `docs/schema.md`, since a "close" for an ETF captured at 20:00 ET is not the 16:00 print.

### 3.7 Open engineering tasks, confirmed in code (medium)

- **T31** — `CaptureResult`, the structured log line and the `snapshots` row carry no `skipped`/`listed` counts. The Cboe provider logs skips at WARNING only. Still the one remaining silent-degradation path.
- **T32** — no retention. Current volume: 3,624 `gex_by_strike` rows for 3 snapshots (about 1,200 per snapshot across the two non-empty filters). At T18's cadence that is roughly 95k rows per day across three symbols, about 24M per year. Postgres will cope for a while, but the decision should precede T18 as the task says.
- **T33** — carry still `RISK_FREE_RATE − DIVIDEND_YIELD = 2.7%`; the live net GEX (+48.16B) still sits about 10% above Vendor B.
- **T15** — no `PriceChart`, no `/api/prices` endpoint, `History` page is a stub. `lightweight-charts` is in `package.json` but imported nowhere.
- **T17** — Phase 3 review not done.

### 3.8 Hygiene (low)

- CORS allows only `http://localhost:5173` and the frontend's API base URL defaults to `localhost:8001`. The responsive phone layout from T16 is unreachable from a phone on the LAN. Make both configurable.
- `/demo/gamma-profile` and `/demo/gex-by-strike` routes ship in the production app.
- `frontend/README.md` is the untouched Vite template.
- Doc drift: `PLAN.md` says React 18 (actual 19); T00 says `/health` on 8000 (moved to 8001 in commit dd500ee); `PLAN.md` §2 lists `levels.py`, `history`, `stream` routers and `tradier.py` that do not exist yet (expected, but the tree drawing reads as current).
- Nine `worktree-agent-*` branches remain, all fully merged.
- Frontend bundle is 1.42 MB minified (ECharts). Fine for a local single-user app; code-split if it ever goes remote.
- 4 eslint `react-refresh/only-export-components` warnings.
- `GET /api/health/capture` logs at ERROR on every call while stale. Correct per T28's rule, but a dashboard that polls it would spam the log; consider logging the transition, not every read.
- `POST /api/snapshots/capture` is unauthenticated. Fine on localhost; relevant the moment CORS or the bind address is widened.

### 3.9 What is good

- The engine's numbers reproduce on a different capture six hours later, and the diagnostics block (`expired`, `missing_iv`, `extreme_iv`, `net_gex_iv_unfiltered`) makes every exclusion auditable from the API alone.
- Nullability is handled honestly end to end: `ZERO_DTE` after the close stores `net_gex = 0` with null walls, and the UI explains the missing flip rather than drawing a wall at zero.
- The T35 fix is real: `connect_timeout=5` on Postgres engines and the catch-up's DB work moved to a worker thread, with a test that boots the app against an unreachable database.
- Module docstrings consistently record why a decision was made and which task made it. The codebase can be picked up cold.

---

## 4. Actionable items

### P0 — this weekend, before Tuesday's first scheduled capture

1. **Recover Friday 2026-09-04.** Move the 12 Parquet files from `backend/data/chains/` into `data/chains/` (same layout) and index them with a one-off script using `SnapshotRepository.add` against the compose database; mark the 19:48 ET SPX, 17:51 ET SPY and 17:51 ET QQQ rows `is_eod=true`, then run `uv run python -m app.gex.backfill`. Alternatively flip compose snapshot 1 to `is_eod=true` for SPX and index only the SPY/QQQ files. Acceptance: `GET /api/health/capture` shows `stale: false` for all three symbols; `/api/gex/SPX/levels/history?eod_only=true` returns one Friday row.
2. **Recreate the backend container** with `docker compose up -d backend` so the rebuilt image with the migrate-on-boot CMD is actually running. Then, only after item 1 is done and `data/` is backed up, test the cold path the report claims works: `docker compose down -v && docker compose up` on a clean checkout, and confirm `/api/snapshots` returns `[]` rather than a 500.
3. **Add a git remote and push.** CI runs for the first time; the code gets a second copy. Then copy `data/` somewhere off this disk. T28 makes this routine; a manual copy is enough for now.
4. **Be up at 16:20 ET Tuesday**, or start the stack that evening. Check `GET /api/health/capture` after 16:25 ET; `eod_captured_today` must be `true` for all three.

### P1 — next tasks to dispatch, in order

5. **New task (suggest T38): make the catch-up target the last completed trading day, not today.** `catch_up_missed_eod` should use `last_completed_trading_day(now)` and check for that day's EOD row. Add `session_date` (from `effective_data_time`) and `fetched_at` (wall clock at fetch) to `snapshots` with a migration; make `has_eod_snapshot_today`, the health endpoint and `levels/history` key on `session_date`. Test: process started on Saturday with no Friday row captures Friday's chain and stores it under Friday's `session_date`. This closes the exact failure that happened this week.
6. **T31** — partial-chain degradation alerting. Unchanged priority from the supervision report.
7. **T37** — empty state and error envelope parsing. Cheap, user-facing.
8. **T33** (Opus) — carry from put-call parity. Measurable target: SPX net GEX moves from +48.2B toward +43.6B.
9. **T15** — price chart and history page. Then **T17** (Opus review of Phase 3).
10. **T32** before **T18**.

### P2 — hygiene, batch into one Sonnet task

11. Make CORS origins and the frontend API base URL configurable via env; remove demo routes from the production router or gate them on `import.meta.env.DEV`; replace `frontend/README.md`; delete the nine merged `worktree-agent-*` branches; fix the four eslint warnings; update `PLAN.md` (React 19, port 8001, mark not-yet-built modules as planned); add the ETF after-hours-spot note to `docs/schema.md`.

---

## 5. Notes for whoever dispatches the next agents

- Put "never kill processes by image name; only PIDs you started" in every prompt, as the supervision report says. The report's other standing constraints (one Opus at a time, Phase 5 blocked on Tradier, `MARKETDATA_TOKEN` absent) all still apply.
- The "two Postgres instances" warning in the report should be read as "the compose volume was recreated once and the old index is gone". There is one Postgres. Do not go looking for a native one.
- `TASKS.md` was not modified by this review. Items 5 and 11 above are proposed as new entries (T38, T39) if the user wants them tracked there.
