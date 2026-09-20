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

## Result — T79

**Done 2026-09-19.** 1,068 backend tests green offline (6 Postgres-gated skips), 1,074 with a
database, linter clean. 218,915 observations migrated with every vintage intact.

### Verified, not assumed

- **The portability claim held.** The only DDL edit the port needed was `DOUBLE` ->
  `DOUBLE PRECISION`. `TEXT`, `BOOLEAN`, `INTEGER`, `DATE` and `TIMESTAMPTZ` came across
  untouched, and no DuckDB-specific construct appears anywhere in the module. The schema
  header's promise, written well before anyone tried to collect on it, was accurate.
- **No vintage was lost.** 209,328 `(series_id, value_date)` pairs, **3,268 of them revised**,
  every one preserved with its full vintage count. All 218,915 values compared **bit-identical**
  (`==`, never `isclose`).
- **The payrolls example reproduces exactly.** January 2024 nonfarm payrolls: five vintages,
  first print 157700, current 157032 — as documented in the store's own worked example. A
  latest-known read returns 157032; `as_of=2024-02-15` returns 157700; `as_of=2024-03-20`
  returns the 157533 interim revision; `as_of=2024-01-15` returns **zero rows**, because the
  world had not produced that number yet. Absent, not approximated.
- `assert_no_lookahead` passes on a valid frame and raises `LookaheadError` on a backdated
  evaluation time.
- **The board builds from Postgres** at both a historical and a recent `as_of` — 75 series, 38
  with computable z-scores, ranked by |z|, with vol-compression flags and staleness warnings
  firing correctly (`ust.*: newest observation is 4 days before the board as_of`).
- The worker boots, finds `terminal.observations` immediately and schedules `0 3 * * *`.

### Judgment calls

**The engine swap is a facade, not a rewrite.** `store/db.py`'s original docstring promised that
"swapping in Postgres later means adding a sibling implementation here, not touching the loader,
the query layer or the adapters". That promise is collected rather than broken: `_Connection`
wears DuckDB's `execute` signature over psycopg, absorbing three differences — `?` vs `%s`
placeholders, literal `%` escaping, and `fetch_df()` — so **25 SQL call sites kept their strings
character for character**. The alternative was editing every SQL string in the module, which
would have made the port unreviewable and put the point-in-time semantics at risk in the same
commit that moved them.

**One SQL edit was unavoidable and is documented in place.** `query.py`'s
`(? IS NULL OR as_of <= ?)` needed explicit `CAST(? AS timestamptz)`: DuckDB infers a NULL
parameter's type from context, Postgres refuses with "could not determine data type of parameter
$4". Semantics unchanged.

**`tables.py`, not `models/db.py`.** xactx already owns a `models.py` (its Pydantic domain
types), and a `models/` package beside it shadows that module and breaks every adapter import.
The ported file is the one that must not move, so the new file took the different name.

**Settings fold into `app/core/config.py` with the `XA_` names preserved**, behind a small
facade in `modules/terminal/config.py` that maps them back to the short attribute names ~6,600
ported lines already read. Renaming `settings.zscore_window` to `settings.XA_ZSCORE_WINDOW`
throughout would have been a large mechanical diff through exactly the code whose behaviour must
be shown not to have changed. **`db_path` was deleted, not repointed** — there is no file any
more, and a path-shaped setting is an invitation to point something at a stray `.duckdb`.

**`board`, `regime`, `factors` and `brief` are not in the nightly sequence.** They compute on
read, so the API can serve them at whatever `as_of` a screen asks for. Precomputing them would
mean the board could only be viewed at the moments a cron job happened to run — the opposite of
what a point-in-time terminal is for.

### Caught by doing

- **My own SQL translator had a quote bug, found by the first real query.** A `--` comment
  containing an ordinary English possessive ("the parameter's type") was read as opening a
  string literal, so every placeholder after it went untranslated and psycopg reported "the
  query has 3 placeholders but 5 parameters were passed". `translate_sql` is now comment-aware
  (line and block), and `tests/test_terminal_store.py` pins the case. The comment is the kind of
  thing anyone would write, which is what made it worth fixing properly rather than rewording.
- **DuckDB returns `as_of` tagged `America/Santo_Domingo`**, not UTC — the pytz artifact the
  brief predicted, and the reason the migration compares instants rather than wall-clock. A
  naive comparison would have reported thousands of false differences.
- **`pytz` confirmed droppable**: zero uses across the ported module. `duckdb` is now a declared
  **dev-only** dependency — the runtime image builds `--no-dev` and nothing the application
  serves has any business opening a DuckDB file, but the one-shot migration stays reproducible.

### Not done here

The ported xactx tests (`test_board.py`, `test_brief.py`, `test_point_in_time.py` and the rest)
have **not** been brought over wholesale. They are built on a `Store(tmp_path/"test.duckdb")`
fixture, so porting them means giving each a throwaway Postgres schema — worth doing, and it is
the honest gap in this task. What exists instead is `tests/test_terminal_store.py`: full offline
coverage of `translate_sql` (the genuinely new and riskiest code in the port) plus
Postgres-gated tests of the point-in-time invariant, the payrolls revision, the naive-`as_of`
refusal and the lookahead guard. Logged as **T84**.

## Result — T80

**Done 2026-09-19.** 1,074 backend tests and 398 frontend tests green (21 added), both linters
clean (frontend 0 errors), production build clean. Six endpoints and five screens, all `as_of`-
aware.

xactx was specified as a screen and became a CLI during its build. It is a screen now. The CLI
survives as a debugging side-door and is no longer how anyone is expected to read this data.

### The as-of control is the product

It lives in the module frame, not on a page, and it is URL state. Setting it re-renders the
board, the regime strip, the graph, the policy path and the brief together -- a terminal where
the board was historical and the regime strip was live would be worse than either alone. `asOf`
is part of every query key, so a control that stopped being threaded through would change what
the tests assert rather than silently show today.

When a past moment is pinned the frame says so in a coloured bar, permanently, not as a subtle
input state. The expensive mistake this screen can cause is reading a historical board as if it
were live, and that mistake looks exactly like reading a live one.

### What the live data shows

Rendered from the real 218,915-observation store: **38 of 75 series scored**, ranked by |z|, and
the top of the board is a rates-led selloff -- the whole curve 2.3 to 3.2 sigma, most of it
against *compressed* trailing volatility. That last column is why the board exists: a 3-sigma
move in a market that had gone quiet is a different event from a 3-sigma move in a volatile one,
and a plain change table cannot tell you which you are looking at.

The other 37 series are listed with their reason (`no_data`, `insufficient_history`), never as
zeros. At a historical as-of most series legitimately have no value yet, and a grid of zeros
would render an eerily calm market.

### Design decisions

**Nothing is precomputed.** `build_board` and `classify` run per request. That is what lets
`as_of` be any instant rather than one of the moments a nightly job happened to fire, and it is
why `board`, `regime`, `factors` and `brief` are deliberately absent from the ingest worker's
sequence.

**Sign conflicts are pulled to the top of the graph and written out in words.** Spec 4 names
`sign_conflict` and `corr_percentile` the two highest-value outputs, and a correlation table
buries both. `expected_sign == 0` renders as "regime-dependent", never as "unknown" -- it is a
deliberate statement that the sign flips with the regime.

**The z colour scale is banded, not continuous**, and nothing under 1 sigma is coloured at all;
a gradient would make every row look like it was saying something. Direction is hue, magnitude
is intensity, and the number is always printed, so the board survives a screenshot, a projector
and colour blindness.

**The brief's markdown is rendered by a ~120-line parser rather than a dependency.** The document
emits headings, paragraphs, tables, blockquotes and lists; pulling in a markdown stack plus a
sanitiser (it would then be rendering HTML) to cover constructs this document never produces
would be more attack surface than the feature is worth. Inline handling is `**bold**` and
`` `code` `` only, and raw HTML is never rendered.

**MSW fixtures honour `as_of`.** They return fewer scored rows at an early moment and no edge
estimates before the night they were computed -- mirroring the real store. A mock that ignored
the parameter would let a broken as-of control look perfectly fine in development *and* in every
test.

### Caught by doing

- **`/edges` returned a 500 that the same code returned cleanly in a shell.** pandas has no
  nullable integer, so an int column containing one NaN becomes `float64` and `beta_window`
  arrives as `250.0`. Fixed generically by casting from each model's own annotations rather than
  listing the integer columns by hand -- a hand-written list going stale is a 500, not a warning.
- **Then a second 500 from the same endpoint, at serialisation rather than validation.**
  `pandas.Timestamp` subclasses `datetime`, so Pydantic validates it happily and pydantic-core
  then fails on its nanosecond precision with the identical "'float' object cannot be interpreted
  as an integer". `_clean` now converts Timestamps at the boundary.
- **`/brief` writes during a GET.** The ported `section_affects` calls `graph.register()` and
  `graph.estimate_all()` -- sensible when `brief` was a CLI command you ran after ingesting,
  wrong for an HTTP GET that should be cacheable, servable from a replica and readable by
  `quantdesk_ro`. Given a writable connection with the reasoning recorded in place rather than
  papered over. **Logged as T85**, and the fix belongs in `brief.py`: `section_affects` should
  read stored `edge_stats` the way `/edges` already does, which is also far cheaper than
  re-estimating the whole panel on every page load.

### Not done here

T81 still owns the module shell. `TerminalFrame` and `ResearchFrame` are both placeholders that
duplicate a little nav chrome; T81 should absorb them and give all three modules one switcher.
The board has no chart -- a series detail view with its vintage history (`/series/{id}` already
returns it) is the obvious next screen and is not built.
