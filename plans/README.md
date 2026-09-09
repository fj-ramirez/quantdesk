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
```

## Conventions

- One file per tool. Each file has the same sections: *Goal*, *What the user sees*, *Data*,
  *Design decisions* (judgment calls, named as such), *Tasks* (in the `ID · Model · Depends`
  block shape used by `TASKS.md`), *Verified facts* (measured by the supervisor, not assumed),
  *Acceptance*, *Likely first-contact failures*, *Out of scope*.
- Task IDs are allocated here and reserved in `TASKS.md` before dispatch. Next free ID after
  this initiative: **T57**.
- Model choice follows the project rule: Opus for new pure-math modules and new subsystems,
  Sonnet for views, ingestion against a spec, and additive wiring.
- The standing prompt preamble applies to every task: *"Read PLAN.md first. Work only inside
  the paths listed. Do not change the public interfaces defined in earlier tasks. Run the tests
  before reporting done."* Plus the hard rules from `context/workflow.md`: never kill processes
  by image name, never run two Opus agents concurrently, verify the artifact not the report.
- When a task lands, append its outcome (what was verified live, what was cut) to the plan file
  under a *Result* heading and update the `TASKS.md` line. The plan file is the memory of why.
