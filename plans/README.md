# Plans

Design documents for work that is bigger than one `TASKS.md` block. Each initiative gets a
folder; each tool inside it gets one file that is ready to hand to an agent as-is. Task IDs are
allocated from one sequence so they never collide: open tasks are indexed in `TASKS.md`, finished
ones in `docs/archive/TASKS-T00-T107.md` and its addendum.

```
plans/
  README.md                         this file: conventions, dispatch rules
  continuation/                     "which markets are continuing, which are fading" (2026-09-09) — complete
    README.md                       initiative overview, dependency graph, dispatch order
    00-foundation-daily-bars.md     T42  shared daily OHLCV ingestion every tool below needs
    01-breakout-ledger.md           T43, T44
    02-trend-chop-scorer.md         T45, T46
    03-regime-board.md              T47, T48, T49
    04-sector-rotation.md           T50, T51
    05-etf-flows.md                 T52, T53
    06-cross-asset-regime.md        T54
    07-ui.md                        T55, T56, and the full specs for T44, T49, T51, T53
  ui-ux-refresh/                    workbench redesign (2026-09-10), T62-T69 — complete
    README.md                       task specs, dependencies, and the Result section
    01-ux-baseline.md               T62 read-only inventory and wireframes
    02-first-pass-review/           the user's own live-build review that produced T69
  continuous-feed/                  from one capture a day to a live picture (2026-09-11) — T20, T72 open; T21-T24 blocked
    README.md                       the two-feed model, tiers, dependency graph, dispatch order
    00-always-on-host.md            T70  the precondition: a backend that is actually always on
    01-capture-integrity.md         T32, T71  retention and idempotency, both gating T18
    02-intraday-polling.md          T18, T19, T20  the free 15-minute tier
    03-live-spot-overlay.md         T72  free live spot against a frozen surface
    04-realtime-paid.md             T21-T23 status and the 2026-09-11 vendor re-survey
  quantdesk/                        three apps, one desk (2026-09-19) — T81, T83-T85 open
    README.md                       module model, schema decision, dependency graph, dispatch order
    00-monorepo-skeleton.md         T75  GEX becomes a module; core/ and workers/ appear
    01-postgres-schemas.md          T76  gex / research / terminal schemas + the read-only role
    02-research-module.md           T77, T78  EdgeLab ports in; leaderboard becomes a page
    03-terminal-module.md           T79, T80  xactx ports in; the board finally gets built
    04-launcher-shell.md            T81  the page you land on, and the module switcher
    05-mcp-connector.md             T82  read-only MCP over all three schemas
  thetadata/                        ThetaData Options Standard (2026-09-25) — T125, T26, T126, T127
    README.md                       what the subscription gives, the five rules, dependency graph
  capture-memory/                   the capture worker's heap (2026-09-21) — complete (T89 not needed)
    README.md                       the measurements, the anon/page-cache distinction, the gate
    00-containment.md               T86  container limits and MALLOC_ARENA_MAX
    01-single-materialization.md    T87  stop rebuilding the chain from disk
    02-return-to-os.md              T88  malloc_trim and Arrow's release_unused
    03-tracemalloc.md               T89  conditional: only if the gate says "leak"
  decision-inputs/                  what the trade path reads (2026-09-21) — T94-T96 open
    README.md                       the eval, the verification that overturned parts of it
    00-nightly-abort.md             T90, T97  P0: the nightly sequence dies before `edges`
    01-empty-nodes.md               T91  three declared nodes that were never ingested
    02-relative-volume.md           T92  the volume column nothing reads
    03-factor-cap.md                T93  stop emitting one trade seventeen times
    04-sector-edges.md              T94  transmission at the level the trades live at
    05-crude-term-structure.md      T95  squeeze versus froth; the source survey is the task
    06-calendar-and-policy-path.md  T96  a source decision, not a wiring job
  desk-integrity/                   what the desk asserts versus what it measured (2026-09-21) — complete
    README.md                       the theme, the dependency graph, dispatch order
    00-wall-identity.md             T99   a wall is named by its gamma, never its position
    01-null-aggregates.md           T100  an empty aggregate is null, not zero
    02-expiry-and-session.md        T101, T102
    03-iv-persistence.md            T103  ATM and 30-day IV, from inputs already in memory
    04-capture-alerting.md          T104  something has to watch the health endpoint
    05-mcp-ergonomics.md            T105, T106  twelve connector calls down to four
    06-doc-drift.md                 T107  three documented facts the data contradicts
  audit-and-compaction/             logic audit + TASKS.md archive and decisions index (2026-09-22)
    README.md                       the plan and its Result: T108-T121, audit lanes, verification
```

## Conventions

- One file per tool. Each file has the same sections: *Goal*, *What the user sees*, *Data*,
  *Design decisions* (judgment calls, named as such), *Tasks* (in the `ID · Model · Depends`
  block shape used by `TASKS.md`), *Verified facts* (measured by the supervisor, not assumed),
  *Acceptance*, *Likely first-contact failures*, *Out of scope*.
- Task IDs are allocated here and reserved in `TASKS.md` before dispatch. **The next free ID
  lives in one place, the header of `TASKS.md`** — not here, so the two cannot disagree. (Past
  allocations: T42-T56 continuation, T62-T69 ui-ux-refresh, T70-T72 continuous-feed, T75-T85
  quantdesk, T86-T89 capture-memory, T90-T97 decision-inputs, T99-T107 desk-integrity,
  T108-T121 audit-and-compaction; the rest were filed directly as task blocks.)
- An initiative may adopt an **existing** ID rather than allocate a new one. `continuous-feed/`
  does this for `T18`-`T20`, `T21`-`T23` and `T32`: those were specified in `TASKS.md` in 2026-09-04
  and never built, so the plan file carries the full spec and the `TASKS.md` block stays as the
  numbered index pointing here. Renumbering shipped-or-specified work would break every
  reference to it.
- Model choice follows the project rule: Opus for new pure-math modules and new subsystems,
  Sonnet for views, ingestion against a spec, and additive wiring.
- The standing prompt preamble applies to every task: *"Read PLAN.md first. Work only inside
  the paths listed. Do not change the public interfaces defined in earlier tasks. Run the tests
  before reporting done."* Plus the hard rules from `context/workflow.md`: never kill processes
  by image name, never run two Opus agents concurrently, verify the artifact not the report.
- When a task lands, append its outcome (what was verified live, what was cut) to the plan file
  under a *Result* heading, then retire the block as `context/workflow.md` describes. The plan
  file is the memory of why.
- **A plan file is a dispatch unit, not just a spec.** Where its tasks share one model, one set
  of paths and a strict dependency chain, hand the whole file to a single agent in one worktree
  and drive the later tasks by message rather than re-dispatching cold. The eligibility rules,
  the per-task verification gate that still applies, and the cases that must stay cold (review
  passes, anything crossing a plan file) are in `context/workflow.md` § *Dispatching a cluster*.
- An agent running a cluster keeps its working memory in `NOTES-<cluster>.md` beside the plan
  file — verified facts, judgment calls, open items, appended after each task. It is what a
  replacement agent reads to start warm, and the raw material for the *Result* heading above.
  Delete it once the *Result* section is written.
