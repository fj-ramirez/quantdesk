# The nightly sequence dies before `edges` (T90)

P0, and not from the eval — found while verifying it. Every scheduled run of the terminal
ingest worker stops at step 4 of 5, so the transmission graph is never recomputed on a
schedule.

## Goal

The nightly sequence runs to completion, and a step that cannot run says so without taking the
rest of the night with it.

## What the user sees

The transmission graph and every screen built on it stop being a day or more stale. Today the
board reports `as_of 2026-09-20T19:24:32` — yesterday's *manual* run — while the scheduled run
at 03:00 ET today produced nothing after `derive`.

## Data

`app/workers/terminal_ingest.py` runs `cli.main([step])` for
`("ingest", "derive", "fomc", "policy", "edges")`. The `policy` handler takes a **required
positional** argument:

```
cli.py:666    p_pol.add_argument("settlements", help="local CSV or JSON settlement file")
```

`cli.main` parses before it guards:

```
cli.py:~705   args = parser.parse_args(argv)          # outside the try
              ...
              try:
                  return handlers[args.command](args, settings)
              except XactxError as e:
```

argparse raises `SystemExit(2)` for the missing positional. `SystemExit` derives from
`BaseException`, so `_run_sequence`'s `except Exception` cannot catch it — despite that
function's docstring promising *"Each step is attempted even if an earlier one failed."* The
exception propagates out of `_run_sequence`, out of `asyncio.to_thread`, and ends the job.

`terminal.ingest_batches` is the proof:

| Run | Adapters that completed |
|---|---|
| 2026-09-21 07:00 UTC — **scheduled**, 03:00 ET | cboe, cftc, derived, **then nothing** |
| 2026-09-20 19:19 UTC — manual | cboe, cftc, fred, treasury, derived, **graph** |

No `graph` batch today; `terminal.edge_stats` is still stamped `as_of 2026-09-20T19:24:32`.

## Design decisions

**Catch `SystemExit` explicitly in the worker, and also stop the CLI raising it for this
case.** Both halves are needed and they fix different things. The worker guard makes *any*
step's hard exit survivable, which is the property `_run_sequence`'s docstring already claims.
The CLI change stops a step that structurally cannot run unattended from being scheduled as
though it could.

**`policy` should be dropped from `SEQUENCE`, not made to fail quietly.** It needs an operator-
supplied file that CME's terms forbid fetching automatically (`cli.py:229`). A step that can
never succeed in this context does not belong in a nightly list; leaving it in and swallowing
the error trades a loud failure for a silent one. It stays a first-class CLI command for hand
use, and T96 owns the question of what would let it run unattended.

**Log the skip at WARNING, once per run.** "The implied policy path did not update, and here is
why" is exactly the thing that should not be inferred from an absence, which is how this bug
survived in the first place.

**Do not widen `except Exception` to `except BaseException`.** That would swallow
`KeyboardInterrupt` and `asyncio.CancelledError` and make the worker unstoppable and
un-shutdownable. `except (Exception, SystemExit)` is the correct width.

## Tasks

## T90 · Sonnet · —

In `app/workers/terminal_ingest.py`: remove `"policy"` from `SEQUENCE`, and log one WARNING per
run naming it and the reason. Change `_run_sequence`'s per-step guard to
`except (Exception, SystemExit)` so no future step can do this again, and make the log line say
which it caught.

In `app/modules/terminal/cli.py`: move `parse_args` inside the guard, or catch `SystemExit`
around it, so `main` returns a code rather than raising for a usage error. It already returns
`2` for `XactxError`; a usage error should behave the same way.

Then confirm on the homeserver that the next scheduled run produces a `graph` batch in
`terminal.ingest_batches` and advances `edge_stats.as_of`.

Also worth a look while in here, **not** part of the fix: today's scheduled run produced no
`fred` and no `treasury` batch, though the manual run did. Same abort, or a second failure — if
the latter, file it separately rather than widening this task.

## Verified facts

Measured 2026-09-21, not assumed:

* `settlements` is a required positional at `cli.py:666`; the worker passes no arguments.
* `parse_args` is outside `cli.main`'s `try`.
* `_run_sequence` catches `Exception`, which excludes `SystemExit`.
* Today's scheduled batches stop after `derived`; yesterday's manual run reached `graph`.
* `edge_stats.as_of` = `2026-09-20T19:24:32`, matching the manual run rather than any scheduled
  one.

## Acceptance

* The suite passes, including a new test that a step raising `SystemExit` does not stop the
  sequence, and one that `cli.main([...])` returns a non-zero code rather than raising for a
  usage error.
* After one scheduled run on the homeserver: a `graph` row in `terminal.ingest_batches` dated
  that run, and `edge_stats.as_of` advanced to it.
* The WARNING naming the skipped policy step appears exactly once per run.

## Likely first-contact failures

* **Fixing only the worker.** The CLI still raises `SystemExit` for any usage error, so the next
  step added with a required argument reproduces this with a different name.
* **Fixing only the CLI.** Then `policy` returns 2, the sequence continues, and the failure goes
  back to being silent — which is how it survived.
* **Assuming `edges` is fine because `edge_stats` has rows.** It has 40, and they are yesterday's.
  Check `as_of`, not the count.

## Out of scope

Making the policy path actually run — that is T96, and it is a data-source question.
