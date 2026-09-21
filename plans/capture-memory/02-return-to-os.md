# Return freed memory to the OS (T88)

T87 lowers the peak. This task makes the worker hand back what it has already freed, so the
heap follows a sawtooth instead of a staircase.

## Goal

After each scheduled job finishes, memory that Python and Arrow have released is returned to
the kernel rather than retained in the process's arenas.

## What the user sees

Nothing. `scripts/mem-sample.sh --report` shows the capture worker's anon dropping after each
cycle instead of only ever rising.

## Data

From [README.md](README.md): the heap rose 125 → 626 MB over four hours and never fell once, in
26 samples, across roughly sixteen capture cycles. Freeing is happening — Python's refcounting
reclaims the chain objects as soon as each capture returns — but nothing gives the pages back.

Two allocators are involved and both need asking:

* **glibc.** Frees go back to a per-thread arena, not to the kernel. `malloc_trim(0)` walks the
  arenas and releases what it can.
* **Arrow.** `pq.read_table` and `write_table` allocate from pyarrow's own pool, which keeps
  its blocks for reuse. `pa.default_memory_pool().release_unused()` returns them.

## Design decisions

**The hook goes on the scheduler, not in the capture path.** An APScheduler `EVENT_JOB_EXECUTED`
listener registered once in `build_scheduler()` covers every job the worker runs — capture,
bars, flows, decisions, intraday, retention — rather than only the one being investigated, and
it fires *between* pieces of work by construction. Calling it inside `capture_all_symbols` would
run it between symbols, inside the 15-minute window a capture has to complete in, for no benefit.

**Never in `gex/engine.py` or `greeks.py`.** Invariant 1 makes those pure — no HTTP, DB,
filesystem or logging. A `ctypes` call into libc is emphatically not pure, and the fact that the
memory being trimmed is mostly *engine* memory is not a reason to put the call there.

**A helper module with a no-op fallback, because the dev host is Windows.** `malloc_trim` is
glibc-only: it does not exist on musl and there is no `libc.so.6` on Windows, where this code is
run by hand constantly. The helper resolves the symbol once at import, catches `OSError` and
`AttributeError`, and degrades to doing nothing. The tests must run on the dev host, so the
no-op path is the one they will mostly exercise — assert the helper is callable and harmless
rather than asserting memory moved.

**Log the before/after, at INFO, once per job.** The whole reason this initiative exists is that
nobody could see the number. A line per job with RSS before and after makes the effect
self-evident in `docker compose logs` and gives the acceptance check something to read that is
not a separate sampling run. Guard the RSS read the same way: `/proc/self/statm` where it
exists, skip the line where it does not.

**If `malloc_trim` returns almost nothing, that is a result, not a failure.** It can only
release whole free pages; a heap fragmented by many small live objects among freed ones will
give back little. A trim that recovers nothing says the retention is fragmentation rather than
arena hoarding, which points at the next lever — swapping the allocator (jemalloc or tcmalloc
via `LD_PRELOAD`) or cutting the peak further — and rules out the cheap fix. Record which it was.

## Tasks

## T88 · Sonnet · T87

Add `app/modules/gex/jobs/memory.py`: a `release_allocator()` that calls
`pyarrow.default_memory_pool().release_unused()` and then `malloc_trim(0)` through `ctypes`,
each independently guarded, and returns the RSS delta in bytes (or `None` where RSS is not
readable). Module-level resolution of both, so the guard is paid once rather than per call.

In `app/modules/gex/jobs/scheduler.py`, register an `EVENT_JOB_EXECUTED` (and
`EVENT_JOB_ERROR` — a job that failed has usually allocated the most) listener in
`build_scheduler()` that calls it and logs one INFO line with the job id and the delta.

Tests: `release_allocator()` is safe to call on a platform with no `malloc_trim` and returns
without raising; the listener is registered by `build_scheduler()`; a job execution event
triggers exactly one call.

## Verified facts

* `build_scheduler()` is at `app/modules/gex/jobs/scheduler.py` and already registers ten jobs;
  the worker logs their ids at boot.
* The API process runs no scheduler at all (invariant 7, `app/main.py` has no lifespan), so a
  scheduler-attached hook cannot affect request latency.
* The capture worker ran 15 threads in steady state, which is what makes glibc's per-thread
  arenas worth trimming.
* `scripts/mem-sample.sh` samples the cgroup's anon figure directly, so a successful trim is
  visible there without any new tooling.

## Acceptance

* Suite green on the Windows dev host, where `malloc_trim` does not exist — this is the real
  test of the fallback.
* On the homeserver, the INFO line appears once per job with a non-`None` delta.
* `scripts/mem-sample.sh --report` over a full session shows `gex-capture` anon **falling
  between cycles** rather than monotonically rising. A sawtooth is the pass condition; a lower
  staircase is a partial pass and means the fragmentation reading above — record it.

## Likely first-contact failures

* **`ctypes.CDLL("libc.so.6")` raising at import** and taking the whole worker down with it.
  Resolve it inside a `try`, at module level, and store `None` on failure.
* **Calling `malloc_trim` on the event loop and blocking it.** It is normally sub-millisecond,
  but on a large fragmented heap it can take longer. Measure it with the delta line already
  being logged; if it exceeds a few tens of milliseconds, move the call into
  `asyncio.to_thread`.
* **Expecting the RSS delta to match the anon drop in the sampler.** They measure different
  things — `/proc/self/statm` is the process, the sampler is the cgroup including every
  short-lived healthcheck process. Directionally equal is what matters.
* **Registering the listener in the worker instead of `build_scheduler()`.** It belongs with the
  scheduler so the tests can assert it without starting a process.

## Out of scope

Switching allocators (`LD_PRELOAD` of jemalloc or tcmalloc), `gc.freeze()`, tuning
`MALLOC_TRIM_THRESHOLD_`, and any periodic timer-driven trim independent of the job schedule.
All are reasonable next levers if this task under-delivers; none should be bundled into it.
