# The expiry dimension, and the session a chain belongs to

`F5` and `F8` from the 2026-09-21 review. Two tasks, one file, because they want the same
write path and the review notices it: the session-date column is "the same column F5's expiry
work will want anyway."

## Goal

Answer "how much of this wall expires Friday?" — the first thing anyone asks about a wall, and
currently unanswerable — and make "which session does this chain belong to" a column rather
than a convention.

## What the user sees

On 09-21 the QQQ 740 wall was +662mn and the desk could not say what fraction of it survived
past that Friday. With a horizon question — "what happens this week" — the answer today is a
single undifferentiated number.

For `F8`: snapshot 178 is `2026-09-20 15:10:08Z`, a **Sunday**, with `is_eod = true` and 10,436
contracts, holding Friday's post-opex book. A consumer filtering `WHERE is_eod` and grouping by
`captured_at::date` gets a phantom Sunday session. That is exactly what the first pass at the
freshness query in the source review did.

## Data

### `F5` — the expiry rollup

`gex.gex_by_strike` is `(id, snapshot_id, filter, strike, call_gex, put_gex, net_gex)`. The
only expiry slicing anywhere in Postgres is the `ZERO_DTE` / `EX_ZERO_DTE` filter.

Per-contract rows live only in Parquet by design (**invariant 5**), so this is a rollup, not a
schema violation. Two shapes are possible and the choice is yours to argue:

- **`gex.gex_by_expiry`**, a sibling table keyed `(snapshot_id, filter, expiry)` — cheap, and
  answers the horizon question directly.
- **an `expiry` column on the strike rollup**, keyed `(snapshot_id, filter, strike, expiry)` —
  strictly more informative (it answers "which expiry owns the 740 wall") and strictly more
  rows. `GexByStrike`'s own docstring already calls itself "the volume driver": a full SPX
  chain is 100+ strikes × 3 filters × every 15-minute capture. Multiplying that by the expiry
  count is the thing to size before choosing.

**Size it against a real SPX capture before deciding**, and put the row-count arithmetic in
the design note. The capture job already holds the per-contract frame, so either is cheap at
write time and expensive later.

### `F8` — the session date

`gex.snapshots` today: `id, underlying, captured_at, source, spot, contract_count,
parquet_path, is_eod, content_hash`. There is no session column. Add one — the trading session
the chain's contents belong to, distinct from the wall-clock instant of capture.

## Design decisions

### 1. Rollup at capture time, not on demand

The review offers an API route resolving Parquet on demand as an alternative. Reject it as the
primary mechanism: the capture job already has the frame in hand, and a read path that reopens
Parquet per request re-does work the write path was already doing. On-demand resolution stays
available via `storage.parquet.resolve_snapshot_path` for anything the rollup does not cover.

### 2. The session date is derived once, at capture, and stored

Not computed by every consumer. The rule — a weekend or holiday capture carries the previous
session — already exists in prose in the `market-research` skill and in `jobs/calendar.py`.
Deriving it once at write time makes it enforceable in SQL instead of documented in English.

### 3. `is_eod` keeps its current meaning

Do not redefine it. It is defensible as "an end-of-session book" and other things read it.
`session_date` is additive and is what a consumer should group by; say so in both docstrings.

### 4. Backfill both columns — decided

**Decided by the user, 2026-09-21: backfill.** Both are derivable from data already on disk —
`session_date` from `captured_at` plus the calendar, the expiry rollup from each snapshot's
Parquet file — so history becomes queryable rather than starting today.

Two limits to state in the `Result` section rather than paper over:

- **The 09-14 → 09-18 gap has no Parquet**, so it stays empty. Backfilling around a hole must
  not make the hole invisible.
- A backfill over every stored snapshot re-reads every Parquet file on disk. Measure how long
  it takes before running it against the homeserver, and do not run it during a capture
  window — invariant: nothing may risk the 16:20 capture.

## Tasks

### T101 · Opus · T100

The expiry dimension, written at capture time. A migration adds the chosen shape (decision:
sibling table versus a column on the strike rollup, sized against a real SPX capture);
`gex/store.compute_and_store` populates it from the frame it already holds; a read route
exposes "how much of this level expires when". `engine.py` gains the pure aggregation and no
I/O.

Paths: `backend/app/modules/gex/models/db.py`, `backend/app/modules/gex/gex/engine.py`,
`backend/app/modules/gex/gex/store.py`, `backend/app/modules/gex/api/gex.py`,
`backend/alembic/versions/`, `backend/tests/`.

### T102 · Sonnet · T101

`gex.snapshots` gains `session_date`: the trading session the chain's contents belong to,
derived at capture from `captured_at` and `jobs/calendar.py`, stored not computed. `is_eod`
is unchanged. Both docstrings state which column a consumer should group by and why.

Paths: `backend/app/modules/gex/models/db.py`, `backend/app/modules/gex/jobs/capture.py`,
`backend/app/modules/gex/gex/store.py`, `backend/alembic/versions/`, `backend/tests/`.

## Verified facts

- `GexByStrike` has no expiry column; `Snapshot` has no session column. Both confirmed
  against the live schema on 2026-09-21.
- Snapshot 178 is the Sunday `is_eod = true` row, 10,436 contracts, and is the `F8` fixture.
- Invariant 5 stands: per-contract rows stay in Parquet. `snapshots.parquet_path` is relative
  to `DATA_DIR` and must be resolved through `storage.parquet.resolve_snapshot_path`.
- `GexByStrike`'s docstring already identifies itself as the volume driver. Read it before
  choosing the shape.
- `jobs/calendar.py` already knows trading days — `catchup_skipped … "not a trading day"` is
  its existing correct behaviour.

## Acceptance

1. For QQQ on 09-21, the desk can state what fraction of the +662mn at 740 expired that Friday
   versus later. Run it; quote the number in the `Result` section.
2. Snapshot 178 carries the Friday session date, not the Sunday one, and a `GROUP BY
   session_date` over `WHERE is_eod` produces no phantom weekend session.
3. Row-count impact of the chosen `F5` shape is measured on a real SPX capture and recorded,
   not estimated.
4. A capture round-trip writes both new columns with no change to any existing number.
5. **Both columns backfilled over history**, with the 09-14 → 09-18 gap still visibly empty and
   the wall-clock cost of the backfill recorded.
6. `uv run pytest` and `uv run ruff check .` clean; the migration applies and rolls back.

## Likely first-contact failures

- **Writing per-contract rows into Postgres.** Invariant 5. The rollup is the whole point.
- **Choosing the strike×expiry shape without sizing it**, then discovering the table is the
  largest in the database after a week of 15-minute captures.
- **Deriving `session_date` in each consumer** instead of once at capture — the same drift
  `F1` is made of.
- **Redefining `is_eod`** and breaking its existing readers.
- Reading Parquet paths directly instead of via `resolve_snapshot_path`.
- Forgetting that the 09-14 → 09-18 gap has no Parquet to backfill from, and emitting rows that
  make it look populated.
- Running the backfill during a capture window.

## Out of scope

- An expiry-aware *decision* engine. This lands the data; using it in `scan/decisions.py` is
  later work.
- Frontend surfacing of either column.
- Distinguishing "no contracts in this bucket" from "contracts existed but none were usable" —
  inherited from `T100` decision 3. If the expiry rollup makes it natural to record *why* a
  bucket is empty, take it; if not, leave the note.


---

## Result — T101, 2026-09-21

**Done. 1,219 backend tests green (7 added), 408 frontend, both linters clean, `tsc` clean,
migration generates and reverses.** Three items need a write seat on the homeserver; see
*Outstanding*.

### The shape decision, settled by measurement

This file offered two shapes and said to size them first. The measurement killed one of them
outright and produced a third.

| shape | rows on 2026-09-21 | vs. `gex_by_strike` that day |
|---|---|---|
| strike x expiry, all filters | **~2,614,587** | **18.6x** |
| per-expiry only | ~5,184 | 0.04x |
| per-strike horizon **columns** | **0 new rows** | -- |

`gex_by_strike` holds **266,050 rows for the project's entire history**. The cross product
would have written nearly ten times that in a single session and become the largest object in
the database inside a week. It is not viable at 15-minute capture cadence and the file should
not have listed it as a live option without this number attached.

But per-expiry alone does not answer `F5`. The review's actual question -- "how much of the QQQ
740 wall survives Friday" -- is per-strike *and* per-horizon, and summing across strikes
discards exactly the half that matters. So:

- **Four `net_gex_*` horizon columns on `gex_by_strike`**, which add no rows at all and answer
  the wall question for *every* strike, not just the walls;
- **`gex_by_expiry`**, ~5.2k rows a day, for the exact-expiry term structure that horizons
  necessarily blur.

Horizons reuse the frame's existing `zero_dte` / `this_week` / `dte` flags rather than
inventing boundaries, so "this week" means what `ExpiryFilter.THIS_WEEK` already means
everywhere else.

### The load-bearing property is the partition

The four horizons are exhaustive and disjoint, and sum back to the strike's own `net_gex`
exactly. Both halves are tested: summing alone would not catch a contract counted twice, so
there is a separate mask-level check that every contract lands in exactly one bucket. If that
partition ever drifts, every "expires Friday" answer is wrong by the gap, silently.

Verified on the SPX fixture -- and the decomposition immediately earns itself. Strike 5000
carries **-634mn** net, which is **-660mn of 0DTE** plus **+25.9mn** surviving into the next 30
days. The sign of what is left flips. That is invisible in the stored data as it existed
yesterday.

### `--recompute`, and why the existing backfill could not do this

`gex/backfill.py` selects snapshots "lacking levels", which answers *was this ever processed*,
not *was it processed by the current code*. After an engine change every stored snapshot looks
up to date and the pass writes nothing -- precisely wrong when propagating the change is the
point. Two changes now need exactly that: `T99`'s wall sign/magnitude test, and this task's
expiry dimension, which no existing row has at all.

`--recompute` processes every snapshot instead. Safe rather than merely permitted, because
`compute_and_store` deletes the `(snapshot_id, filter)` slice before reinserting -- pinned by a
test that a second pass leaves the row count unmoved. It is not the default: it reopens every
Parquet file on disk, so it runs outside capture hours. It rewrites derived rows only and never
touches `gex.decisions`.

This is the mechanism `T99`'s outstanding item was waiting for, so one run now settles both.

### Read path

`GET /api/gex/gex/{underlying}/expiry/history` reads `gex_by_expiry` JOIN `snapshots` and
**never opens Parquet**, so a term structure from months ago costs what today's costs. That is
the entire argument for rolling up at capture time: `engine.by_expiry` has computed this since
T08 and thrown it away at persist, so until now no question about a *past* term structure could
be answered at any price short of reopening the chain.

`StrikeGexOut` and the frontend `StrikeGex` carry the horizon fields, both nullable -- rows
written before this migration have no split and cannot get one without reopening Parquet. Null
means "not computed for this row", never zero.

### Outstanding

1. **The migration has not been applied.** It generates and reverses correctly offline; it has
   not run against the homeserver.
2. **The backfill has not run.** `--recompute` exists and is tested, but reprocessing every
   stored snapshot needs a write seat, the Parquet directory, and a window outside capture
   hours.
3. **Acceptance 1 is demonstrated, not measured on the real row.** The capability is proven
   against the SPX fixture and the partition is tested; quoting the actual QQQ 740 split for
   2026-09-21 requires that snapshot's Parquet file, which is on the homeserver. Once the
   backfill runs, the number is one query away -- and it belongs in this section.

Also noted while working, not fixed here: `alembic heads` fails standalone, because two frozen
pre-T75 revisions do `import app.models.db` and the `sys.modules` alias that rescues them lives
in `alembic/env.py`, which `heads` does not load. `upgrade` and `downgrade` both work, so this
is a papercut in an introspection command rather than a broken migration path -- but the next
person to run `alembic heads` will think the migrations are broken, as I did.
