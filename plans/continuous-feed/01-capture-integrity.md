# Capture integrity (T32, T71)

Both tasks here exist because 27 captures a day turns two currently-harmless properties of the
capture path into daily problems. Neither is optional before `T18`, and `TASKS.md`'s own T32
block already says so: *"implement it before T18 ships, not after the table is large."*

## Goal

The capture path can run 27 times a session, indefinitely, without the database growing
without bound and without a re-fire, a catch-up overlap or a frozen vendor payload creating a
duplicate snapshot.

## What the user sees

Nothing directly. Indirectly: the `/intraday` timeline (`T20`) shows real moves rather than a
sawtooth of duplicated readings, and the History page's snapshot list stops accumulating rows
that represent the same instant twice.

---

## T32 — retention for `gex_by_strike`

### The volume

Measured on disk 2026-09-11, one EOD capture round of all five symbols:

| Symbol | Parquet bytes |
|---|---|
| SPX | 1,227,460 |
| SPY | 570,334 |
| QQQ | 512,382 |
| GLD | 340,095 |
| DIA | 212,751 |
| **round total** | **~2.86 MB** |

At 27 captures a session that is **~77 MB/day, ~386 MB/week, ~19 GB/year** of Parquet — which
is fine on any host, but is a number `T70` needs when sizing a disk.

The database is the problem, not the disk. `gex_by_strike` runs ~800 rows per filter per
capture; with five symbols and two non-empty filters that is ~8,000 rows per capture round,
so **~216,000 rows/day, ~1.1 M/week, ~54 M/year** against ~8,000/day today. Nothing partitions
or prunes it.

### Design decisions

**`gex_by_strike` is a cache, not a record.** This is what makes a retention policy safe to
adopt rather than a data-loss decision. Per invariant 5, per-contract data lives in Parquet
and Postgres holds computed results plus a snapshot index. Every row in `gex_by_strike` is
recomputable from the Parquet file that `snapshots.parquet_path` points at, by the same
`compute_and_store` that wrote it (it already does delete-then-insert, so a rebuild is
idempotent) or by the `gex/backfill.py` CLI. Pruning is therefore reversible, and the
migration's docstring should say so, so a future reader does not treat pruned days as lost.

**Recommended policy, to be confirmed by the implementing agent against the real row counts:**

- `gex_levels` — **never pruned.** One row per (snapshot, filter); even at 27 captures a day
  that is a few hundred rows/day. This is the table the history views and `T20`'s timeline
  read, and it is what makes a pruned day still legible.
- `snapshots` — **never pruned.** It is the index to the Parquet archive.
- Parquet files — **never pruned.** Unbackfillable, and the archive of record.
- `gex_by_strike` for `is_eod = true` snapshots — **kept forever.**
- `gex_by_strike` for `is_eod = false` snapshots — **kept N days, then deleted**, default
  `N = 30` via a config key (`INTRADAY_STRIKE_RETENTION_DAYS`). Thirty days covers "scrub
  through last month's sessions" while capping the table at roughly 6 M rows.

**Judgment call for the agent, named as such:** whether the prune runs as its own scheduled
job or as a tail step of the existing daily flow. Prefer a separate nightly job with its own
ID in `scheduler.py` — it makes the deletion visible in the job list and independently
runnable, and it keeps a slow `DELETE` off the capture path's latency. State the reasoning
either way.

### T32 · Opus · T09

**Retention policy for `gex_by_strike`**

- Add `INTRADAY_STRIKE_RETENTION_DAYS` to `app/config.py` (default 30; `0` disables pruning).
- Add a prune routine and register it on the scheduler, after the EOD jobs have run.
- Alembic migration if any index is needed to make the delete's predicate cheap — the current
  indexes are `uq(snapshot_id, filter, strike)` and `ix_gex_by_strike_snapshot_filter`, both
  keyed on `snapshot_id`, so a prune that selects by *date* will otherwise scan. Joining to
  `snapshots.captured_at` is the natural predicate; measure before adding anything.
- Document the reversibility (rebuild from Parquet via `compute_and_store`/`backfill`) in the
  job's docstring, not just in this plan.

Acceptance: with synthetic rows spanning 60 days of mixed `is_eod`, one run deletes exactly
the non-EOD strike rows older than the cutoff, leaves every EOD strike row, every `gex_levels`
row, every `snapshots` row and every Parquet file untouched, and re-running is a no-op.

---

## T71 — capture idempotency and content dedupe

### The two defects

**1. `snapshots` has no uniqueness at all.** `app/models/db.py:125` declares only a
non-unique composite `Index("ix_snapshots_underlying_captured_at", ...)`. Compare
`gex_levels`, which has `uq_gex_levels_snapshot_filter` precisely so `compute_and_store`'s
replace is idempotent. Nothing stops two rows for the same `(underlying, captured_at)`, each
with its own Parquet file. There is already physical evidence of this shape in the tree: the
state review's 12 orphaned files under `backend/data/`, and several same-day SPX captures with
byte-identical sizes.

At one capture a day the overlap window is tiny. At 27 a day, plus a startup catch-up, plus
`T70`'s migration period where two stacks may briefly both be running, it becomes routine.

**2. A frozen vendor payload still writes a fresh row.** This is the subtle one.
`T34` established, verified live 2026-09-04 at 17:55 ET, that Cboe's top-level `timestamp` is
**payload-generation time, not data-effective time**: it advanced to nearly two hours after
the close while the chain underneath stayed frozen at the close. `providers/cboe.py:293`
parses that field straight into `captured_at`.

The consequence for polling: **you cannot dedupe on the vendor timestamp**, because it is
exactly the field that keeps moving when the data does not. A poll into a stalled or
slow-refreshing endpoint produces a new `captured_at`, a new Parquet file, a new snapshot row
and a new set of level rows describing an instant that never happened. `T20`'s timeline would
plot it as a real reading.

### Design decisions

**Dedupe on content, not on time.** Compute a stable hash over the normalized chain — the
sorted per-contract tuple of (strike, expiry, right, bid, ask, iv, open_interest) plus spot —
and store it on the snapshot row (`content_hash`, indexed with `underlying`). Before writing,
compare against the most recent snapshot for that underlying; if equal, do not write the
Parquet file, do not insert, log a structured `capture_duplicate` line and return a
`CaptureResult` that says so. `CaptureResult` is already the single funnel for every outcome
(`app/jobs/capture.py`), so this is a new outcome, not a new control path.

**Judgment call, named: what happens when the EOD capture matches the 16:15 intraday poll.**
They very likely will match — the market closed at 16:00 and the feed is 15 minutes delayed,
so both readings reflect roughly the same settled chain. Silently skipping the EOD write would
be wrong: `T29`'s catch-up, `GET /api/health/capture` and the whole history view all key on
"an `is_eod = true` row exists for today". **Recommended resolution: promote, don't insert.**
On a content match where the incoming capture is `is_eod = true` and the matched row is not,
update the existing row's `is_eod` to `true` and return a distinct result state. The stored
`captured_at` then reflects 16:15 rather than 16:20, which is if anything the more honest
instant. The agent must verify that catch-up and the capture-health endpoint both accept a
promoted row.

**Order the constraint after the cleanup.** Adding `UniqueConstraint("underlying",
"captured_at")` will fail the migration if duplicates already exist. The migration must
report what it finds and resolve it explicitly (keep the earliest row per pair, leave the
orphaned Parquet files alone — deleting data is not this task's job) before adding the
constraint.

### T71 · Sonnet · T05

**Capture idempotency and content dedupe**

- Alembic migration in `backend/alembic/versions/`: add `snapshots.content_hash`
  (nullable — existing rows predate it), an index on `(underlying, content_hash)`, and
  `UniqueConstraint("underlying", "captured_at")` after a duplicate check that reports before
  it resolves.
- Compute the hash in the capture path, in `app/jobs/capture.py` — **not** in the provider and
  **not** in `app/gex/`, which invariant 1 keeps pure.
- Skip-on-match, promote-on-EOD-match, both as explicit `CaptureResult` states with their own
  structured log events.
- Tests: identical consecutive payloads produce one row and one Parquet file; a changed bid
  produces two; an EOD capture matching an earlier intraday row promotes rather than inserts;
  a promoted row satisfies `GET /api/health/capture`'s "today's EOD exists" check.

Acceptance: calling `capture_snapshot` twice against the same fixture payload leaves exactly
one `snapshots` row, one Parquet file, and one set of `gex_levels` rows.

## Verified facts (2026-09-11)

- `snapshots.__table_args__` contains only a non-unique index; no `UniqueConstraint`.
- `gex_levels` and `gex_by_strike` both carry unique constraints; `compute_and_store` relies
  on them for delete-then-insert idempotency.
- Cboe's top-level `timestamp` is naive **UTC**; `last_trade_time` inside each option is naive
  **America/New_York**. Two zones in one payload — `providers/cboe.py` documents this at
  module level and parses them separately. Any content hash that includes timestamps must not
  reintroduce the confusion; prefer excluding them from the hash entirely.
- `app/jobs/calendar.py` already ships `effective_data_time(captured_at, delayed_minutes)`,
  written in anticipation of T18: during a regular session it returns `captured_at` unchanged.
- Alembic is in use; five migrations exist under `backend/alembic/versions/`.
- Measured Parquet sizes and derived row/byte volumes as tabulated above.

## Likely first-contact failures

- Hashing a pandas frame directly and getting a hash that changes with column order, dtype
  inference or float repr. Hash an explicitly-ordered, explicitly-typed tuple sequence.
- Including `None` open interest in the hash in a way that collides with `0`. Invariant 3:
  `None` means unknown and excludes the contract, `0` means zero and includes it — they must
  hash differently.
- Adding the unique constraint in the same migration step as the duplicate cleanup and hitting
  a partial failure. Two steps, cleanup first, with the count logged.
- Assuming the Windows dev DB and the container DB agree on what duplicates exist. Run the
  duplicate report on whichever database is real at migration time.

## Out of scope

Deleting the orphaned `backend/data/` Parquet files — that is the state review's own item and
carries its own risk of deleting something the index still points at.
