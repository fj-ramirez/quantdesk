# Backend conventions

Python 3.12 (pinned `>=3.12,<3.13`), managed by `uv`. FastAPI + SQLAlchemy 2.0 + Alembic +
APScheduler + NumPy/pandas/pyarrow/scipy. Ruff, line length **100**, target `py312`.
pytest with `asyncio_mode = "auto"` — async tests need no decorator.

## House style

The codebase is heavily commented, and deliberately so: comments explain *why a decision was
made*, usually naming the task (`T09`, `T34`) or the failure mode being prevented. Match that
density when editing; a one-line "what" comment on top of self-evident code is not the local
idiom, a paragraph explaining a non-obvious trade-off is.

Other habits worth matching: `from __future__ import annotations` where used, explicit
`__all__` in library modules, frozen `slots=True` dataclasses for result records, keyword-only
arguments for anything optional, and dependency injection via defaulted parameters
(`session_factory=None`, `data_dir=None`) so tests can run the real code path offline.

## API

**T75.** The ten routers are composed by `app/modules/gex/router.py` into one
`APIRouter(prefix="/gex")`, which `main.py` mounts with `prefix="/api"`. Each router keeps its
own sub-prefix on top of that, so every path gained a `/gex` segment. One wart follows from
doing that mechanically: `api/gex.py`'s router was already `prefix="/gex"` (it is the
levels/profile API, named long before there was a *module* called gex), so its routes read
`/api/gex/gex/{underlying}/...`. That is recorded rather than fixed -- renaming it would be
re-specifying a GEX behaviour, which T75 was forbidden to do.

`GET /health` stays at the root, unprefixed, because `compose.prod.yaml`'s healthcheck is a
deployment contract. Per-module health is `/api/<module>/health`.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | app-level, not under `/api` at all: status, provider, symbols, `db` |
| GET | `/api/gex/health/capture` | per-symbol capture freshness; stale = ≥2 trading days behind |
| POST | `/api/gex/snapshots/capture?underlying=&eod=` | 201; 422 unknown symbol, 502 capture failed |
| GET | `/api/gex/snapshots?underlying=&limit=` | index rows, newest first, limit 1–500 (default 30) |
| GET | `/api/gex/gex/{underlying}/latest?filter=` | precomputed levels + per-strike series |
| GET | `/api/gex/gex/{underlying}/snapshots/{snapshot_id}?filter=` | same, for a specific snapshot |
| GET | `/api/gex/gex/{underlying}/levels/history?filter=&start=&end=&eod_only=` | level time series |
| GET | `/api/gex/chains/{underlying}/latest?expiry=` | raw contracts for one expiry |
| GET | `/api/gex/decisions?filter=&min_score=` | T60 decision engine: ranked opportunities (entry/stop/target, thesis, invalidation) across the optioned universe, plus per-symbol rows and no-trade reasons |
| GET | `/api/gex/decisions/{underlying}?filter=` | one symbol's opportunities; same 404 contract as `/api/gex/gex` |
| GET | `/api/gex/decisions/history?underlying=&outcome=&limit=` | T61 track record: stored opportunities newest first plus hit/win rates and R by setup and grade |
| POST | `/api/gex/decisions/record?filter=` | 201; runs the 17:45 ET record-and-score job now |

Response models live in `api/schemas.py`. A symbol that has never been captured returns a
**clean 404 with a specific `detail`** — that is the contract the frontend's empty state
depends on (T37); do not turn it into a 500 or a bare body.

## Database

### Schemas, one per module (T76)

One database, three namespaces — `gex`, `research`, `terminal` — and `public` holds nothing but
Alembic's `alembic_version`. Full reasoning in
[plans/quantdesk/01-postgres-schemas.md](../plans/quantdesk/01-postgres-schemas.md); the short
version is that schemas give per-module grants (`GRANT USAGE ON SCHEMA research`), `pg_dump -n
terminal`, and an answer to "who owns this table" that `psql \dn` prints, where a
`module__table` prefix gives a convention nothing enforces.

**Placement is declarative and cannot be forgotten.** `Base` in
`app/modules/gex/models/db.py` carries `metadata = MetaData(schema=SCHEMA_GEX)`, so
`class Foo(Base)` is `gex.foo` with no further thought — deliberately not a per-model
`__table_args__ = {"schema": ...}`, which is exactly the thing a new table forgets. The names
come from `app/core/schemas.py`; research (T77) and terminal (T79) each get their own `Base`
the same way. String foreign keys stay unqualified (`ForeignKey("snapshots.id")`) — SQLAlchemy
resolves them within the metadata's default schema.

**SQLite tests still work, and that is not an accident.** ~30 test files build their own SQLite
engine and call `Base.metadata.create_all`; SQLite has no schemas, so `CREATE TABLE
gex.snapshots` is a hard error. `get_engine` applies `schema_translate_map` mapping every
schema in `SCHEMAS` to `None` for non-Postgres URLs — SQLAlchemy's designed mechanism, covering
DDL and queries alike. That is why T76 changed no test file. `tests/test_module_schemas.py`
guards it, because nothing else in the suite would notice it silently stopping.

**`search_path` is pinned to `public`, on both the app engine and Alembic's.** This project's
database role is called `gex` and there is now a *schema* called `gex`, so the default
`"$user", public` made `gex` the default schema — and `alembic revision --autogenerate` then
compared metadata that says `gex.snapshots` against reflection that said `snapshots`, and
emitted a migration recreating all seven tables. Observed, not theorised. It must be a
*connect* argument (`options=-csearch_path=public`, see `connect_args_for`): SQLAlchemy
resolves `default_schema_name` once at first connect and caches it, so a later `SET
search_path` changes query resolution and leaves autogenerate still wrong. `env.py` also sets
`include_schemas=True` plus an `include_name` hook confining reflection to the three schemas —
without the hook, a schema this app does not own would be diffed against metadata that has
never heard of it and proposed for dropping.

**`alembic_version` stays in `public`, deliberately.** Pointing `version_table_schema` at `gex`
would mean that on any database whose version table is still in `public`, Alembic finds no
version table, concludes the database is at base, and re-runs the whole chain against tables
that already exist. There is no ordering of "move the table" and "change the setting" safe in
both directions on both a fresh and an existing database. So `public` holds one table, owned by
the migration tool rather than by any module.

### The read-only role (T76)

`quantdesk_ro` is what the T82 MCP connector authenticates as: `USAGE` on all three schemas,
`SELECT` on their tables, and an `ALTER DEFAULT PRIVILEGES` so **tables added later are
readable with no new grant** — which is what stops T77 and T79 from having to remember. Default
privileges apply per *granting* role, so the migration reads `current_user` rather than
assuming `postgres`; set for the wrong role they silently govern nothing and the failure
surfaces months later.

Privileges and secret are split on purpose. The migration (`a3f1c7d92b64`) creates the role
`NOLOGIN` and grants — versioned schema state, identical on every host, no secret in git.
`app/core/ro_role.py` applies `LOGIN` and `QUANTDESK_RO_PASSWORD` immediately after `alembic
upgrade head` in both Dockerfile stages, so **rotating the password is an env edit and a
restart**, not a new migration. It is a no-op when the variable is unset and exits 0 even when
it fails: nothing else in the stack needs the role, so it must never gate uvicorn.

### Tables

Tables, all defined in `models/db.py` (the original three plus `daily_bars`, `etf_shares_outstanding`, `decisions`):

- **`snapshots`** — index of Parquet files. Thin on purpose: anything queryable without
  opening the file (underlying, `captured_at`, source, spot, contract count, `is_eod`,
  `parquet_path`). Composite index on `(underlying, captured_at)` — the dominant query shape.
- **`gex_levels`** — one row per `(snapshot_id, filter)`. **Every numeric level column is
  nullable on purpose**: `key_levels` legitimately returns `None` when a filter admits no
  contracts (the everyday case is `ZERO_DTE` on a 16:20 EOD capture) or when the ±10 % profile
  never changes sign. Unique on `(snapshot_id, filter)`.
- **`decisions`** (T61) — one row per emitted opportunity per `(snapshot_id, filter, key)`:
  the levels as suggested, the full opportunity as JSON `payload`, and the outcome columns
  `app.modules.gex.scan.outcomes.evaluate` fills in from later bars (`outcome`, `fill`, `result_r` in R,
  `mfe_r`/`mae_r`, `mark_r` while pending). Insert-when-unseen, never upsert: a recorded level
  is a commitment the track record scores.
- **`gex_by_strike`** — per-strike series; the volume driver. Never null here: a strike only
  gets a row if contracts contributed to it. Unique on `(snapshot_id, filter, strike)`.

`compute_and_store` deletes the existing `(snapshot_id, filter)` slice before reinserting, so
retries and backfills **replace rather than accumulate**. The unique constraints are the
backstop against racing writers, not the primary mechanism.

**`UTCDateTime`** is a `TypeDecorator` that makes tz-aware UTC round-trip identically on
SQLite and Postgres. It rejects naive datetimes on write and always returns aware UTC on read.
This exists because SQLite silently drops `tzinfo`, which would kill the codebase-wide
`captured_at is aware UTC` invariant only in production. Use it for every timestamp column.

`get_engine()` / `get_sessionmaker()` live in **`app/core/db.py`** since T75 (with the cached
`get_session_factory()`, lifted out of `jobs/capture.py`) — they are the only database code
with no opinion about what is in the database, which is what lets `core` import nothing from
`app.modules.*`. `Base`, `UTCDateTime` and the tables stayed in
`app/modules/gex/models/db.py`. They are never called at import time — Alembic's `env.py`
and tests each need a different URL. Postgres URLs get `connect_timeout=5` (T35: an
unreachable Postgres must not hang startup) and `options=-csearch_path=public` (T76, above);
SQLite gets no connect args and a `schema_translate_map` instead. Both are built by
`connect_args_for`.

Migrations: Alembic in `backend/alembic/`. Add a revision for any model change — models and
migrations must both stay backend-portable (plain core types, no Postgres-only types).

Two revisions still `import app.models.db`, the pre-T75 path. `alembic/env.py` aliases that
name in `sys.modules` **and** binds it as an attribute on the `app` package, rather than
rewriting the revision files: a migration is a historical record of what was applied, and
refactoring one rewrites history to match code that did not exist when it ran. The attribute
half is not optional — `import app.models.db` is satisfied by `sys.modules` alone, but the
revisions use the dotted form `app.models.db.UTCDateTime(...)`, which walks attributes.
`tests/test_alembic_upgrade.py` holds both halves; the unit suite never ran a migration before
it, which is why the first version of that alias passed 988 tests and still broke a container
boot.

## Jobs

- `jobs/capture.capture_snapshot(underlying, *, is_eod, provider=, session_factory=, data_dir=)`
  — fetch, write Parquet, index, compute levels. **Never raises**; returns a `CaptureResult`
  with `ok=False` for provider *or* storage failure, having already emitted the structured log
  line (symbol, contract count, spot, duration, error).
- `jobs/capture.capture_all_symbols` — shares one provider connection across all three symbols.
- `jobs/scheduler.build_scheduler()` — registers cron jobs on `AsyncIOScheduler`. **Since T75
  nothing in the API process calls it**; `app/workers/gex_capture.py` does, in its own
  container. `build_scheduler()` stays separate from starting it so tests can inspect
  registered job ids and triggers without a clock. Two of them are
  Mon–Fri in `America/New_York`: **16:20 EOD capture** and a **20:00 safety net**. Both use
  `misfire_grace_time=None` (a capture hours late still beats one that never runs — the free
  Cboe source has no history), `coalesce=True`, `max_instances=1`.
- `jobs/catchup.startup_catchup_job()` — fired by the worker via `asyncio.create_task`, not
  awaited, so boot is not blocked behind up to three sequential Cboe fetches. Never raises.
  (Before T75 this came from `main.py`'s lifespan.)
- `jobs/calendar` — `is_trading_day`, `is_market_holiday`, `is_regular_session`,
  `effective_data_time(captured_at, delayed_minutes)`.

`logging.basicConfig` is called at import in `main.py`, and again in
`app/workers/gex_capture.py`, because uvicorn configures only its own `uvicorn.*` loggers (and
the worker has no uvicorn at all); without it every structured capture log would go nowhere.

### The worker (T75)

`app/workers/gex_capture.py` is the container entrypoint (`python -m app.workers.gex_capture`)
for everything clock-bound: it builds and starts the scheduler, logs its registered job ids at
INFO, fires the startup catch-up as a background task, and waits on SIGTERM/SIGINT. It also
waits (bounded, 60 s) for the `snapshots` table before starting, because the API container is
what runs `alembic upgrade head` and two containers racing that is worse than the ordering
problem it would solve.

**The API process must never start background work again.** `tests/test_main.py` asserts it:
with the worker container also running, a scheduler in the lifespan means every capture fires
twice, and T71's write idempotency would hide most of the evidence.

## Backfill CLI

`uv run python -m app.modules.gex.gex.backfill` recomputes levels for snapshots that lack them, printing
`total/processed/failed/skipped_up_to_date` and exiting non-zero on any failure.

## Testing

`backend/tests/` mirrors the module layout, one `test_<module>.py` per module. Everything runs
**fully offline**: SQLite session factories and `tmp_path` data dirs are injected through the
same defaulted parameters production uses, and provider tests replay recorded JSON from
`tests/fixtures/cboe/` and `tests/fixtures/marketdata/`. Do not add a test that reaches the
network. When you change a provider's parsing, update its fixture rather than mocking `httpx`
ad hoc.
