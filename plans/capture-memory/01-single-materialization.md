# Stop rebuilding the chain from disk (T87)

The substantive fix. A capture holds 62,944 Pydantic contract models in memory, writes them to
Parquet, and then immediately reads the file back to build all 62,944 again — while the first
set is still alive.

## Goal

One materialization of a chain per capture instead of two, without changing a single stored
value and without touching the engine.

## What the user sees

Nothing. Same levels, same rows, same API responses. A capture cycle finishes slightly faster
and the worker's peak heap is roughly halved.

## Data

The path today, per symbol, in `jobs/capture.py`:

| Step | Where | What it allocates |
|---|---|---|
| fetch + parse | `providers/cboe.py:305` | vendor JSON dict, then one `OptionContract` per contract |
| write | `storage/parquet.py:write_snapshot` | an Arrow table from those models |
| **re-read** | `gex/store.py:110`, called from `jobs/capture.py:186` | **a second full set of `OptionContract` models, from the file just written** |
| flatten | `gex/store.py:111` | a pandas frame from the second set |

**Both sets are alive at the same time.** `capture_snapshot` still holds its `snapshot` after
the `compute_and_store` call — it reads `snapshot.underlying.value` and `snapshot.captured_at`
for the T19 broker publish immediately afterwards — so the second materialization does not
replace the first, it stacks on it.

Contract counts per cycle, measured from stored Parquet on 2026-09-21: SPX 27,782, SPY 12,312,
QQQ 10,436, GLD 7,676, DIA 4,738 — **62,944 total**, at a cycle every 15 minutes while the
market is open.

## Design decisions

**An optional `snapshot=` parameter on `compute_and_store`, not a new function.** The disk path
is not dead code: `gex/backfill.py:50` genuinely has only a `snapshot_id` and must keep reading
the file. One function with an optional shortcut keeps a single implementation of the levels
computation; two functions would be two things to keep in step.

**Pass the in-memory snapshot only when this call actually wrote it.** This is the load-bearing
decision in the task, and getting it wrong is how a subtle corruption gets in.

`_persist_sync` returns `skipped_duplicate=True` with the *pre-existing* row when it detects a
duplicate, and `capture_snapshot` then computes levels against that older `row.id`. Handing it
the freshly fetched chain would store levels for a snapshot whose Parquet file is a different
object — not obviously different in content, but no longer *provably* the same. The property
worth keeping is that **a snapshot's levels are reproducible from its stored Parquet**, which is
what makes `gex/backfill.py` a valid repair tool at all. So: fresh write → pass the object;
duplicate → keep reading from disk, exactly as today. Duplicates are the rare path and cost
nothing to leave slow.

**No change to the engine.** `gex/engine.py` and `greeks.py` stay untouched; invariant 1 holds
trivially because this task never opens those files. `compute_all` keeps taking a
`ChainSnapshot` plus the shared `frame`, as it does now at `store.py:115`.

**Equivalence is asserted by a test, not by reasoning.** The round trip through Parquet is
deliberately careful about `None` versus `0` for open interest (invariant 3 — `read_snapshot`
uses `to_pylist()` specifically so nulls come back as `None` rather than `NaN` or `0`). The
in-memory object is the input to that round trip, so it should be identical, but "should" is
not good enough for the one thing invariant 3 exists to protect.

## Tasks

## T87 · Opus · T86

In `app/modules/gex/gex/store.py`, give `compute_and_store` a keyword-only
`snapshot: ChainSnapshot | None = None`. When it is `None`, behave exactly as today: resolve the
path via `resolve_snapshot_path` (invariant 5) and `read_snapshot` it. When it is provided, skip
both and use it. Still load the `Snapshot` row and still raise `ValueError` for an unknown
`snapshot_id` — the row is what the levels are keyed to, and a caller passing a snapshot object
for an id that does not exist is a bug worth surfacing.

In `app/modules/gex/jobs/capture.py`, at the `compute_and_store` call on line 186, pass
`snapshot=snapshot` **only when `skipped_duplicate` is False**. Comment the condition with the
reproducibility reason above; it is not self-evident and will look like a missed optimization to
the next reader.

Leave `gex/backfill.py` alone.

Tests, in the existing gex test module for the store:

1. **Equivalence.** For a snapshot containing at least one contract with `open_interest=None`
   and one with `open_interest=0`, assert that `compute_and_store(id, snapshot=s)` and
   `compute_and_store(id)` produce identical `GexLevel` and `GexByStrike` rows. This is the test
   that protects invariant 3 across the change.
2. **The duplicate path still reads from disk.** Drive `capture_snapshot` so `_persist_sync`
   returns `skipped_duplicate=True`, and assert `read_snapshot` was called.
3. **The fresh path does not.** The same capture on a new snapshot, asserting `read_snapshot`
   was not called.

## Verified facts

Measured or read from the source 2026-09-21, not assumed:

* `compute_and_store` is at `gex/store.py:74`; its disk read is line 110 and the flatten is 111.
* Its only two callers are `jobs/capture.py:186` and `gex/backfill.py:50`.
* `capture_snapshot` still references `snapshot` after the `compute_and_store` call, for the
  broker publish — so the two materializations genuinely overlap.
* `compute_all` (`gex/engine.py:1468`) takes the snapshot *and* an optional prebuilt `frame`;
  `store.py` already builds the frame once and shares it across all three filters.
* 62,944 contracts per cycle across the five symbols.

## Acceptance

* Full backend suite green, plus the three new tests.
* One real capture cycle on the homeserver produces levels byte-identical to what the same
  snapshot would have produced before. Practical check: run `python -m app.modules.gex.gex.backfill`
  against the new snapshot, which recomputes from disk, and confirm it changes no rows.
* `scripts/mem-sample.sh --report` over a full session shows `gex-capture` anon peaking
  **materially below** the T86 baseline. This is the number the task is for; record it.

## Likely first-contact failures

* **Passing the snapshot on the duplicate path anyway**, because the condition reads like an
  accident. See the design decision; the test at (2) is there to catch exactly this.
* **Assuming the heap halves.** It will not, quite: the Arrow table, the pandas frame and the
  vendor JSON dict are all still allocated per capture. Halving the *Pydantic* peak is the claim.
* **Reaching for `del snapshot` or a `gc.collect()` to "help".** Neither belongs in this task.
  Returning freed memory to the OS is T88's job and has a correct mechanism.
* **Touching the engine to avoid the frame rebuild.** Out of scope, and invariant 1 constrains
  what may live there.

## Out of scope

Streaming the vendor payload instead of buffering it, `__slots__` or a dataclass rewrite of
`OptionContract`, and any change to the Parquet schema or the stored bytes.

---

## Result — T87

**Done 2026-09-21.** 1,158 backend tests green (4 added), ruff clean. Not yet deployed.

`compute_and_store` takes a keyword-only `snapshot: ChainSnapshot | None = None`. `None` keeps
today's behaviour exactly — resolve via `resolve_snapshot_path` (invariant 5) and
`read_snapshot` — which is what `gex/backfill.py` still does, untouched, having nothing but a
`snapshot_id`. The `Snapshot` row is loaded and the `ValueError` for an unknown id is raised
either way; a test pins that, because "I passed the chain" must not become "I skipped the
check".

`jobs/capture.py` passes `snapshot=None if skipped_duplicate else snapshot`, with the
reproducibility reason in a comment beside it. Both the condition and the comment are the
point of the task: on the duplicate path `row.id` is an *earlier* snapshot whose Parquet file
is a different object from the chain just fetched.

Four tests rather than the three the spec asked for:

1. **Equivalence**, over a chain carrying both `open_interest=None` and `open_interest=0`.
   Every persisted column of both tables is compared — `id` and `computed_at` excluded, being
   row identity and a clock rather than computed values — and the assertion is checked for
   vacuousness: `net_gex` is non-zero, strike 490 (the `0` contract) is in the per-strike rows
   and strike 510 (the `None` one) is not. That last pair is invariant 3 surviving the change,
   stated as data rather than as a claim.
2. **The fresh path does not read**, asserted as zero calls, with the levels still landing for
   all three filters — one materialization, not none.
3. **The duplicate path still reads**, exactly once.
4. **An unknown `snapshot_id` still raises** even with a chain in hand.

The spy patches `read_snapshot` on `app.modules.gex.gex.store`, where it is used, not on
`storage.parquet` where it is defined: `store.py` bound the name at import, and patching the
origin would have left its reference alone and quietly made every one of these tests pass.

**`asyncio.to_thread` forwards keyword arguments**, so the new one goes in beside
`session_factory` and `data_dir` with no `partial` wrapper. Worth stating because the first
draft reached for one.

### Not yet measured

The claim is that the *Pydantic* peak halves — roughly 63,000 models per cycle instead of
126,000 — not that the heap does: the Arrow table, the pandas frame and the vendor JSON dict
are still allocated per capture, and T88 is what returns any of it to the OS. The number goes
here after a full session against T86's recorded 600–630 MB water mark.

The acceptance item that needs the homeserver — `python -m app.modules.gex.gex.backfill` over
a snapshot captured by the new code, recomputing from disk and changing no rows — is also
still open. It is the real end of this task: it proves the stored levels are still
reproducible from the stored Parquet, which is the property the duplicate-path condition
exists to protect.
