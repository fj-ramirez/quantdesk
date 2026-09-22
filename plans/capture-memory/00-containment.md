# Containment (T86)

The first task, and the only unconditional one: whatever the gate says, no service on the
homeserver has a memory limit today, so any one of them can take the whole host.

## Goal

A runaway service is killed and restarted by Docker instead of taking the machine down with
it, and the allocator is told not to keep one arena per thread.

## What the user sees

Nothing, when it works. If a service does hit its cap, `docker ps` shows it restarting rather
than the whole stack becoming unreachable.

## Data

Host RAM is **5.677 GiB** (from `docker stats`' percentage denominator, 2026-09-21). Current
limits: none. `compose.prod.yaml` sets `restart: unless-stopped` and log rotation on every
service but no `mem_limit`, so a container's cgroup has no ceiling and the kernel's OOM killer
chooses its victim across the whole host.

Observed steady-state heap, per [README.md](README.md): backend 126 MB, gex-capture 626 MB
(climbing), research-search 96 MB, terminal-ingest 50 MB, postgres 13 MB.

## Design decisions

**Generous caps, not tight ones.** A cap exists here to stop one service taking the host, not
to make a number look good. `gex-capture` gets 1.5 GB against an observed 626 MB — because a
cap that trips during the 16:20 EOD capture converts a memory problem into a permanent hole in
the only dataset this repo cannot backfill. Halving the cap to "save" 750 MB of a machine that
is not under pressure buys nothing and risks the one thing worth protecting.

**Postgres is deliberately left uncapped.** It manages its own memory through `shared_buffers`
and work_mem, and most of its cgroup total is page cache it will give back under pressure.
Capping it is a good way to get the database OOM-killed mid-write, which is a far worse failure
than any of the ones this initiative is about.

**The caps sum to more than the host has, and that is intended.** 1.5 + 1 + 1.5 + 0.75 + 0.125
= 4.875 GB of capped services against 5.677 GB of RAM, with Postgres uncapped on top. These are
ceilings against a single runaway, not a reservation scheme; sizing them to sum to RAM would
mean every service gets a cap far below what it legitimately needs at peak.

**`MALLOC_ARENA_MAX=2` goes on the shared environment anchor in `compose.yaml`, not on one
service in the prod overlay.** Every Python service in this stack has the same allocator
behaviour and the same thread-pool shape, and the dev stack should reproduce production's
memory behaviour rather than diverge from it. glibc's default is up to 8 arenas per core, each
of which retains what it has freed; the capture worker runs 15 threads.

**The trade-off `MALLOC_ARENA_MAX` makes is contention for retention**, and it is measurable
rather than theoretical: two arenas across 15 threads means more lock contention on allocation.
The capture path already logs `duration_seconds` per symbol (`CaptureResult`, emitted as JSON
by `_log_result`), so the cost is visible in data the worker already produces. That is what the
acceptance criterion below checks.

## Tasks

## T86 · Sonnet · —

In `compose.prod.yaml`, add `mem_limit` to each service: `gex-capture` 1500m, `backend` 1024m,
`research-search` 1500m, `terminal-ingest` 768m, `frontend` 128m. Leave `postgres` uncapped and
say why in a comment, following the file's existing habit of explaining a deliberate absence.

In `compose.yaml`, add `MALLOC_ARENA_MAX: 2` to the `&backend_environment` anchor, so all four
Python services and both environments inherit it.

Use `mem_limit`, not `deploy.resources.limits.memory`: the latter is honoured by the Compose v2
CLI but is a swarm-shaped key, and this stack is plain compose. A comment saying so will save
the next person the same lookup.

Deploy with `docker compose -f compose.yaml -f compose.prod.yaml up -d`, and confirm the limits
landed with `docker inspect -f '{{.Name}} {{.HostConfig.Memory}}' $(docker ps -q)`.

## Verified facts

Measured 2026-09-21, not assumed:

* Host RAM 5.677 GiB; no service has a memory limit.
* `gex-capture` runs 15 threads in steady state (9 at boot).
* `CaptureResult.duration_seconds` is already emitted per symbol per capture in the structured
  JSON log line, so before/after timing needs no new instrumentation.
* `compose.yaml`'s `&backend_environment` anchor is shared by `backend`, `gex-capture`,
  `research-search` and `terminal-ingest`; `compose.prod.yaml` re-declares a subset of it as
  `&prod_backend_environment`. **Check both** — a variable added to only one of them reaches
  dev and not production.

## Acceptance

* `docker inspect` shows a non-zero `HostConfig.Memory` on all five capped services and `0` on
  postgres.
* A capture cycle completes after the change, and `duration_seconds` per symbol from the
  structured log is **within 25 % of the pre-change values** for the same symbols. A larger
  regression means arena contention is real on this host and `MALLOC_ARENA_MAX` should go to 4,
  or come out — record which, in the Result section.
* `scripts/mem-sample.sh --report` after a full session shows `gex-capture` anon below its
  pre-change trajectory. Record the number; it is the baseline T87 and T88 are measured against.

## Likely first-contact failures

* **`mem_limit` silently ignored.** Older Compose files under a v3 schema drop it. This repo's
  compose files declare no `version:` key, so they are parsed as the Compose Specification and
  `mem_limit` applies — but verify with `docker inspect` rather than assuming, which is what the
  acceptance step is for.
* **Adding the variable to the prod anchor only.** `compose.prod.yaml`'s
  `&prod_backend_environment` does not inherit from `compose.yaml`'s anchor; it is merged by
  compose at the service level. Putting `MALLOC_ARENA_MAX` in the wrong one gives a dev stack
  that behaves differently from production, which is the failure this file's design decision is
  trying to avoid.
* **A cap that trips during the 16:20 capture.** If `gex-capture` is OOM-killed, check
  `docker inspect -f '{{.State.OOMKilled}}'` before concluding anything else, and confirm the
  capture landed via `GET /api/gex/health/capture`. Raise the cap; do not tune it down.

## Out of scope

Swap configuration, `memory.high` / soft limits, and any cgroup tuning beyond a hard cap.
Restart-on-schedule as a mitigation — see [README.md](README.md)'s standing risk.

---

## Result — T86

**Done and deployed 2026-09-21.** Merged config verified before deploy, limits verified on the
host after it.

```
/quantdesk-backend-1          mem=1073741824
/quantdesk-frontend-1         mem=134217728
/quantdesk-gex-capture-1      mem=1572864000
/quantdesk-postgres-1         mem=0
/quantdesk-research-search-1  mem=1572864000
/quantdesk-terminal-ingest-1  mem=805306368
```

`MALLOC_ARENA_MAX=2` reads back from inside both `gex-capture` and `backend`, so the shared
anchor reaches production as the spec predicted it would. Nothing was OOM-killed on the way up.

**One deviation, forced rather than chosen.** `research-search` and `terminal-ingest` carry
their limit as `deploy.resources.limits.memory` instead of `mem_limit`, against what this file
asked for. Compose refuses the project outright otherwise:

```
services.research-search: can't set distinct values on 'mem_limit'
and 'deploy.resources.limits.memory': invalid compose project
```

An *unset* key counts as distinct, so a service that already has a `deploy.resources.limits`
block — which both of these do, for `cpus` — has to keep its memory limit there too. The
other three services have no deploy block and use `mem_limit` as specified. The failure is
loud and happens at config-parse time, so this cannot silently regress.

### Not yet verified

Two acceptance items need a trading session that has not happened yet, and both are recorded
here so they are not mistaken for passes:

* **`duration_seconds` within 25 %.** The pre-change baseline was taken from the worker's own
  structured log before the redeploy (113 successful captures, 2026-09-21), and is the number
  the post-change run is measured against:

  | Symbol | Captures | Contracts | Mean | Max |
  |---|---|---|---|---|
  | SPX | 18 | 29,518 | **3.319 s** | 3.718 s |
  | SPY | 18 | 12,388 | 1.520 s | 1.678 s |
  | QQQ | 18 | 10,560 | 1.236 s | 1.345 s |
  | GLD | 18 | 7,758 | 0.963 s | 1.063 s |
  | DIA | 18 | 4,778 | 0.644 s | 0.822 s |

  If SPX comes back above ~4.1 s, arena contention is real on this host: raise
  `MALLOC_ARENA_MAX` to 4, or drop it, and record which here.

* **The anon trajectory under the cap.** The pre-change water mark is 600–630 MB, reached
  within an hour of the open. That is the baseline T87 and T88 are measured against, and the
  redeploy reset the worker's heap, so the next full session is the first comparable one.
