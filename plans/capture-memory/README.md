# capture-memory — the capture worker's heap (T86–T89)

Opened 2026-09-21, after "the deployed services keep increasing memory usage". The measurement
found one service growing and the rest flat, so this initiative is about `gex-capture` and
nothing else.

## The evidence

Two readings of the homeserver, on either side of an unrelated reboot at 11:49 ET.

**Before** (containers at ~15 h uptime, `docker stats` totals — cgroup total, page cache
included):

| container | total |
|---|---|
| backend | 762 MB |
| gex-capture | 613 MB |
| research-search | 380 MB |
| postgres | 168 MB |

**After** the reboot, sampled every 15 minutes for four hours by `scripts/mem-sample.sh`,
which records the anon/file split rather than the total:

| container | anon at boot | anon 4 h later | verdict |
|---|---|---|---|
| gex-capture | 125.4 MB | **625.9 MB** | climbing |
| backend | 125.1 MB | 125.7 MB | flat |
| research-search | 95.9 MB | 96.3 MB | flat |
| terminal-ingest | 49.8 MB | 50.2 MB | flat |
| postgres | 10.4 MB | 13.1 MB | flat |

**The anon/file split is the whole diagnosis and it is why the first reading misled.** The
cgroup total includes page cache, and this stack generates a lot of it: `gex-capture` writes a
Parquet file per capture and had 89 MB of charged-but-reclaimable cache one minute after boot;
Postgres's total climbed 70 → 166 MB in four hours while its *heap* moved 3 MB. Cache growth
looks exactly like a leak in `docker stats` and is the kernel doing its job. Only `anon` can
leak.

The capture worker's heap, every 15 minutes:

```
15:51  125 MB     17:06  571 MB
16:06  435 MB     17:21  575 MB
16:21  490 MB     17:36  596 MB
16:36  508 MB     17:51  591 MB
16:51  553 MB     18:37  620 MB
                  19:44  626 MB
```

Monotonic, never returned — but **decelerating**: +310 MB in the first fifteen minutes, +5.7 MB
in the last sixty-seven. Thread count went 9 → 15 and stopped, so nothing leaks threads.

## What is being allocated

One 15-minute capture cycle materializes **62,944 option contracts**, measured from the stored
Parquet on 2026-09-21:

| Symbol | Contracts |
|---|---|
| SPX | 27,782 |
| SPY | 12,312 |
| QQQ | 10,436 |
| GLD | 7,676 |
| DIA | 4,738 |
| **cycle total** | **62,944** |

And it materializes each of them **twice**:

1. `providers/cboe.py:305` builds one Pydantic `OptionContract` per contract, on top of the
   parsed vendor JSON dict, which is still alive for the whole loop.
2. `storage/parquet.py:write_snapshot` converts that to an Arrow table and writes it.
3. `gex/store.py:110` — called from `jobs/capture.py:186`, immediately after — **re-opens the
   Parquet file just written** and rebuilds all 62,944 Pydantic models from it, then flattens
   them into a pandas frame at `store.py:111`.

So each cycle churns on the order of 150 MB of short-lived objects, spread across the worker's
thread pool. That is the shape that produces a high allocator water mark: peak live set is
large, and glibc keeps per-thread arenas rather than returning them.

## The gate — is it retention or a leak?

**This decides which tasks below are worth doing, and it must be read before dispatching
anything.**

The overnight sampler (40 samples, 30 minutes apart, started 2026-09-21 19:44 UTC) spans a
closed market, so no captures fire for most of it. Read it with:

```
scripts/mem-sample.sh --report
```

* **`anon MB/day` for `quantdesk-gex-capture-1` near zero overnight → retention.** The heap is
  an allocator water mark, not a leak. Dispatch T86, T87, T88. Skip T89.
* **Still climbing with no captures firing → a real leak.** Dispatch T86 for containment and
  T89 to find it. T87 is still worth doing but only slows the bleed; T88 will not help.

Supporting evidence for the retention reading, to be weighed rather than trusted: the container
measured at 15 h uptime held 613 MB and the one measured at 4 h held 626 MB. If something leaked
per capture, fifteen hours would be far past 626 MB. Two points are not a trend, which is what
the overnight run is for.

### The gate's answer — 2026-09-21

**Retention. Dispatch T86, T87, T88; skip T89.**

Across the three and a half hours after the 20:00 UTC close, with no captures firing,
`gex-capture`'s heap went 625.9 -> 599.6 -> 623.6 -> 619.6 MB and settled there: oscillating
within about 25 MB and **ending below where it started**. A leak does not give memory back, and
this gave some back and then held. Combined with the 613 MB-at-15 h against 626 MB-at-4 h
reading above, the shape is a water mark of roughly 600-630 MB that fills during the session
and holds.

Note that `--report`'s own `anon MB/day` column reads ~1,600 for this container and should be
ignored here: it is a first-to-last slope across the whole file, so it is still dominated by
the 125 -> 626 MB climb in the fifteen minutes after boot. The trajectory is the evidence, not
the slope.

## Dependency graph and dispatch order

```
        gate (read the sampler)
          │
          ├── retention ──► T86 ──► T87 ──► T88
          │
          └── leak ───────► T86 ──► T89 ──► (T87 on its findings)
```

`T86` is unconditional: the host has no memory limit on any service today, and that is true
regardless of which branch the gate picks.

| File | Tasks |
|---|---|
| [00-containment.md](00-containment.md) | T86 — container limits and `MALLOC_ARENA_MAX` |
| [01-single-materialization.md](01-single-materialization.md) | T87 — stop rebuilding the chain from disk |
| [02-return-to-os.md](02-return-to-os.md) | T88 — release freed memory after each cycle |
| [03-tracemalloc.md](03-tracemalloc.md) | T89 — **conditional**, only on the leak branch |

## The standing risk on every task here

`gex-capture` owns the one artifact in this repository that cannot be recreated: the free Cboe
endpoint serves only "now", so a capture that does not happen at 16:20 is a permanent hole.
Every task below is therefore biased towards *not interrupting capture* over *saving memory*.
That is why T86's limits are generous rather than tight, why T88's hook runs between cycles
rather than inside one, and why nothing here proposes restarting the worker on a timer.

## Out of scope for this initiative

* **The backend's heap** -- and the reason it is out of scope is sharper than it first looked.
  It sat at 762 MB before the reboot, then at *exactly* 125.7 MB with 12 processes for three
  and a half hours after it, and then jumped to 185.2 MB with 15 processes the moment the MCP
  connector was reconnected on 2026-09-21. **The connector runs as `docker exec ... python -m
  app.mcp` inside this container** (see `.mcp.json`), so an MCP session's own process and heap
  are charged to the backend's cgroup and are indistinguishable from the API's in `docker
  stats`. A long Claude session doing database work therefore *looks* like an API memory leak.
  The pre-reboot 762 MB should be read with that in mind. If the backend is ever investigated,
  separate the uvicorn process from whatever `docker exec` sessions are attached first --
  `/proc/1/status` versus the cgroup total -- or the measurement is of the wrong thing.
* **`Registry` never disposing its engine** (`modules/research/registry.py:109` creates an
  `Engine` per cycle; `close()` at `:396` closes only the session, and `dispose()` appears
  nowhere in the codebase), and the **five session factories per process** (`core/db.py` plus
  `gex/store.py:70`, `bars_repository.py:56`, `flows_repository.py:62`,
  `decisions_repository.py:48`, each with its own pool against one `DATABASE_URL`). Both are
  real and both were found during this investigation. Neither is remotely large enough to
  explain 500 MB, and `research-search`'s heap is flat, so they are hygiene rather than this
  initiative's subject. File them separately.
