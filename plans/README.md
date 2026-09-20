# Plans

Design documents for work that is bigger than one `TASKS.md` block. Each initiative gets a
folder; each tool inside it gets one file that is ready to hand to an agent as-is. `TASKS.md`
stays the single numbered index (one line per task pointing here) so task IDs never collide.

```
plans/
  README.md                         this file: conventions, dispatch rules
  continuation/                     "which markets are continuing, which are fading" (2026-09-09)
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
  continuous-feed/                  from one capture a day to a live picture (2026-09-11)
    README.md                       the two-feed model, tiers, dependency graph, dispatch order
    00-always-on-host.md            T70  the precondition: a backend that is actually always on
    01-capture-integrity.md         T32, T71  retention and idempotency, both gating T18
    02-intraday-polling.md          T18, T19, T20  the free 15-minute tier
    03-live-spot-overlay.md         T72  free live spot against a frozen surface
    04-realtime-paid.md             T21-T23 status and the 2026-09-11 vendor re-survey
  quantdesk/                        three apps, one desk (2026-09-19)
    README.md                       module model, schema decision, dependency graph, dispatch order
    00-monorepo-skeleton.md         T75  GEX becomes a module; core/ and workers/ appear
    01-postgres-schemas.md          T76  gex / research / terminal schemas + the read-only role
    02-research-module.md           T77, T78  EdgeLab ports in; leaderboard becomes a page
    03-terminal-module.md           T79, T80  xactx ports in; the board finally gets built
    04-launcher-shell.md            T81  the page you land on, and the module switcher
    05-mcp-connector.md             T82  read-only MCP over all three schemas
```

## Conventions

- One file per tool. Each file has the same sections: *Goal*, *What the user sees*, *Data*,
  *Design decisions* (judgment calls, named as such), *Tasks* (in the `ID · Model · Depends`
  block shape used by `TASKS.md`), *Verified facts* (measured by the supervisor, not assumed),
  *Acceptance*, *Likely first-contact failures*, *Out of scope*.
- Task IDs are allocated here and reserved in `TASKS.md` before dispatch. Next free ID as of
  2026-09-19: **T83**. (The continuation initiative ended at T56; T57-T61 were filed directly
  in `TASKS.md`; T62-T69 went to `ui-ux-refresh/`; T70-T72 to `continuous-feed/`; T73-T74 directly in
  `TASKS.md`; T75-T82 to `quantdesk/`.)
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
  under a *Result* heading and update the `TASKS.md` line. The plan file is the memory of why.
