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

---

## Result — T90

**Done 2026-09-21** (code). 1,118 backend tests green (5 added), ruff clean.

Both halves landed, as the design decision required:

* `app/workers/terminal_ingest.py` — `policy` removed from `SEQUENCE`, which is now
  `("ingest", "derive", "fomc", "edges")`. A new `UNSCHEDULED_STEPS` tuple carries the step and
  its reason, logged at WARNING once per run so the gap is stated rather than inferred. The
  per-step guard is `except (Exception, SystemExit)` and names the exception type it caught.
* `app/modules/terminal/cli.py` — `parse_args` is wrapped, so `main` returns an exit code for a
  usage error instead of raising. `--help` still returns 0, and the `__main__` block still turns
  the return into a real process status, so nothing changes for a human at a shell.

Tests went into `tests/test_terminal_cli_contract.py` rather than a new file — that file exists
because of the *same* class of bug (T79's `db_path` drift, silently failing in this same worker
at 03:00), and its opening docstring already makes the argument. Five: usage error returns 2,
`--help` returns 0, `policy` is absent from `SEQUENCE` and present in `UNSCHEDULED_STEPS`, a
step raising `SystemExit` does not truncate the sequence, and the unscheduled warning fires once
per run.

The existing `test_worker_sequence_steps_are_real_subcommands` parametrizes over
`["ingest", "derive", "edges"]` and never named `policy`, so removing the step broke nothing.

**Not verified, and it is the acceptance criterion that matters:** the homeserver has not been
deployed to, so no scheduled run has yet produced a `graph` batch. Until it does, `edge_stats`
stays stamped `2026-09-20T19:24:32`. Deploy, then confirm after the next 03:00 ET run:

```sql
SELECT adapter, started_at FROM terminal.ingest_batches ORDER BY started_at DESC LIMIT 8;
SELECT max(as_of) FROM terminal.edge_stats;
```

**Still open, deliberately out of scope:** today's scheduled run also produced no `fred` and no
`treasury` batch while the manual run did. That is consistent with the abort (both adapters run
inside `ingest`, before the `policy` step — so it is *not* explained by it, and is more likely a
second, independent failure). Worth a look once a clean scheduled run exists to compare against.

### Verified on the homeserver — 2026-09-21

Deployed, then the sequence was run by hand inside `terminal-ingest` rather than waiting for
03:00 ET. **It reached `edges`**, which it had not done since the bug was introduced:

```
terminal fomc: finished
terminal edges: starting
  ... 15 edges defined, 10 estimated, 5 not computable
terminal edges: finished
```

`terminal.edge_stats` now carries `as_of = 2026-09-22 00:10:04+00` across 50 rows, advanced
from the `2026-09-20T19:24:32` left by the last *manual* run, and `terminal.ingest_batches`
records a `graph` batch (`20260922T001005-1439d9e4`, status `ok`). The acceptance criterion
that mattered is met.

The five uncomputable edges are all missing an input series — `policy.ff.meeting_1`,
`eq.msci_em`, `cmdty.gold`, `eq.rut` — which is precisely what T91 and T96 exist for.

## T97 · Opus · T90

**The "still open" note above, explained and fixed.** The missing `fred` and `treasury`
batches were not a second mystery: `XA_FRED_API_KEY` is **empty on the homeserver**, so
`build_adapter("fred", ...)` raises `UnknownSeriesError` before any fetch — and that raise
left `cmd_ingest` entirely. `sources` is `sorted(FETCHABLE_SOURCES)`, so `treasury` sits
behind `fred` and never ran. A missing free API key for one vendor silently stopped a
different, keyless vendor's data.

It is T90's bug one level down: a step that dies at the first bad source instead of carrying
on to the independent ones. Same reasoning, same fix.

In `app/modules/terminal/cli.py`'s `cmd_ingest`, build the adapter inside a guard, record a
failure and continue to the next source; and on a mid-fetch exception, mark the batch failed
as today but continue rather than re-raise. Keep the non-zero exit code — a source that did
not run must still fail the run. Scope each batch's `ok`/`failed` to what *that* source did,
which the accumulating `failures` list no longer does once a run can get past a failure.

### Result — T97

**Done 2026-09-21.** 1,167 backend tests green (3 added), ruff clean.

`tests/test_terminal_ingest_isolation.py` drives `cmd_ingest` over the **real** universe with
a fake store, loader and adapters. Real on purpose: `sorted(FETCHABLE_SOURCES)` putting
`treasury` after `fred` is the thing that turned one missing key into two missing sources, and
an invented two-source universe would not reproduce it. Three tests — an adapter that cannot
be built, an adapter that fails mid-fetch, and a clean run — asserting in each case that the
*later* sources still ran and that the batch statuses say what actually happened.

**The per-source batch status is a real fix, not tidying.** `finish_batch(batch, "failed" if
failures else "ok")` read a list that accumulates across sources, so with the loop now
surviving a failure, every source after the first bad one would have been recorded as failed.
The batch table would have reported a total outage on a night when three of four sources were
fine.

**What this does not fix: there is still no FRED key.** The adapter cannot work without one,
and `fred` backs 29 of the registered series — the entire rate, breakeven and credit spine.
They are stamped `2026-09-18` and will simply stop moving. A free key takes a minute at
<https://fred.stlouisfed.org/docs/api/api_key.html>; it goes in the homeserver's
`/srv/docker/quantdesk/.env` as `XA_FRED_API_KEY=...` followed by a redeploy. Until then this
change buys the *other* sources back — `treasury` most of all, which publishes the par yield
curve daily and needs no credential at all.

### Verified on the homeserver, and the key arrived — 2026-09-21

Deployed, then `ingest` run by hand. Before the key was set:

```
filled       15
fetch_failed 1
  FAIL fred: adapter unavailable: FRED adapter requires an API key ...
```

**`treasury` ran, which it had not done since the key went missing**, and its latest
observation moved 2026-09-18 -> 2026-09-21: three sessions of the par yield curve recovered
by letting the loop continue past a source it could not build. `fred` is named in the output
instead of being an absence, which is the other half of the point.

The user then set `XA_FRED_API_KEY` on the server. After a redeploy:

```
filled       44
fetch_failed 0
```

All 44 fetchable series, FRED included. `derive` wrote 2 new observations and `edges` recomputed
the graph on the fresh panel. The five uncomputable edges are unchanged and unrelated -- they
want `policy.ff.meeting_1`, `eq.msci_em`, `cmdty.gold` and `eq.rut`, which are T91 and T96.
