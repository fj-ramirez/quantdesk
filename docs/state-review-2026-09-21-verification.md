# Verification of the 2026-09-21 state review

Companion to [state-review-2026-09-21.md](state-review-2026-09-21.md), written the same day.

That document closed by naming its own limitation:

> the diagnoses are grounded in observed rows, but the *causes* are inferred and none has been
> confirmed against the source.

This is that confirmation pass. **Basis:** the same live Postgres via the read-only MCP
connector, plus the backend source, which the original review deliberately did not read.

**Headline: every finding reproduces.** F4's outage is visible incidentally in any QQQ levels
pull; F7's four drift items all confirm; F7d's figures reconcile against the database exactly.
The symptoms were right every time.

**Two of the inferred causes were wrong in ways that change the fix**, and both are in the
highest-priority items. That is the honest score for a read-only review: it finds *what* is
broken reliably and *why* unreliably, so a source pass before dispatch is not optional.

---

## F1 — confirmed, but the mechanism and the fix are both different

### The cause is one layer up from where the review looked

The review inferred that wall selection "picks the nearest significant strike below spot and
labels it the put wall." It does not. `scan/regime.py:413` `_nearest_walls` selects **by
position deliberately**, and says so:

> the two walls are not guaranteed to straddle spot (DIA's own 2026-09-05 capture has its two
> most negative strikes *above* spot) … so this picks by position, not by assuming `put_wall`
> is always "below"

That reasoning is correct and the code is correct. The defect is that `_nearest_walls` returns
bare `(strike, gex)` tuples which have **lost their identity as call wall or put wall**, and
`scan/decisions.py` then re-invents that identity from position alone:

```python
wall_word = "call wall" if side == "SHORT" else "put wall"      # decisions.py:553
key=f"FADE_{'CALL' if side == 'SHORT' else 'PUT'}_WALL"          # decisions.py:699
```

`side` is purely positional — `SHORT` for the wall above spot, `LONG` for the one below. So
whenever a wall sits on the "wrong" side of spot, it is renamed to match its position.

### The review's proposed fix would reintroduce the bug the code already avoids

> **Fix:** … it should read `gex_levels.put_wall` (or recompute the argmin over `net_gex`), not
> scan for proximity.

Reading `gex_levels.put_wall` and assuming it is the wall below spot is exactly the assumption
`_nearest_walls` exists to prevent, and it would break the DIA-shaped case its docstring cites.

The actual fix is one line and cheaper than either option offered: **name the wall from
`wall.net_gex`.** `WallInfo` already carries `net_gex`, and `_fade` already has the object in
hand — it reads `wall.abs_gex` two lines below the mislabel. Keep the positional selection;
replace only the naming.

### The bug is symmetric, and the review checked one half

The review filtered `key = 'FADE_PUT_WALL' AND entry = call_wall`, so it could only find one
direction. Filtering both:

| id | sym | date | key | entry | spot | net gamma at entry | outcome |
|---|---|---|---|---|---|---|---|
| 23 | SLV | 09-10 | **FADE_CALL_WALL** | 58 | 57.44 | **−7.5mn** | stop, −0.23R |
| 32 | XLE | 09-11 | FADE_PUT_WALL | 65 | 65.12 | +51.5mn | stop, −1R |
| 50 | QQQ | 09-18 | FADE_PUT_WALL | 720 | 722.0 | +715mn | pending |
| 77 | SPY | 09-21 | FADE_PUT_WALL | 772 | 773.2 | +1.39bn | pending |
| 78 | QQQ | 09-21 | FADE_PUT_WALL | 740 | 741.0 | +662mn | pending |
| 80 | DIA | 09-21 | FADE_PUT_WALL | 516 | 519.9 | +41.4mn | pending |

Six rows, not five. SLV id 23 is the mirror: both walls sat **above** spot (57.44 < 58 < 70),
so the *put* wall became `wall_above` and was emitted as `FADE_CALL_WALL`.

```sql
SELECT d.id, d.underlying, d.decided_on, d.key, d.entry, d.spot,
       l.call_wall, l.call_wall_gex, l.put_wall, l.put_wall_gex, d.outcome, d.result_r
FROM gex.decisions d
JOIN gex.gex_levels l ON l.snapshot_id = d.snapshot_id AND l.filter = d.filter
WHERE (d.key = 'FADE_PUT_WALL'  AND d.entry = l.call_wall)
   OR (d.key = 'FADE_CALL_WALL' AND d.entry = l.put_wall)
ORDER BY d.decided_on;
```

### The two halves are not equally wrong, which inverts the review's severity reasoning

The review grades all of it "wrong level, wrong side of the book." That is true of exactly one
row, and it is the row it did not find.

The five `FADE_PUT_WALL` rows go **long into a large positive-gamma strike below spot**. The
thesis claims dealers buy weakness there — and at +662mn they do. The mechanism holds; only the
name is wrong. These are mislabeled, not misjudged.

SLV id 23 goes **short into the most negative-gamma strike in the book** while asserting
"hedging sells strength." At a short-gamma strike dealer hedging amplifies rather than resists,
so the stated mechanism is inverted, not merely misnamed. That one is a genuine trade error.
It stopped out.

Consequence for the fix: the review's guard — "assert `sign(net_gex) < 0` for `FADE_PUT_WALL`
… and refuse to emit otherwise" — is right for the SLV shape and too blunt for the other five,
where it would suppress a defensible setup in order to correct a labelling bug. The four
sign/position combinations need enumerating rather than collapsing into one assertion; see
[plans/desk-integrity/00-wall-identity.md](../plans/desk-integrity/00-wall-identity.md).

### New: the bug has contaminated the track record

Not in the original review, and it bears directly on F7d. Both mislabeled rows that have
resolved are losses, and each is the only loss dragging its key down:

| key | n | avg R | excluding mislabeled | removed |
|---|---|---|---|---|
| `FADE_CALL_WALL` | 7 | +1.162R | **+1.394R** (n=6) | SLV −0.231R |
| `FADE_PUT_WALL` | 4 | +0.133R | **+0.510R** (n=3) | XLE −1.000R |

Two of the eleven resolved fade decisions are scored under the wrong key. At n=1 each this is
not evidence that mislabeling *causes* losses, but the only scorecard the desk has is
measurably contaminated — and those are the exact numbers F7d bakes into a prompt file.

---

## F1b — new finding: no magnitude floor on wall selection

A third manifestation of the same "wall identity is never validated" defect, this time in the
engine rather than in `decisions.py`. Visible in any `gex_levels` pull, QQQ `ZERO_DTE`:

| captured_at | call_wall | call_wall_gex | put_wall | **put_wall_gex** |
|---|---|---|---|---|
| 09-21 19:59 | 743 | +4.35e6 | 740 | **−2.17e−20** |
| 09-11 19:59 | 716 | +2.00e8 | 742 | **−5.48e−37** |
| 09-11 19:44 | 715 | +1.79e9 | 665 | **−1.87** |

`engine.py:1353` takes `argmin(net)` unconditionally. Over an all-but-positive array that
returns the *least positive* strike and reports it as a put wall. A put wall carrying −1.87
dollars of net gamma against a book whose maximum is +1.79bn is not a wall; the bottom two are
floating-point residue.

**This is why F1's guard cannot be sign-only.** All three rows above are technically negative
and would pass a sign assertion unchanged. Whatever guard lands must be magnitude-relative.

---

## F3 — confirmed exactly; the fix is larger than stated

The inferred cause is right, and it is visible at `engine.py:1316`:

```python
if strike_gex.empty:
    return KeyLevels(net_gex=0.0, call_gex=0.0, put_gex=0.0, abs_gex=0.0,
                     call_wall=None, call_wall_gex=None, put_wall=None, ...)
```

Every *level* is correctly `None`; the four *aggregates* are `0.0`. The exclusion logic is
right and the aggregation is what conflates unmeasured with flat — as diagnosed.

Two corrections to the fix estimate:

- **Storage is already free.** `GexLevel.net_gex` is `nullable=True`, and the model docstring
  argues the null case at length: "Storing `0.0` … would misreport 'no level exists' as 'level
  at strike zero'." The schema was built for this; only the engine disagrees with it.
- **The domain type is not.** `KeyLevels.net_gex` is declared `float`, not `float | None`, and
  `regime.py:499` passes it straight into `dealer_positioning(levels.net_gex, levels.abs_gex,
  …)`. So this is a type widening plus a consumer audit, not a one-line change.

The review's open question — "should a capture that admits zero contracts be persisted as a
snapshot at all?" — is already answered by that same docstring: yes, and nulls are the designed
representation. Its second question stands: `ZERO_DTE` legitimately empty and a chain that
admitted nothing are still indistinguishable after the fix.

---

## F7c — two series, not one

> `vol.vix` last prints 09-18 … Worth checking that one series' ingest path.

`vol.skew` is stale at 09-18 alongside it, while `vix9d` / `vix3m` / `vix6m` are all 09-21.

| series | last value_date | last as_of |
|---|---|---|
| `vol.vix` | 2026-09-18 | 2026-09-18 |
| `vol.skew` | 2026-09-18 | 2026-09-18 |
| `vol.vix9d` | 2026-09-21 | 2026-09-21 |
| `vol.vix3m` | 2026-09-21 | 2026-09-21 |

Two series stalling together points at a shared source rather than one broken series, which is
a materially better place to start looking.

---

## F2, F4, F5, F6, F7a/b/d, F8 — confirmed as written

- **F2** — confirmed, and it is not a separate fix. The prose at `decisions.py:610` interpolates
  the same `wall_word` variable assigned at `:553`. So do `entry_label`, `stop_label`,
  `target_label`, `beyond_word` and three of the four invalidation clauses. Correcting that one
  assignment corrects every surface at once; F2 is a consequence of F1, not a second task.
- **F4** — corroborated incidentally: a QQQ levels pull jumps from 2026-09-20T15:10 straight
  back to 2026-09-11T20:19. The container logs remain unread; still needs shell access.
- **F5** — confirmed. `GexByStrike` is `(id, snapshot_id, filter, strike, call_gex, put_gex,
  net_gex)`. No expiry column exists.
- **F6** — confirmed. No IV column on `gex.snapshots` or any sibling.
- **F7a** — confirmed and understated: `cmdty.gold` holds **1,262 observations** through 09-21,
  not merely a recent value. The docs describe a series that has been populated for a while.
- **F7b** — confirmed. `ust_cc.10y` at 09-21 against `ust.10y.nominal` at 09-18.
- **F7d** — confirmed to the decimal. 40 rows with an R, +0.187R, SE ±0.180, and every per-key
  figure matches. One reconciliation note for whoever fixes it: **97 decisions have a non-null
  `outcome` but only 40 have a non-null `result_r`.** "Resolved" means the latter. A query
  written against `outcome IS NOT NULL` returns 97 and a different answer.
- **F8** — confirmed.

---

## The MCP connector

Evaluated alongside the review, after the observation that a research session spends around
twelve calls on it.

### The cost is real and it is rows, not prose

`gex_levels("QQQ")` with defaults returns **~9,000 tokens, truncated at 100 rows**, to answer a
question whose answer is the first three. Roughly 97% waste — and it arrives with a truncation
warning telling the model any summary is incomplete, at a moment when it had 33× more data than
it needed.

The per-tool notes are **not** the problem: ~60 tokens against ~9,000. The "caveats travel with
the data" principle is the best thing about this connector and should survive untouched. The
waste is entirely in row shape and tool granularity.

### Five structural reasons the call count is what it is

1. **`gex_levels` contradicts its own description.** It promises "the most recent snapshot on or
   before `date`", singular. The SQL applies no such restriction — `ORDER BY captured_at DESC`
   capped by `limit`, so you get roughly 33 snapshots across 3 filters.
2. **`gex_levels` takes one symbol.** The real question is nearly always the board. Across a
   28-symbol universe that is 28 calls, or a drop to `query_sql` — which is what the
   `market-research` skill actually teaches.
3. **No freshness tool exists**, yet the skill opens with "Check freshness before quoting a
   single number — non-negotiable, and first, every time", followed by hand-written SQL. One
   guaranteed `query_sql` per session, for the single thing the connector most wants enforced.
4. **No decision-summary tool.** The skill hardcodes the per-key breakdown as SQL *and* bakes
   stale results into prose. That is F7d, and it is a tool-surface gap expressing itself as
   documentation rot.
5. **`gex_decisions` returns too little to be terminal.** No `spot`, `stop`, `target`,
   `snapshot_id` or `payload.thesis`, and no `key` filter — so any question about a level or a
   rationale becomes a `query_sql` follow-up. F2 was found that way.

### One factual bug in the connector's own guidance

`gex_decisions` tells every caller:

> `outcome` null means still pending, which is different from a loss.

**`outcome` is never null.** The literals are `pending` (51), `stop` (27), `target` (13) and
`untriggered` (6). A model following that note's null-check concludes nothing is pending. The
note also omits `untriggered` entirely — six decisions that never filled, which the skill
separately and correctly insists are not losses. The real pending test is `result_r IS NULL`.

This is the connector making the same class of error the review documents in the data: a
confident assertion about a column, one table over from the column.

### Target

Twelve calls to four or five. Fix (1) and (2) in one `gex_levels` change; add (3) and (4) as
tools — the track-record tool retires F7d permanently, because there is then nothing left to
hardcode; widen `gex_decisions` and correct its note. Optionally fold the schema resource into
`query_sql`'s error path, appending the relevant table's columns on an unknown-column error, so
the schema is paid for when it is needed rather than speculatively up front.

Planned as [plans/desk-integrity/05-mcp-ergonomics.md](../plans/desk-integrity/05-mcp-ergonomics.md).

---

## What this pass says about the method

The original review found eight real problems from a read-only seat, with a reproducing query
for each, and none of them was a false positive. That is a good result and the method should be
used again.

It also inferred two causes wrongly, in the two findings it ranked first and third, and in both
cases the inferred fix would have caused a new problem: F1's would have reintroduced the
straddle bug the code documents, and F3's underestimated a type change as a value change. Both
errors share a shape — reasoning from the data to a plausible implementation, and getting a
simpler implementation than the real one, because the real one already handles a case the data
did not happen to show.

The rule that follows: **a data-only review is a list of symptoms, not a list of fixes.** Its
findings are dispatchable; its causes are hypotheses, and each one needs a source pass before
anyone writes code against it.
