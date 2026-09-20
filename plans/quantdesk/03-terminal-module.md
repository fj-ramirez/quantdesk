# 03 · Terminal module — xactx — T79, T80

## Goal

Move `projects/market-terminal` (the `xactx` cross-asset context engine) in as
`modules/terminal`, swap DuckDB for the `terminal` Postgres schema, and finally build the
thing it was always meant to be: a terminal. It became a CLI during its build; the CLI stays
as a thin shell over the same functions, but the product is the screen.

## What the user sees

`/terminal` — the board this module was specified for:

- **Change board** (spec 3.1) — every series, normalized change, z-score against its own
  trailing window, coloured by standard deviations. The default screen.
- **Regime** (spec 3.4) — the rolling-PCA regime label, factor loadings, residuals.
- **Transmission graph** (spec 4) — edges with rolling beta, correlation percentile, and the
  sign-conflict flag called out. Its own spec names those the two highest-value outputs.
- **Policy path** (spec 2.1) — the implied path and the FOMC calendar.
- **Brief** (spec 5) — the daily brief rendered as a page rather than stdout.
- **As-of control, global.** Every screen takes `?as_of=`, and setting it re-renders the whole
  board as it looked at that moment. This is the module's entire reason to exist; it is not a
  detail view on one page.

## Data

Six tables, moved to `terminal.*` with the DDL essentially unchanged:

```
terminal.series_metadata    series_id PK
terminal.observations       PK (series_id, value_date, as_of)     <- the point-in-time table
terminal.releases           release_id PK
terminal.ingest_batches     source_batch PK
terminal.edge_definitions   PK (from_series, to_series)
terminal.edge_stats         PK (from_series, to_series, as_of)
```

**The point-in-time rule is this module's invariant and survives the move untouched:** a
revision *adds a row* and never overwrites one. `as_of` is in the primary key, and
`as_of_basis` records per row how that `as_of` was established (`source_vintage`,
`derived_lag` or `archive_floor`) — the per-row column is the authority, not the series'
dominant basis. `assert_no_lookahead` comes across with its tests.

Add this to `CLAUDE.md` as invariant 8 once the module lands.

## Design decisions

**The port is a driver swap, not a query rewrite.** `store/schema.sql` was written portable on
purpose. Its own header says the DDL is kept close to ANSI so the same tables can be created
in Postgres later without a redesign, and that anything DuckDB-specific belongs in `db.py`. A
grep for `ASOF JOIN`, `QUALIFY`, `PIVOT`, `read_parquet`, `arg_max` and `EXCLUDE (...)` across
all 6,628 lines returns nothing. Believe the header, but verify it: the port reads every
`conn.execute` call site rather than assuming.

**Keep raw SQL; do not convert to the SQLAlchemy ORM.** The analytics read frames, not objects
— `board.py`, `factors.py`, `regime.py` and `graph.py` all pull into pandas and work there.
Rewriting 64 call sites into an ORM would be a large diff with no beneficiary. The models
exist for Alembic's sake; the queries stay SQL through a psycopg connection.

**`DOUBLE` becomes `DOUBLE PRECISION`; `TEXT` and `TIMESTAMPTZ` are unchanged.** Those should
be the only DDL edits the port needs. A third edit is a finding worth recording under
*Result*, because the schema's portability claim is load-bearing for this estimate.

**Ingestion becomes a worker with a schedule; the CLI stays.** `xactx ingest | derive |
factors | edges | brief` remain runnable by hand — that is how the module gets debugged — but
the nightly sequence runs in `app/workers/terminal_ingest.py`. The CLI and the worker call the
same functions; neither reimplements the other.

**FRED needs a key.** `XA_FRED_API_KEY` joins the stack's environment, required with the `:?`
form in prod like the Postgres credentials. The Treasury, Cboe, CFTC and CME adapters are
keyless.

**Phase 4 stays blocked.** The `releases` table's consensus columns have no free source. The
table moves across empty, the constraint stays documented, and the UI says so wherever a
consensus would otherwise appear. Moving house does not unblock a vendor.

## Tasks

### T79 · Opus · T76

Port xactx into `app/modules/terminal/` against Postgres, preserving point-in-time semantics
exactly.

- Move `src/xactx/*` to `app/modules/terminal/`, keeping `store/`, `adapters/`, `analytics/`
  and the top-level modules (`brief.py`, `derive.py`, `graph.py`, `policy.py`, `fomc.py`,
  `universe.py`, `models.py`, `cli.py`).
- `store/db.py` swaps DuckDB for psycopg against the shared engine; `schema.sql` becomes an
  Alembic migration under `SCHEMA_TERMINAL` with the type edits above.
- `scripts/migrate_xactx.py` — DuckDB to Postgres, one-shot, idempotent, table by table, with
  per-table row-count verification and an explicit check that no `(series_id, value_date)`
  pair lost a vintage.
- Adapters and their `httpx` usage move unchanged. The `pydantic-settings` config folds into
  `app/core/config.py` with the `XA_` prefix preserved.
- `app/workers/terminal_ingest.py` plus a `terminal-ingest` compose service running the
  nightly sequence, on `internal` only.
- Port all tests, including the vintage/revision tests and `assert_no_lookahead`.
- `pytz` can probably be dropped: it is there only because DuckDB needed it to return
  `TIMESTAMPTZ` through `fetchall`. Confirm before removing.

### T80 · Sonnet · T79

`/api/terminal/*` and the board.

- `GET /api/terminal/board`, `/regime`, `/edges`, `/policy`, `/brief`, `/series`,
  `/series/{id}` — every one accepting `as_of`.
- `frontend/src/modules/terminal/` — the five screens above, sharing the existing chart and
  table components.
- The as-of control lives in the module frame rather than per page, and is URL state, so a
  board view is a shareable link.
- Where a value is missing because a vintage did not exist yet, the cell says so. A blank and
  a genuine zero must never look alike — the same discipline as GEX's invariant 3 about open
  interest.
- MSW fixtures per endpoint.

## Verified facts

Measured 2026-09-19.

- `data/xactx.duckdb` is 28,061,696 bytes; six tables as listed above.
- `schema.sql` carries the portability note quoted in *Design decisions*.
- No DuckDB-specific SQL construct found anywhere in `src/xactx`.
- 6,628 lines total. DuckDB call sites: `store/query.py` 11, `store/loader.py` 6,
  `store/db.py` 5, `brief.py` 15, `graph.py` 8, `regime.py` 5, `cli.py` 4, `derive.py` 4,
  `board.py` 3, `panel.py` 2, `config.py` 1.
- Phases 1, 2, 3a–3c, 5, 6 and 7 are done; phase 4 is blocked on a paid consensus vendor;
  phase 8 is not started.
- Dependencies: `duckdb`, `pytz`, `httpx`, `numpy`, `pandas`, `pydantic-settings`. All but the
  first two are already in the backend.
- The store's own worked example: January 2024 nonfarm payrolls first printed 157700 and ended
  at 157032 across five vintages. That row set is the migration's correctness test.

## Acceptance

- Row counts match per table. No `(series_id, value_date)` pair loses a vintage: the count of
  distinct `as_of` per pair is identical before and after.
- The payrolls example reproduces — a latest-known read returns 157032 and an early-vintage
  read returns 157700, exactly as from DuckDB today.
- `board --as-of 2020-03-16T17:00` produces identical output from Postgres and from the
  DuckDB original.
- `assert_no_lookahead` passes on every frame the analytics assemble.
- `python -m xactx verify` still reports every source live.
- The board page and the CLI `board` command agree for the same `as_of`.

## Likely first-contact failures

- **Upsert syntax.** DuckDB's and Postgres's conflict handling differ. The loader is where
  this bites, and an upsert that silently degrades to a plain insert will violate the primary
  key on the second ingest of a revised series.
- **Timezone round-tripping.** `TIMESTAMPTZ` semantics differ subtly between the engines, and
  this module's whole correctness rests on `as_of` comparisons. Every comparison needs a test
  against a value that is not UTC midnight.
- **`DATE` versus `TIMESTAMP` for `value_date`.** A silent widening turns the primary key into
  something that admits duplicates.
- **Float text round-trip.** Migrating via CSV or `str()` loses low bits and shifts a z-score
  in the fourth decimal — enough to flip a board colour and impossible to spot later. Move
  binary, or via parquet, and assert equality rather than closeness.
- **Postgres is stricter.** A column receiving a Python `None` where the DuckDB path tolerated
  it will now raise `NOT NULL`.
- **Query plans.** `observations` is the large table and point-in-time reads filter on
  `as_of <= ?`. The `(series_id, as_of)` index carries over, but verify with `EXPLAIN` rather
  than assuming DuckDB's columnar performance transfers.

## Out of scope

- Phase 4 (consensus) and phase 8.
- New series, new adapters, new analytics. Port only.
- Streaming or intraday cross-asset data. This module is daily by design.
