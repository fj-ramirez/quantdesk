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
