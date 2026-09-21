# Find the leak (T89) — conditional

**Only dispatch this if the gate in [README.md](README.md) says the capture worker's heap keeps
climbing overnight with no captures firing.** On the retention branch this task is wasted
effort: it will faithfully report that nothing is leaking.

## Goal

Name the allocation site. The code audit already cleared every leak shape worth suspecting by
inspection, so the next step is measurement from inside the process rather than more reading.

## What the user sees

Nothing. A log line per cycle in `docker compose logs gex-capture`, off unless switched on.

## Data

What the audit ruled out, so this task does not re-walk it: the SSE broker unsubscribes in a
`finally` and bounds its queues; every provider's `httpx.AsyncClient` is closed in a `finally`
by every job; the terminal module closes its psycopg connections at all seven call sites; there
are no module-level accumulators, no mutable default arguments, no dynamically-named loggers and
no unawaited-task pileup. Thread count is stable at 15.

## Design decisions

**Off by default, behind a setting.** `tracemalloc` costs roughly 2-3× in allocation overhead
and holds its own tables — measurable overhead in the process being measured. A
`GEX_TRACEMALLOC` setting defaulting to off, read the same way every other flag in
`app/core/config.py` is, keeps it a deliberate act.

**Diff consecutive cycles; keep exactly one previous snapshot.** The interesting quantity is
what *grew* between two cycles, not the absolute top-20, which will be dominated by legitimate
long-lived structures. Keep one snapshot in a module-level slot and replace it each time —
keeping a list of them would make the diagnostic the biggest leak in the process.

**Five frames, not the default one.** One frame usually names a line inside pandas or Pydantic,
which is true and useless. Five reaches this codebase's own call sites without much cost.

**`tracemalloc` has a blind spot and it matters here.** It sees only allocations made through
Python's allocator. Arrow buffers, NumPy arrays over a certain size and anything a C extension
mallocs directly are invisible to it — and this worker's biggest allocations are Arrow tables
and pandas frames. So the same log line must also carry `pa.total_allocated_bytes()` and a
histogram of live object counts by type from `gc.get_objects()`. If the Python-side diff is flat
while RSS climbs, that combination is what tells you the growth is in a C extension, which is a
different investigation with a different tool (`pympler`, or an allocator with profiling).

**Same placement as T88.** The scheduler's job-executed listener is already the per-cycle
boundary; this hangs off the same hook rather than inventing a second one.

## Tasks

## T89 · Sonnet · gate

Add `GEX_TRACEMALLOC: bool = False` to `app/core/config.py`. When set, `app/workers/gex_capture.py`
calls `tracemalloc.start(5)` before building the scheduler.

Extend the job-executed hook (T88's, or add it standalone if T88 has not landed) so that when
the setting is on it takes a `tracemalloc.snapshot()`, diffs it against the stored previous one
with `compare_to(prev, "lineno")`, and logs one line carrying: the job id, the top 20 entries by
size difference, `pa.total_allocated_bytes()`, and the five largest type counts from a
`gc.get_objects()` histogram with their deltas. Then replaces the stored snapshot.

Deploy with the setting on, let it run a full session, and read the log. The deliverable is the
named site and a decision recorded in this file's Result section — not a fix. Fix in a follow-on
task once the site is known.

## Verified facts

* The worker runs ten registered jobs and logs their ids at boot, so a per-job attribution in
  the diff line is meaningful — the leaking job is identifiable, not just the leaking line.
* `capture_all_symbols` runs symbols sequentially, so a per-cycle diff is not confounded by
  concurrent captures.
* The heap curve measured on 2026-09-21 rose fastest in the first fifteen minutes after boot
  (+310 MB), which includes the startup catch-up — the first diff after boot will be atypical
  and should be discarded rather than explained.

## Acceptance

* With the setting off, no behavioural change and no measurable overhead.
* With it on, one diff line per job execution, and after a full session the log names a site
  whose growth is monotonic across cycles.
* A Result section in this file recording what it named, or recording that the Python-side diff
  was flat while RSS climbed — which is an equally valid, equally useful answer that redirects
  the next task at the C extensions.

## Likely first-contact failures

* **Reading the first diff as signal.** See the verified fact above; discard the boot cycle.
* **Leaving it on.** It is a diagnostic. Turn it off once the site is named, or it becomes part
  of the steady-state memory profile it was meant to explain.
* **`gc.get_objects()` on a large heap being slow.** It walks every tracked object. Once per
  cycle is fine; anywhere in a loop is not.
* **Concluding "no leak" from a flat tracemalloc diff alone.** That conclusion needs the Arrow
  and object-count figures to be flat too.

## Out of scope

Fixing whatever this finds. The fix is a follow-on task written against a named site, which is
the point of doing this separately.
