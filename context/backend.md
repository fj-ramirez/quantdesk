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

All routers are included in `main.py` with `prefix="/api"`, each carrying its own sub-prefix.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | app-level, not under `/api`: status, provider, symbols |
| GET | `/api/health/capture` | per-symbol capture freshness; stale = ≥2 trading days behind |
| POST | `/api/snapshots/capture?underlying=&eod=` | 201; 422 unknown symbol, 502 capture failed |
| GET | `/api/snapshots?underlying=&limit=` | index rows, newest first, limit 1–500 (default 30) |
| GET | `/api/gex/{underlying}/latest?filter=` | precomputed levels + per-strike series |
| GET | `/api/gex/{underlying}/snapshots/{snapshot_id}?filter=` | same, for a specific snapshot |
| GET | `/api/gex/{underlying}/levels/history?filter=&start=&end=&eod_only=` | level time series |
| GET | `/api/chains/{underlying}/latest?expiry=` | raw contracts for one expiry |

Response models live in `api/schemas.py`. A symbol that has never been captured returns a
**clean 404 with a specific `detail`** — that is the contract the frontend's empty state
depends on (T37); do not turn it into a 500 or a bare body.

## Database

Three tables, all defined in `models/db.py`:

- **`snapshots`** — index of Parquet files. Thin on purpose: anything queryable without
  opening the file (underlying, `captured_at`, source, spot, contract count, `is_eod`,
  `parquet_path`). Composite index on `(underlying, captured_at)` — the dominant query shape.
- **`gex_levels`** — one row per `(snapshot_id, filter)`. **Every numeric level column is
  nullable on purpose**: `key_levels` legitimately returns `None` when a filter admits no
  contracts (the everyday case is `ZERO_DTE` on a 16:20 EOD capture) or when the ±10 % profile
  never changes sign. Unique on `(snapshot_id, filter)`.
- **`gex_by_strike`** — per-strike series; the volume driver. Never null here: a strike only
  gets a row if contracts contributed to it. Unique on `(snapshot_id, filter, strike)`.

`compute_and_store` deletes the existing `(snapshot_id, filter)` slice before reinserting, so
retries and backfills **replace rather than accumulate**. The unique constraints are the
backstop against racing writers, not the primary mechanism.

**`UTCDateTime`** is a `TypeDecorator` that makes tz-aware UTC round-trip identically on
SQLite and Postgres. It rejects naive datetimes on write and always returns aware UTC on read.
This exists because SQLite silently drops `tzinfo`, which would kill the codebase-wide
`captured_at is aware UTC` invariant only in production. Use it for every timestamp column.

`get_engine()` / `get_sessionmaker()` are never called at import time — Alembic's `env.py`
and tests each need a different URL. Postgres URLs get `connect_timeout=5` (T35: an
unreachable Postgres must not hang startup); SQLite gets no connect args.

Migrations: Alembic in `backend/alembic/`, two revisions so far (snapshots; gex_levels +
gex_by_strike). Add a revision for any model change — models and migrations must both stay
backend-portable (plain core types, no Postgres-only types).

## Jobs

- `jobs/capture.capture_snapshot(underlying, *, is_eod, provider=, session_factory=, data_dir=)`
  — fetch, write Parquet, index, compute levels. **Never raises**; returns a `CaptureResult`
  with `ok=False` for provider *or* storage failure, having already emitted the structured log
  line (symbol, contract count, spot, duration, error).
- `jobs/capture.capture_all_symbols` — shares one provider connection across all three symbols.
- `jobs/scheduler.build_scheduler()` — registers two cron jobs on `AsyncIOScheduler`, both
  Mon–Fri in `America/New_York`: **16:20 EOD capture** and a **20:00 safety net**. Both use
  `misfire_grace_time=None` (a capture hours late still beats one that never runs — the free
  Cboe source has no history), `coalesce=True`, `max_instances=1`.
- `jobs/catchup.startup_catchup_job()` — fired from the lifespan via `asyncio.create_task`, not
  awaited, so boot is not blocked behind up to three sequential Cboe fetches. Never raises.
- `jobs/calendar` — `is_trading_day`, `is_market_holiday`, `is_regular_session`,
  `effective_data_time(captured_at, delayed_minutes)`.

`logging.basicConfig` is called at import in `main.py` because uvicorn configures only its own
`uvicorn.*` loggers; without it every structured capture log would go nowhere.

## Backfill CLI

`uv run python -m app.gex.backfill` recomputes levels for snapshots that lack them, printing
`total/processed/failed/skipped_up_to_date` and exiting non-zero on any failure.

## Testing

`backend/tests/` mirrors the module layout, one `test_<module>.py` per module. Everything runs
**fully offline**: SQLite session factories and `tmp_path` data dirs are injected through the
same defaulted parameters production uses, and provider tests replay recorded JSON from
`tests/fixtures/cboe/` and `tests/fixtures/marketdata/`. Do not add a test that reaches the
network. When you change a provider's parsing, update its fixture rather than mocking `httpx`
ad hoc.
