# 00 · Monorepo skeleton — T75

## Goal

Turn the clone into a module host **without changing a single behaviour**. GEX becomes
`modules/gex` on both sides of the wire; `app/core/` appears with the three things every
module needs; the launcher route exists but is the only new page. Nothing else arrives yet.

This is the one task in the initiative that touches every file and must change nothing. If
GEX behaves differently afterwards, the task failed, however tidy the tree looks.

## What the user sees

Nothing, except that every GEX URL gains a segment: `/dashboard` → `/gex/dashboard`,
`/api/snapshots` → `/api/gex/snapshots`. Visiting `/` lands on a launcher listing one live
module and two greyed-out ones.

## Layout after this task

```
backend/app/
  core/
    config.py        the existing app/config.py, moved; settings shared by all modules
    db.py            engine + session factory, extracted from jobs/capture.py
    schemas.py       SCHEMA_GEX / SCHEMA_RESEARCH / SCHEMA_TERMINAL constants (T76 uses them)
  modules/
    __init__.py
    gex/
      api/           the ten routers, unchanged except their prefix
      providers/  models/  gex/  scan/  jobs/  storage/    moved verbatim
      router.py    APIRouter(prefix="/gex") including the ten
  main.py            mounts each module's router; lifespan starts nothing (see below)
  workers/
    gex_capture.py   the APScheduler + catchup lifespan, lifted out of main.py

frontend/src/
  modules/gex/       pages/ components/ api/ — the current tree, moved
  shell/             AppFrame, module switcher, Launcher
  lib/  theme/       stay shared
```

## Design decisions

**The scheduler leaves the API process.** Today `main.py`'s lifespan starts APScheduler and
fires the startup catch-up task. Once three modules share one process that is wrong twice
over: a `--reload` dev restart re-runs catch-up, and an API-only redeploy interrupts capture.
T75 moves it to `app/workers/gex_capture.py` with its own container entrypoint. The API
process starts no background work at all after this task — that is the property that lets
research and terminal add workers later without the API growing a third scheduler.

*Judgment call, and the riskiest part of T75.* Capture is the one thing in this repo that
cannot be backfilled (the free Cboe endpoint serves only "now"). The worker must be verified
firing on the lab host before this task is called done — a green test suite does not prove a
cron trigger survived the move.

**`/api/gex` rather than keeping `/api`.** Symmetry now is cheaper than a migration later,
and the frontend's API layer is one `client.ts` plus `queries.ts`. Keeping GEX privileged at
the root would make every later module look like a bolt-on and would collide the moment two
modules both want `/api/health`.

**Module routers compose; they do not register themselves.** `main.py` imports each module's
`router.py` explicitly. No plugin discovery, no entry points — with three modules a visible
list of three imports is strictly better than a mechanism, and a missing module is then an
`ImportError` at boot rather than a silently absent route.

**Root `/health` stays at the root, unprefixed.** `compose.prod.yaml`'s backend healthcheck
hits `http://127.0.0.1:8001/health` and asserts `db == "ok"`. That contract does not move.
Per-module health lives at `/api/<module>/health`.

**The frontend moves in the same task, not a follow-up.** Splitting it would leave the repo
in a state where the backend is modular and the client is not, and every URL in the test
suite would have to be changed twice.

## Tasks

### T75 · Opus · —

Move GEX into `modules/gex` on both sides, extract `core/` and `workers/`, add the launcher
route, and change nothing else.

Scope:
- `backend/app/**`, `backend/tests/**`, `backend/alembic/env.py` (import path only),
  `backend/Dockerfile` (worker stage), `frontend/src/**`, `compose*.yaml`, `CLAUDE.md`,
  `context/architecture.md`, `context/backend.md`, `context/frontend.md`.
- Do **not** touch `backend/alembic/versions/**`. No schema change belongs in this task; T76
  owns the database.
- Do **not** rename, resign or re-specify any GEX behaviour. Imports and route prefixes only.

The five invariants in `CLAUDE.md` survive verbatim; update the paths they name
(`app/gex/engine.py` → `app/modules/gex/gex/engine.py`) and nothing else about them.

## Verified facts

- `main.py` currently mounts ten routers at `/api`: snapshots, health, gex, chains, report,
  bars, scan, symbols, decisions, stream.
- The frontend router declares thirteen routes under one `AppFrame` layout, with `index` and
  `/overview` both rendering `Overview`.
- `alembic/env.py` imports `app.config.settings` and `app.models.db.Base`. Both paths move.
- `compose.prod.yaml`'s backend healthcheck asserts `json[...]['db'] == 'ok'` against
  `/health`, and its `start_period` is 60 s to cover `alembic upgrade head`.
- The prod frontend build arg `VITE_API_BASE_URL` is deliberately empty so the bundle resolves
  against page origin. Adding a path segment must not change that.

## Acceptance

- `uv run pytest` and `npm test` pass with no test skipped or deleted. A test that only needed
  its URL updated is fine; a test that was removed is not.
- `uv run ruff check .` and `npm run lint` clean.
- `docker compose up` brings up postgres, api, the gex-capture worker and the frontend.
  `GET /health` returns `db: "ok"`; `GET /api/gex/health/capture` answers as `/api/health/capture`
  did.
- The capture worker's scheduler logs its registered job ids at boot, and a manually triggered
  capture writes a snapshot row and a Parquet file.
- Every route reachable before the move is reachable after it under `/gex/*`, and the app
  renders identical data for the same database.

## Likely first-contact failures

- **Alembic cannot find the models.** `env.py`'s `target_metadata` import moves; `alembic.ini`
  deliberately leaves `sqlalchemy.url` unset and `env.py` injects it. Both facts must hold
  after the move or the container's boot-time `alembic upgrade head` fails and the healthcheck
  reports a 60-second-old container as broken.
- **MSW handlers still mock `/api/...`.** `frontend/src/mocks/handlers.ts` will match nothing
  once the client calls `/api/gex/...`, and the failure looks like an empty dashboard rather
  than an error.
- **Two schedulers.** If the lifespan's `build_scheduler()` call is left in place *and* the
  worker container runs, every capture fires twice. Idempotency (T71) may hide this; the log
  line will not.
- **The dev bind mount.** `compose.override.yaml` mounts `./backend` at `/app`. The worker
  service needs the same mount or it will run a stale image in dev while the API hot-reloads.
- Relative imports inside `app/gex/` and `app/scan/` that survive the move by accident and
  then resolve to the wrong package once a second module exists.

## Out of scope

- Any Postgres schema change (T76).
- Any shared `core/` beyond settings, session and schema constants — no bars, no symbols, no
  provider ABC hoisting. That is the successor initiative.
- Launcher design. T75 ships a plain list; T81 makes it a page worth looking at.

## Result

**T75 landed 2026-09-19**, commit `ff05e2e` on `t75-monorepo-skeleton`. 310 files,
+3404/−1994.

### Verified by the supervisor, independently of the agent's report

- `uv run pytest` **990 passed** (baseline 981), `ruff check` clean. `npm test` **357 tests in
  47 files** (baseline 356), `npm run lint` 0 errors and the same 5 pre-existing
  `react-refresh` warnings the frozen reference carries.
- **Nothing deleted**: `git diff --diff-filter=D` over the commit range is empty, and the
  test-file count went 107 → 109 (the two new files below).
- **The `versions/` directory is byte-for-byte untouched**, confirmed by diffing the range.
- Route surface from `app.openapi()`: **26 paths, 25 under `/api/gex/`**, exactly one outside
  it — `/health`, unprefixed, which is the contract `compose.prod.yaml`'s healthcheck depends
  on.
- `main.py` has no `lifespan` parameter at all, and `on_startup`/`on_shutdown` are both empty.
  The API process starts no background work.
- **`alembic upgrade head` against a real Postgres 16**: all seven revisions apply, exit 0,
  eight tables created. This is the gap `tests/test_alembic_upgrade.py` does not cover — that
  test runs on SQLite and stops at `b2d4f6a8c0e1`, because the final revision uses
  `ALTER TABLE … ADD CONSTRAINT`, which SQLite cannot do. Worth re-running by hand on any
  future change to `env.py`.
- **The capture worker booted** against that database and registered eight jobs:
  `['retention_prune', 'bars_update_preopen', 'capture_eod', 'capture_extended', 'bars_update',
  'decisions_update', 'flows_update', 'capture_eod_safety_net']`. Eight rather than ten because
  both intraday flags default false.
- **The cron survived the move**, and proving it did not have to wait for Monday: inspecting
  the built triggers gives `capture_eod` as `cron[day_of_week='mon-fri', hour='16',
  minute='20']` with next fire `2026-09-21 16:20:00-04:00`. Correct schedule, correct zone.
  Being able to check that without a clock is exactly what `build_scheduler()`'s separation
  from `.start()` buys, and that property survived the move.

### The two things that went wrong, both instructive

- **`sys.modules` aliasing was not enough for the frozen revisions.** Two of them do
  `import app.models.db` and then use the dotted form `app.models.db.UTCDateTime(...)`. The
  `sys.modules` entry satisfies the *import* but never creates the `models` **attribute** on
  the `app` package, which is what attribute access walks. The backend container exited 1 with
  `AttributeError: module 'app' has no attribute 'models'` while all 988 tests were green —
  because nothing in the suite ran a migration. Fixed with an explicit `setattr`, guarded now
  by `tests/test_alembic_upgrade.py`.
- **That new test then broke 25 unrelated tests**, in full runs only, each passing alone:
  `env.py`'s `fileConfig` defaults to `disable_existing_loggers=True` and silenced the
  application's loggers for the rest of the pytest session. Fixed with Alembic's own
  `config.attributes.get("configure_logger", True)` idiom; the CLI path is unchanged.

Both are the same lesson in different clothes: a green unit suite does not exercise container
boot. The compose checks are not optional on a task shaped like this one.

### Judgment calls, as made

1. **Scheduler out of the API.** `app/workers/gex_capture.py` exposes
   `run(*, stop, wait_for_schema)` — the old lifespan in substance, both parameters injectable
   so tests drive the real path with no clock and no database. Signals via
   `loop.add_signal_handler`, with a `signal.signal` fallback for Windows where the former
   raises `NotImplementedError`. The one piece of genuinely new logic is `_wait_for_schema()`:
   before the split, `alembic upgrade head && exec uvicorn` guaranteed the schema existed
   before catch-up ran; two containers have no such ordering, and since `startup_catchup_job`
   never raises, the failure mode would be a *silently* skipped catch-up on every cold start.
   It polls for 60 s (matching prod's `start_period`) and starts the scheduler either way —
   refusing to boot because Postgres was slow would turn a recoverable delay into a missed
   16:20. It deliberately does not run alembic; two containers racing `upgrade head` is worse.
2. **`app/core/` extent.** Three files. `get_engine`/`get_sessionmaker` moved out of
   `models/db.py`, because the cached factory cannot live in core without core importing
   `app.modules.gex.models.db` — layering pointing the wrong way on day one. `Base`,
   `UTCDateTime` and the tables stayed; hoisting `UTCDateTime` now would be guessing at
   consumers that do not exist, and T76 will have a real reason.
3. **Frontend boundary.** One edge crosses deliberately: `shell/AppFrame` renders `ContextBar`,
   which reads `useDashboardParams`/`useCaptureHealth`, so `ContextBar` and `AssetSelector`
   live in the module and `AppFrame` imports one thing from it, commented and naming T81.
   `navConfig` stayed in the shell — T81 turns it into the module switcher's data source, and
   moving it twice is worse.

The agent rejected the separate Dockerfile worker stage this file listed as in scope: the
worker needs an identical image and differs only in its command. It did need its own **tag** —
`backend` and `gex-capture` both naming `gex-backend:dev` made buildx race the export and fail
the build outright. They are `gex-capture:dev`/`:prod` now, same target, fully layer-cached.
Good call; this plan file was wrong.

### Carried forward

- **The `/api/gex/gex/…` wart.** `api/gex.py`'s router already carried `prefix="/gex"`, so
  three routes now read `/api/gex/gex/{underlying}/…`. That is the literal consequence of "all
  ten move to `/api/gex`" plus "do not re-specify any GEX behaviour", and renaming it to
  `/levels` was correctly not the agent's call. One line in the router, three in the frontend
  client, plus MSW handlers, whenever someone decides.
- **T76 should delete the aliasing block in `alembic/env.py`** when it rewrites those tables
  into the `gex.` schema.
- `app/core/schemas.py` defines the three schema names and nothing imports them yet. T76.
- One `pyproject.toml` change beyond the move: `[tool.ruff.lint.isort] known-first-party =
  ["app"]`, because ruff resolves first-party on disk and reclassified the frozen revisions'
  `import app.models.db` as third-party once `app.models` stopped existing.
- Old URLs are not redirected to their `/gex` equivalents; CORS still allowlists exactly
  `http://localhost:5173`; the launcher is a plain list. T81 owns the design.

### Not verified

`docker compose -f compose.yaml -f compose.prod.yaml up -d` was not run: it needs the external
`edge` network shared with Caddy and would start a second Postgres against `./data/postgres`.
Config resolution and all three image builds were verified, and the runtime images were
smoke-tested by running them directly. Starting the production topology on the dev host
remains a deliberate, separate act.
