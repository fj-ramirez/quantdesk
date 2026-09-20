# quantdesk — three apps, one desk

Initiative opened 2026-09-19. Turns this repo (a clone of `gex-trading`, history preserved,
origin detached) into an umbrella application with selectable modules: **gex**, **research**,
**terminal**, and whatever comes next.

`gex-trading/` stays frozen on disk as the reference copy. Nothing in this initiative edits it.

## The model

One FastAPI process, one React app, one Postgres database, one compose stack.

```
                         ┌─ /              launcher: pick a module
  browser ── Caddy ──────┼─ /gex/*         the shipped GEX app, unchanged in behaviour
                         ├─ /research/*    EdgeLab: edge search + leaderboard
                         └─ /terminal/*    xactx: cross-asset board, regime, brief

                         ┌─ /api/gex/*     ┐
  api (1 container) ─────┼─ /api/research/*├─ routers mounted from app/modules/<name>/api
                         └─ /api/terminal/*┘
                                    │
  workers (n containers) ───────────┼──── same image, different entrypoint:
    gex-capture      APScheduler, EOD + intraday      │   gex schema
    research-search  APScheduler, nightly search cycle│   research schema
    terminal-ingest  xactx ingest/derive/factors      │   terminal schema
                                    │
                         Postgres (one database, three schemas) + DATA_DIR parquet
```

**Why one API process.** Single user, single machine. Three services would buy isolation
nobody needs and cost three Dockerfiles, three healthchecks and HTTP hops between modules.
The work that genuinely must not share a process — EdgeLab's search cycle, the capture
scheduler, xactx ingestion — is CPU-bound or clock-bound and already wants to be a worker.
So the split is **request path vs. background work**, not module vs. module.

**Why one database with three schemas** (`gex.`, `research.`, `terminal.`) rather than
`gex__snapshots` prefixes or three databases:

- Per-module grants. The MCP connector (T82) gets a read-only role with `GRANT USAGE` on
  three schemas — one statement each — instead of a table-name convention nothing enforces.
- `pg_dump -n research` backs up one module.
- `search_path` lets a module's own SQL stay unqualified.
- Three *databases* would put cross-module joins behind `postgres_fdw`, which defeats the
  point of merging at all.

**Why "coexist first, unify later"** (the user's call, 2026-09-19). Each module moves in
carrying its own ingestion, its own models and its own vocabulary. The duplication is real and
known — Yahoo daily bars are fetched by both `gex` and `research`; Cboe is fetched by both
`gex` and `terminal` — and it is deliberately left in place for now. A shared `core/` that all
three consume is the *next* initiative, written once there are three real consumers to design
against rather than one and two guesses. What T75 does build in `app/core/` is only the
irreducible minimum: settings, the SQLAlchemy session, and the schema constants.

## Environments

Lab and production are now equivalent and independent, which is what makes the SQLite and
DuckDB stores expendable:

| | lab | production |
|---|---|---|
| host | homeserver | VPS |
| postgres | its own | its own |
| compose | `compose.yaml` + `compose.override.yaml` | `compose.yaml` + `compose.prod.yaml` |

Nothing is copied between them at the database level. A module validated in the lab is
deployed to the VPS by image, and repopulates itself from its own sources.

## Dependency graph

```
  T75 skeleton ──┬── T76 schemas ──┬── T77 research port ── T78 research UI ──┐
                 │                 │                                          ├── T81 launcher
                 │                 └── T79 terminal port ── T80 terminal UI ──┘
                 │                                                            │
                 └────────────────────────────────────────────────────────────┴── T82 MCP
```

T77 and T79 are independent of each other and may run in either order — but never
concurrently, per the one-Opus-agent rule in `context/workflow.md`.

## Dispatch order

| | Task | Model | File |
|---|---|---|---|
| 1 | T75 monorepo skeleton ✅ | Opus | [00-monorepo-skeleton.md](00-monorepo-skeleton.md) |
| 2 | T76 Postgres schemas + read-only role ✅ | Opus | [01-postgres-schemas.md](01-postgres-schemas.md) |
| 3 | T77 research port, T78 research UI | Opus, Sonnet | [02-research-module.md](02-research-module.md) |
| 4 | T79 terminal port, T80 terminal UI | Opus, Sonnet | [03-terminal-module.md](03-terminal-module.md) |
| 5 | T81 launcher + module shell | Sonnet | [04-launcher-shell.md](04-launcher-shell.md) |
| 6 | T82 MCP connector | Opus | [05-mcp-connector.md](05-mcp-connector.md) |

T81 can be pulled forward to run against gex alone if a working launcher is wanted early; it
is listed last only because it is cheapest to build once all three module navs exist.

## Where the scheduled work runs

Three workers, one mechanism. Each uses the APScheduler pattern already established in
`app/jobs/scheduler.py` — built separately from the job functions, so the trigger policy is
readable in one place and testable without waiting for a clock.

| worker | trigger | catch up a missed fire? |
|---|---|---|
| `gex-capture` | cron, market hours, `America/New_York` | **yes** — the free Cboe feed serves only "now", so a missed EOD is gone forever |
| `research-search` | cron nightly, or interval — see T77 | **no** — a skipped cycle costs nothing; the search resumes where it left off |
| `terminal-ingest` | cron nightly, after the sources publish | **partly** — FRED/ALFRED serve history, so a gap refills on the next run |

That middle row is the point of the distinction. GEX's catch-up machinery exists because its
data is unbackfillable; copying it into the research worker would add a failure mode in
exchange for nothing.

## Verified facts

Measured on 2026-09-19, not assumed.

- `research/results/registry.db`: **134,377** rows in `trials`, **23** in `paper_candidates`.
  Two flat tables, no foreign keys, no views. `run_date` and `promoted_at` are stored as
  `TEXT`.
- `research/`: 33 MB `data/` (parquet OHLCV under crypto/forex/futures/stocks), 37 MB
  `results/`, 132 KB `reports/`. Git-initialized 2026-09-19 (`bcd727d`).
- `market-terminal/data/xactx.duckdb`: 28,061,696 bytes. Six tables — `series_metadata`,
  `observations`, `releases`, `ingest_batches`, `edge_definitions`, `edge_stats`.
- `market-terminal/src/xactx/store/schema.sql` is **already portable by design**. Its header:
  *"the DDL is kept close to ANSI so the same tables can be created in Postgres later without
  a redesign. Anything DuckDB-specific belongs in db.py, not here."* A grep for `ASOF JOIN`,
  `QUALIFY`, `PIVOT`, `read_parquet`, `arg_max` and `EXCLUDE (…)` across all of `src/xactx`
  returns nothing. The port is a driver swap, not a query rewrite.
- xactx is 6,628 lines. DuckDB call sites cluster in `store/` (22) with a tail in `brief.py`
  (15), `graph.py` (8), `derive.py` (4) and the analytics modules (10).
- `backend/app/main.py` mounts ten routers at `/api`; all ten move to `/api/gex` in T75.
- Next free task ID before this initiative: **T75**. This initiative allocates T75–T82; T83
  was then logged out of T76 (an `alembic heads` breakage inherited from T75), so the next
  free ID is **T84**.

## Out of scope for this initiative

- Order routing. Still never, in any module. `CLAUDE.md`'s first line survives the merge.
- Auth. Single user, one Caddy host, Tailscale for remote. A login page buys nothing yet.
- The shared `core/` consolidation (bars, symbols, calendar, provider ABCs). Named as the
  successor initiative, deliberately deferred.
- Paid data vendors. xactx phase 4 stays blocked on a consensus vendor; that does not change
  by moving house.
- Remote/HTTP MCP. T82 ships stdio only — see its file for why.
