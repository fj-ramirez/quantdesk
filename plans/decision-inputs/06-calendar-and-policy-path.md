# Event calendar and the implied policy path (T96)

The eval's "single biggest hole". The verification agreed with it — and found the hole is
deeper than described, because the policy-path *code* is finished and has never produced a
single row.

## Goal

The desk knows what is scheduled before it recommends a trade, and the implied Fed path is a
series rather than a CLI command nobody can run unattended.

## What the user sees

A calendar of what is scheduled in the trade window — FOMC, CPI, claims, Treasury auctions —
and a market-implied policy path that updates on its own.

## Data

**The policy path is built and dead.** `terminal/policy.py` implements the ZQ decomposition
properly, including the arithmetic-average settlement algebra, and writes
`policy.ff.meeting_{i}` observations (`policy.py:350-356`). `terminal.observations` contains
**zero** `policy.ff.*` rows. The only policy-adjacent series in the database are `rates.effr`
(5,955 obs to 2026-09-17) and `credit.hy_ig.diff`.

The reason is in the code:

```
cli.py:666    p_pol.add_argument("settlements", help="local CSV or JSON settlement file")
cli.py:229    "Settlements come from a local file, not from cmegroup.com: automated access
               to their settlements endpoint is prohibited by CME's Data Terms of Use, and
               it carries only about a week of history regardless."
```

A required positional, supplied by hand. The nightly worker calls `cli.main(["policy"])` with
no arguments, which is the bug T90 fixes — but fixing the abort does not make the path run. It
needs a source.

Two declared graph edges are blocked on this: `be.5y → policy.ff.meeting_1` and
`policy.ff.meeting_1 → ust.2y.nominal`.

**The event calendar does not exist.** `terminal.releases` has **0 rows**, and `tables.py:123`
documents it as *"Empty, and knowingly so."* The FOMC calendar is a JSON file on disk
(`fomc.py`'s `save_calendar` / `load_calendar`), not a database table, so it is invisible to
every screen and to the MCP connector. Nothing covers CPI, claims or auctions.

## Design decisions

**Two halves, sequenced, and the calendar goes first.** The calendar is a solved problem with
free sources (the BLS and BEA release schedules, Treasury's auction calendar, the Fed's own
FOMC page that `fomc.py` already parses). The policy path is blocked on a licensing question
that may not have a good answer. Doing the calendar first means the task delivers something
even if the second half fails.

**The calendar belongs in `terminal.releases`, which was designed for it.** The table exists
and is empty *by intent* — the module was built expecting this. Use it rather than inventing
storage, and move the FOMC calendar off disk into it while there, so one mechanism answers
"what is scheduled" instead of two.

**For the policy path, survey OIS before anything else.** The question is narrow: a legally
usable, free or cheap daily source for the fed funds strip or the OIS curve. Candidates in
likely order: **FRED**, which already has an adapter here and carries several OIS and fed funds
series; the **NY Fed's** published reference rates; and only then paid futures data. Write the
answer down the way `PLAN.md` wrote the original data survey — source, coverage, cost, terms.

**If nothing qualifies, say so and leave the code.** `policy.py` stays as a hand-run CLI
command for the days someone has a settlement file, and the two edges stay empty with a
recorded reason. That is a legitimate outcome, and it is better than a path derived from
something that is not the market's.

**Do not have the calendar predict.** A release calendar says what is scheduled and when. A
consensus figure is a different dataset with different licensing, and the `releases` table has
a consensus column that should stay null rather than get filled with a guess.

## Tasks

## T96 · Opus · T90

**Half one — the calendar.** An adapter populating `terminal.releases` with scheduled releases
for at least FOMC, CPI, claims and Treasury auctions, from free official sources. Move the
FOMC calendar out of its JSON file into the same table. Expose "what is scheduled in the next
N days" on the terminal API so the trade path can read it, and make it available through the
MCP connector.

**Half two — the source survey.** Survey FRED, the NY Fed and the paid options for a fed funds
or OIS strip. Write the result into this file under a *Source survey* heading. If one
qualifies, wire it into `policy.py`'s existing decomposition — the algebra is done and tested,
so this is an adapter and a nightly step, not new science. If none qualifies, record that and
stop.

Depends on T90 because a nightly sequence that aborts will not run either half.

## Verified facts

Measured 2026-09-21:

* Zero `policy.ff.*` observations exist. The `policy` step has never successfully run in
  production.
* `settlements` is a required positional; the worker passes none.
* `terminal.releases` has 0 rows; the FOMC calendar is file-based.
* `rates.effr` is populated to 2026-09-17, so the *realised* rate is available — only the
  *implied path* is missing.
* Two declared edges are blocked on `policy.ff.meeting_1`.
* `policy.py`'s own docstring records that the spec's FedWatch validation **cannot be built**:
  CME publishes FedWatch for the current day only and sells history through DataMine. Do not
  re-attempt that comparison as an acceptance check.

## Acceptance

* `terminal.releases` carries the four event families, updating nightly, with the FOMC calendar
  migrated off disk and the old path removed rather than left as a second source of truth.
* "What is scheduled in the next N days" answerable through the API and the MCP connector.
* A written source survey with a decision, including the negative case.
* If a source was found: `policy.ff.meeting_1` carries observations and its two edges estimate.

## Likely first-contact failures

* **Filling the consensus column.** It is null for a reason; see the design decision.
* **Re-attempting the FedWatch validation.** The code already records that it is impossible.
  Validate the algebra against hand-computed cases, which the existing tests already do.
* **Leaving the JSON calendar in place as well.** Two stores for one calendar is the fork that
  `modules/research`'s registry refuses on principle (invariant 9); the same reasoning applies.
* **Treating `rates.effr` as a path.** It is the realised rate. Using it as a stand-in for
  expectations would populate the series with something that answers a different question.
* **Timezones on release times.** A release is scheduled at a wall-clock time in a specific
  zone; stored UTC per invariant 4, but the source's zone has to be read, not assumed.

## Out of scope

Consensus and surprise data; earnings calendars; intraday event handling; and any change to
`policy.py`'s decomposition, which is finished and tested.
