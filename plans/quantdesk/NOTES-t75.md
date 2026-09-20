# NOTES — T75 monorepo skeleton

Written as the task ran. Survives context loss; raw material for the *Result* heading
`plans/README.md` asks for. 2026-09-19.

## Measured, before and after

Fresh clone, `uv sync` + `npm install` in `backend/` and `frontend/`.

| | before | after |
|---|---|---|
| `uv run pytest` | **981 passed** (107 s) | **990 passed** (36 s) |
| `npm test -- --run` | **47 files, 356 tests** | **47 files, 357 tests** |
| `uv run ruff check .` | clean | clean |
| `npm run lint` | 0 errors, 5 warnings | 0 errors, 5 warnings (identical) |

The five frontend warnings are all pre-existing `react-refresh/only-export-components`;
verified by running the linter in the frozen `gex-trading/` reference, which reports the same
five.

**No test was skipped or deleted.** Three moved file; see *Tests that moved*.

## Facts verified in the code, not assumed

- **There is not a single relative import in `backend/app/`.** `grep -rn "from \.\|import \."`
  over `backend/app --include=*.py`, minus the absolute `from app.` lines, returns nothing.
  The plan file lists "relative imports inside `app/gex/` and `app/scan/` that survive the
  move by accident" as a likely first-contact failure; on this codebase that failure mode does
  not exist, and the whole backend move was a mechanical rewrite of absolute dotted paths.
- `main.py` mounted exactly ten routers at `/api`, as stated.
- `app/models/db.py` held `Base`, `UTCDateTime`, every table **and** the generic helpers
  `get_engine` / `get_sessionmaker`. The cached process-wide `get_session_factory()` lived in
  `app/jobs/capture.py` — which is why `main.py`'s `/health` probe imported a job module.
- `app/jobs/scheduler.py` registers **ten** jobs, two conditional on settings
  (`capture_intraday` on `INTRADAY_ENABLED`, `intraday_bars_update` on
  `INTRADAY_BARS_ENABLED`). With both off, eight. `build_scheduler()` is construction only.
- There are **five** separate `get_session_factory` functions (capture, `gex/store.py`, and
  the bars/decisions/flows repositories), each with its own module-level cache. Only
  capture's moved — the spec named it, the others are module-local and each is monkeypatched
  independently by tests. The duplication is pre-existing and left alone.
- `frontend/src/mocks/handlers.ts` matches 27 paths with a `*` origin wildcard.
- Ruff here is 0.16.6, whose default rule set includes `I` (isort), `RUF` and `BLE` — not the
  `E4/E7/E9/F` I expected from the bare `[tool.ruff]` block in `pyproject.toml`.

## Judgment calls

### 1. How the scheduler leaves the API process

`app/main.py` now has **no lifespan at all** — the parameter is absent, not an empty function.
An empty lifespan is an invitation to add "just one small thing"; the property the initiative
needs is categorical.

`app/workers/gex_capture.py`:

- `run(*, stop, wait_for_schema)` is the old lifespan in substance: build, start, log the job
  ids, `create_task` the catch-up (not `await` — it can sit through several sequential Cboe
  fetches), then on the way out `scheduler.shutdown(wait=False)` and cancel the catch-up.
  Same two lines in the same order as the old `finally`.
- Both parameters are injectable for the same reason the rest of the codebase injects
  `session_factory` and `data_dir`: the tests drive the real code path with no clock, no
  database and no signal.
- **Signals**: `loop.add_signal_handler` where the platform has it (Linux containers), falling
  back to `signal.signal` + `call_soon_threadsafe` where it does not (Windows, where it raises
  `NotImplementedError`). The user develops on Windows and deploys on Linux. Verified working
  in the container — `docker compose restart gex-capture` logs `shutting down`.
- Entry point `python -m app.workers.gex_capture`.

**`_wait_for_schema()` — the one piece of genuinely new logic.** Before the split the API
container ran `alembic upgrade head && exec uvicorn`, so the schema was guaranteed to exist by
the time the startup catch-up ran. Two containers have no such ordering, and
`startup_catchup_job` never raises — so the failure would not be a crash but a *silently*
skipped catch-up on every cold start, which on a data source with no history is a P0. So the
worker polls for the `snapshots` table for up to 60 s (matching `compose.prod.yaml`'s
`start_period`, which exists for the same migration) and **starts the scheduler either way**.
A worker that refused to boot because Postgres was slow would turn a recoverable delay into a
missed 16:20.

It deliberately does **not** run alembic: two containers racing `upgrade head` against one
Postgres is a worse failure than the one being prevented.

*Rejected:* `depends_on: backend: condition: service_healthy`. Works in prod, where
`compose.prod.yaml` defines a backend healthcheck; does nothing in dev, where none exists. A
guard absent exactly on the machine the user develops on is not a guard.

*Rejected:* a separate `worker` stage in `backend/Dockerfile`, which the plan file lists as in
scope. The worker needs an identical image — same dependencies, same source, same non-root
user — and differs only in its command, so a second stage would be another thing to keep in
sync for nothing. Both services use the existing `dev`/`runtime` targets and the worker
overrides `command:`. Recorded because it deviates from the plan file's wording.

*Forced correction:* the worker does need its own image **tag**. Having `backend` and
`gex-capture` both build *and* both name `gex-backend:dev` made buildx race to export the tag
and fail the build outright (`image "docker.io/library/gex-backend:dev": already exists`). The
overlays now tag it `gex-capture:dev` / `gex-capture:prod` — same Dockerfile, same target,
fully layer-cached, only the name differs.

### 2. How far `app/core/` goes

Three files, nothing else.

- `core/config.py` — `app/config.py` moved verbatim, not one line changed.
- `core/db.py` — `get_engine`, `get_sessionmaker` (lifted out of `models/db.py`) and the
  cached `get_session_factory()` (lifted out of `jobs/capture.py`).
- `core/schemas.py` — `SCHEMA_GEX` / `SCHEMA_RESEARCH` / `SCHEMA_TERMINAL` and a `SCHEMAS`
  tuple. **Nothing imports them yet**, which is the intended end state: T76 owns the database.

**Why `get_engine`/`get_sessionmaker` moved too, not just the cached factory.** The spec asks
for "engine + session factory, extracted from `jobs/capture.py`". The cached factory alone
cannot live in `core` — it calls the other two, so `core` would have to import
`app.modules.gex.models.db` and the layering would point the wrong way on day one. Those two
are entirely generic (a URL becomes an Engine; an Engine becomes a sessionmaker; neither
mentions a table), so moving them leaves `core/db.py` importing nothing from any module.
`Base`, `UTCDateTime` and every table stayed in `app/modules/gex/models/db.py`.

`UTCDateTime` is the obvious next candidate — invariant 4 is codebase-wide, not gex-specific.
It stayed put. Hoisting it now would be guessing at two consumers that do not exist, which is
what the initiative README says to stop doing, and T76 will have a real reason.

### 3. Frontend module boundary

**Into `src/modules/gex/`:** `api/` (client, queries, types, `useLiveLevels` + tests),
`pages/`, `state/urlState.ts`, `mocks/`, `routes.tsx` (new), and every `components/`
subdirectory that renders GEX data — charts, decisions, flows, overview, regime, rotation,
scan — plus the loose `EmptyState` / `ErrorState` / `LoadingState` / `GammaProfile` /
`KeyLevels` / `LevelHistoryTable`.

**Shared, at `src/`:** `lib/format.ts`, `lib/time.ts`, `theme/`, `components/ui/`. This is the
line the brief drew and it is the right one — a percentage, a strike, a New York timestamp and
a Surface must render identically in every future module, so they cannot belong to one.
`components/ui/` is a design system, not GEX content.

**`src/shell/`:** `AppFrame`, `SideRail`, `CommandPalette`, `ThemeToggle`, `navConfig`,
`icons`, `useOverlayDismiss`, and the new `Launcher`.

**The one edge left crossing.** `AppFrame` renders `ContextBar`, which is GEX-specific (it
reads `useDashboardParams` and `useCaptureHealth`). `ContextBar` and `AssetSelector` therefore
moved to `modules/gex/components/layout/`, and `shell/AppFrame` imports one thing from one
module. A single commented violation, naming T81, beats either leaving GEX code in the shell
or redesigning the frame — which is explicitly T81's job.

`navConfig.ts` stayed in the shell with its `to:` values reprefixed. Its contents are 100 %
GEX routes today so it could equally have moved; it stays because T81 turns it into the module
switcher's data source, and moving it twice is worse than once.

`App.tsx` is now two lines of routing: `/` → `Launcher`, plus each module's exported
`<Route>` element. Exported as an **element**, not a component — `<Routes>` reads its children
as configuration rather than rendering them, so a component returning a `<Route>` would be
invisible to it.

## Things that went wrong, and what they cost

Recorded because each is a trap the next module port will hit.

1. **`sys.modules` aliasing is not enough for the frozen migrations.** Two revisions do
   `import app.models.db` and then use the dotted form `app.models.db.UTCDateTime(...)`. The
   `sys.modules` entry satisfies the *import*; it does not create the `models` **attribute**
   on the `app` package that a real import would leave behind. The backend container exited 1
   with `AttributeError: module 'app' has no attribute 'models'` — while the whole unit suite
   was green, because nothing in it ran a migration. `alembic/env.py` now also does the
   `setattr`, and `tests/test_alembic_upgrade.py` exists so it cannot regress silently. That
   test upgrades to `b2d4f6a8c0e1`, not `head`: the final revision uses
   `ALTER TABLE ... ADD CONSTRAINT`, which SQLite cannot do (a pre-existing property of that
   migration, not something T75 introduced). It still covers both of the revisions that
   import the old path.
2. **`fileConfig` disables existing loggers.** Adding that alembic test broke 25 unrelated
   tests — in a full run only, each passing individually — because `env.py`'s
   `fileConfig(...)` defaults to `disable_existing_loggers=True` and silenced the application
   loggers for the rest of the pytest session. `env.py` now uses Alembic's own idiom,
   `config.attributes.get("configure_logger", True)`. The CLI path is unchanged (no
   attribute → logging configured), so the container behaves exactly as before.
3. **A `/api/` → `/api/gex/` regex whose lookbehind excluded `\w` and `/` but not `.`**
   rewrote relative *import specifiers* (`'../api/types'` → `'../api/gex/types'`) across 61
   frontend files. Caught by `tsc`. If you do this again: only rewrite inside quoted strings
   that do not start with `.`.
4. **The levels router was already `prefix="/gex"`**, so its paths read `/api/gex/...` before
   T75 and the first reprefix pass skipped them. They needed a second, narrower pass. The
   resulting `/api/gex/gex/{underlying}/latest` is a wart — see below.

## The `/api/gex/gex/...` wart

Mounting the ten routers behind `APIRouter(prefix="/gex")` means `api/gex.py`'s own `/gex`
router becomes `/api/gex/gex/{underlying}/latest`. It reads badly. It is also the literal
consequence of the two rules the brief gives — "all ten move to `/api/gex`" and "do not
rename, resign or re-specify any GEX behaviour" — so renaming that router to, say, `/levels`
was not mine to do. Flagged as a candidate cleanup: one line in
`app/modules/gex/api/gex.py`, three in `frontend/src/modules/gex/api/client.ts`, and the
matching MSW handlers.

`GET /health` stayed at the root, unprefixed and otherwise untouched, because
`compose.prod.yaml`'s healthcheck is a deployment contract. `tests/test_main.py` now asserts
both that it is there and that `/api/health` and `/api/gex/health` are **not**, so a later
"tidy-up" cannot move it while the suite stays green.

## Tests that moved file

`backend/tests/test_main.py` had six tests. Three asserted on the lifespan — catch-up
scheduled as a background task, slow catch-up not blocking boot, and the T35 unreachable-
database regression. That behaviour left `main.py`, so the tests followed it into
`backend/tests/test_workers_gex_capture.py`, rewritten against `app.workers.gex_capture`,
assertions preserved one for one including the 192.0.2.1 RFC 5737 TEST-NET-1 case. The three
`/health` tests stayed.

Net: `test_main.py` 6 → 5 (three left, two added), `test_workers_gex_capture.py` +8,
`test_alembic_upgrade.py` +2. 981 → 990.

New coverage worth naming:

- `test_main.py::test_the_api_process_starts_no_background_work` — the direct test of the
  "two schedulers" failure the plan warns about. T71's write idempotency would hide most of
  the damage, which is exactly why it needs a test rather than a symptom.
- `test_main.py::test_health_stays_at_the_root_and_is_not_under_a_module_prefix`.
- `test_workers_gex_capture.py::test_worker_logs_its_registered_job_ids_at_boot` — a silent
  worker and a worker with no jobs look identical from outside.

Eleven API test files built their own mini-`FastAPI` and included one router at `prefix="/api"`.
They now use `prefix="/api/gex"`, matching how `main.py` mounts the module router.

## Verification actually run

Docker **was** running on this host (server 29.1.2), so the compose checks were executed, not
asserted.

- `uv run pytest` — 990 passed. `uv run ruff check .` — clean.
- `npm test -- --run` — 47 files, 357 tests passed. `npm run lint` — 0 errors, 5 pre-existing
  warnings.
- `docker compose up -d --build` from a **cold start** (`down -v`, `./data` deleted): postgres
  healthy, backend up, gex-capture up, frontend up.
- `GET /health` → `{"status":"ok","provider":"cboe","symbols":[...],"db":"ok"}`.
- `GET /api/gex/health/capture` → 200, same body shape as `/api/health/capture` gave.
  `GET /api/health/capture` → 404, i.e. the old path is genuinely gone.
- OpenAPI: 26 paths, 25 under `/api/gex/`, and exactly one not — `/health`.
- One route probed per router. On an empty database the GEX/report/chains routes return the
  T37 empty-state contract (`{"detail":"no snapshot captured yet for SPX"}`), distinguishable
  from a routing miss (`{"detail":"Not Found"}`).
- **Live capture through the moved stack:** `POST /api/gex/snapshots/capture?underlying=SPY`
  → 201, 12,312 contracts, spot 761.69, `snapshot_id: 1`, Parquet written to
  `data/chains/SPY/2026/09/20260919T233029000000Z.parquet` (526,739 bytes) and the row
  indexed in Postgres with the correct `DATA_DIR`-relative path. Afterwards
  `/api/gex/gex/SPY/latest`, `/api/gex/report/SPY` and `/api/gex/snapshots` all returned 200
  with real data.
- **Two-schedulers check:** `docker compose logs backend | grep -ci "scheduler started|apscheduler"`
  → **0**. The API process starts none.
- Worker boot log, verbatim from the cold start:

      2026-09-19 20:59:51,273 INFO app.workers.gex_capture waiting for the 'snapshots' table (up to 60s)
      2026-09-19 20:59:51,457 INFO app.workers.gex_capture schema ready after 1 probe(s)
      2026-09-19 20:59:51,459 INFO app.workers.gex_capture scheduler started; jobs=['retention_prune', 'bars_update_preopen', 'capture_eod', 'capture_extended', 'bars_update', 'decisions_update', 'flows_update', 'capture_eod_safety_net']

  Eight jobs because `INTRADAY_ENABLED` and `INTRADAY_BARS_ENABLED` both default to false. Run
  with both true, it registers all ten (`capture_intraday`, `intraday_bars_update`).
- Frontend dev server: `/` 200, `/gex` 200.
- Production overlay: `docker compose -f compose.yaml -f compose.prod.yaml config` resolves
  (required-var `DATABASE_URL`, `internal`-only network for the worker, no published ports),
  and all three prod images **build**. Smoke-tested by running them: `gex-capture:prod`
  imports the worker, `gex-backend:prod` reports `/health` at the root, 25 paths under
  `/api/gex/`, and empty `on_startup`/`on_shutdown`.
- The dev stack was torn down afterwards (`docker compose down -v`) and `./data` removed.

### What was *not* verified, and why

- **`docker compose -f compose.yaml -f compose.prod.yaml up -d` was not run.** It requires the
  external `edge` network shared with the user's Caddy, and would start a second Postgres
  against the bind-mounted `./data/postgres`. Starting the production topology on the dev host
  uninvited is not mine to do. Config resolution and all three image builds were verified
  instead, plus the runtime images' behaviour directly.
- **No capture fired from a real cron trigger.** The triggers are Mon–Fri market hours and
  today is Saturday 2026-09-19 (the catch-up correctly logged
  `{"event": "catchup_skipped", ..., "reason": "not a trading day"}`). What was verified is
  that the jobs are registered with their ids in the worker process, and that a capture
  through the moved code path works end to end. **Watch Monday's 16:20 log line** — that is
  the only thing that proves the cron survived, and this task's riskiest surface.

## Open items / left undone

- `backend/alembic/versions/**` is **byte-for-byte untouched** — confirmed with
  `git status --short alembic/versions/`. No migration was added and none is wanted. T76 owns
  the database, and should delete the aliasing block in `alembic/env.py` when it rewrites
  those tables into the `gex.` schema.
- `app/core/schemas.py` defines the three schema names and nothing imports them. T76.
- The `/api/gex/gex/...` double segment, above.
- Old URLs (`/dashboard`, `/api/snapshots`) are **not** redirected to their `/gex`
  equivalents. Single user, no external links, and a redirect layer would be one more thing
  for T81 to delete.
- One `pyproject.toml` change beyond the move: `[tool.ruff.lint.isort] known-first-party =
  ["app"]`. Ruff infers first-party by resolving the import on disk, so once `app.models`
  stopped existing, the frozen revisions' `import app.models.db` was reclassified as
  third-party and `ruff check` demanded those files be re-sorted — files this task may not
  touch. Stating it is also simply more correct than inference.
- The launcher is a plain list, per the plan file. T81 owns the design.
- CORS still allowlists exactly `http://localhost:5173`, unchanged.
