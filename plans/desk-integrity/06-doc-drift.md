# Three documented facts the data contradicts

`F7` from the 2026-09-21 review. Cheap, and each item is an active source of a wrong agent
read — an agent that trusts the docs over the data gets the wrong answer confidently.

## Goal

No document on this desk asserts a fact the database contradicts.

## What the user sees

### a. `cmdty.gold` has data, and the docs say it never has

`universe.py` marks it `_pending`; `CLAUDE.md` and the `market-research` skill both state the
desk has no gold price. The skill's wording is emphatic:

> **`cmdty.gold` has never had data.** … **The desk has no gold price**; GLD options are the
> only gold read, and `ust.10y.real → cmdty.gold` is uncomputable.

It holds **1,262 observations** through 09-21, the board returns **398.4** (`derived_lag`, GLD
proxy), and `ust.10y.real → cmdty.gold` computes at β −0.120, t −3.47, significant.

**Describe it accurately as an ETF proxy, not a bullion fix.** That distinction is the reason
the original caveat existed and deleting the caveat outright would lose it.

### b. `ust_cc.*` is fresher than `ust.*`, inverting the documented lag

The skill states the constant-maturity series lag the main ones and "the board flags them
stale." On 09-21 `ust_cc.10y` is 09-21 while `ust.10y.nominal` is 09-18. If the board's
stale-flagging is keyed on the documented assumption, **it is flagging the fresh series and
passing the stale one** — check whether it is, because that is a code bug hiding behind a
documentation bug.

### c. `vol.vix` is stale — and so is `vol.skew`

| series | last value_date | last as_of |
|---|---|---|
| `vol.vix` | 2026-09-18 | 2026-09-18 |
| `vol.skew` | 2026-09-18 | 2026-09-18 |
| `vol.vix9d` | 2026-09-21 | 2026-09-21 |
| `vol.vix3m` | 2026-09-21 | 2026-09-21 |

The review names only `vol.vix` and suggests checking "that one series' ingest path". It is
two, and they stall together, which points at a **shared source** rather than one broken
series. The derived ratios correctly inherit the 09-18 vintage, so the board shows a 09-21
VIX3M beside a 09-18 ratio — internally consistent, confusing, and it means the headline VIX
is three days old.

### d. The track record — fixed by `T106`, not here

The skill hardcodes "As of 2026-09-20 (32 resolved) … Overall +0.27R." Actual on 09-21: 40
resolved, +0.187R, SE ±0.180. **Do not update the numbers.** Updating them re-arms the same
trap for whoever reads the file next month. Delete the block and point at `T106`'s
track-record tool, which is why this task depends on it.

## Data

Read-only investigation plus documentation edits. One possible code fix in (b).

## Design decisions

### 1. Replace hardcoded results with instructions to query

The `F7d` lesson generalises: numbers baked into a prompt file rot silently and are then
quoted with full confidence. Anywhere the skill states a *result*, replace it with the call
that produces the result. Anywhere it states a *rule* — the noise ceiling, the point-in-time
rule, null-is-not-zero — leave it exactly as it is. Rules do not rot; measurements do.

### 2. Keep the caveats, correct the claims

`cmdty.gold`'s caveat exists because a GLD-derived proxy is not a bullion fix. The claim "it
has no data" is false; the caveat behind it is still true. Rewrite, do not delete.

### 3. (b) may be a code bug — find out before writing prose

If the board's stale-flagging hardcodes the assumption that `ust_cc.*` lags, fixing the
sentence leaves the wrong flag in place. Check `modules/terminal/` first; if it is a code
bug, fix it here and say so, or split it out if it turns out to be large.

## Tasks

### T107 · Sonnet · T106

Three documented facts corrected against the data. `cmdty.gold` described as a populated ETF
proxy with its caveat intact, in `universe.py`, `CLAUDE.md` and the `market-research` skill.
The `ust_cc.*` lag claim corrected, and the board's stale-flagging checked against it — fixed
if it hardcodes the old assumption. The `vol.vix` / `vol.skew` shared-source stall
investigated and either fixed or written down as a known gap with what was ruled out. The
skill's hardcoded track-record block deleted and replaced with a pointer to `T106`'s tool.

Paths: `.claude/skills/market-research/SKILL.md`, `CLAUDE.md`,
`backend/app/modules/terminal/` (read-only unless (b) or (c) turns out to be a code bug),
`docs/`.

## Verified facts

Measured 2026-09-21 against live Postgres.

- `cmdty.gold`: 1,262 observations, last value_date 09-21, last as_of 09-21, basis
  `derived_lag`, value 398.4, GLD-derived.
- `ust_cc.10y` last value_date 09-21; `ust.10y.nominal` last value_date 09-18.
- `vol.vix` and `vol.skew` both last print 09-18. `vol.vix9d`, `vol.vix3m`, `vol.vix6m` all
  print 09-21.
- Track record on 09-21: 40 resolved (`result_r IS NOT NULL`), +0.187R, SE ±0.180. Per key:
  `FADE_CALL_WALL` +1.162R (n=7), `FADE_PUT_WALL` +0.133R (n=4), `CONTINUATION_DOWN` +0.094R
  (n=26), `CONTINUATION_UP` −1.212R (n=3).
- **Those per-key fade numbers are contaminated by `F1`** — see
  [00-wall-identity.md](00-wall-identity.md). If `T99` has landed, the skill should not quote
  per-key fade history at all without noting that rows before the fix were scored under
  possibly-wrong keys.
- The skill's other known blind spots — SPX/SPY gamma-sign disagreement, the
  `XA_FRED_API_KEY` recreate-not-restart trap — were **not** re-checked in this pass. Leave
  them unless you verify them.

## Acceptance

1. Every factual claim edited is re-verified against the database at edit time, and the query
   goes in the commit message or the `Result` section.
2. No number that can rot is left hardcoded in `SKILL.md`. Grep it for digits and justify each
   survivor.
3. For (b): state explicitly whether the board's stale-flagging was wrong. "Checked, it keys
   off the data" is a valid and useful answer.
4. For (c): either the ingest path is fixed and `vol.vix` prints 09-21, or the gap is
   documented with what was ruled out. A silent leave-it is not acceptable.
5. `cmdty.gold`'s entry says "populated" and "ETF proxy, not a bullion fix" in some form.
6. `uv run pytest` and `uv run ruff check .` clean.

## Likely first-contact failures

- **Updating the track-record numbers instead of deleting them.** Decision 1. This is the
  most likely wrong turn and it re-arms the trap.
- **Deleting the `cmdty.gold` caveat** along with the false claim.
- Fixing the prose in (b) while leaving a code path that hardcodes the same wrong assumption.
- Treating (c) as one series.
- Re-verifying nothing and trusting this file's numbers — they were true on 2026-09-21 and
  this task may run later. Re-run them.
- Quoting per-key fade history post-`T99` without the contamination caveat.

## Out of scope

- The skill's unverified blind spots (SPX/SPY disagreement, FRED key handling). Not re-checked
  here; do not edit what you have not measured.
- Restructuring the skill. Correct what is false; leave the shape.
- A general "regenerate the docs from the data" job. Tempting and much larger; the
  track-record tool already removes the worst instance.


---

## Result — T107, 2026-09-22

**Done.** Every claim re-verified against the live database at edit time, as acceptance 1
requires; the queries are in this section rather than only in a commit message.

### a. `cmdty.gold` — the docs were wrong, and so was the review

Verified: **1,262 observations spanning 2021-09-10 to 2026-09-21.** Not a recent arrival — five
years of history.

The review said `universe.py` marks it `_pending`. **It does not**, and had already stopped
doing so before this task: it is defined as `_prices("cmdty.gold", "GLD", "Gold (GLD proxy)",
…)`, landed with the prices adapter. `CLAUDE.md` turned out not to claim anything about gold
data either. The only place the false claim survived was the `market-research` skill, which is
the one that matters — it is what an agent reads before answering.

Rewritten rather than deleted, per decision 2. The caveat that motivated the original entry is
still true and is now the point of it: **say "GLD proxy", not "gold"** — an ETF's price history
carries expense-ratio drift and US-session hours, and is not a bullion fix.

### b. `ust_cc.*` — documentation error only, and the code was checked

Verified: **every** `ust_cc.*` prints 2026-09-21 while **every** `ust.*` prints 2026-09-18. The
documented lag is inverted for the whole family, not just the 10y the review sampled.

Decision 3 asked whether this was a code bug hiding behind a documentation bug. **It is not.**
Staleness is computed per series from `stale_days` against `stale_warn_days`, with no
per-series assumption anywhere in `modules/terminal/`. The board flags whatever is actually
stale. The ingest is healthy too — today's batch wrote `ust.10y.nominal`; its upstream simply
publishes later. Recorded in the skill so nobody has to check again.

### c. `vol.vix` / `vol.skew` — fixed, and the mechanism is better than either guess

The review said "one series' ingest path". The verification document said two series sharing a
source. **Both were wrong.** All five vol series come from the same `_cboe` definition and the
same adapter, and three of the five were fresh — so a shared-source failure cannot explain it.

What the evidence actually showed:

1. Upstream has 09-21 for **all five**, with the expected columns. Source and column mapping
   are fine.
2. `observations.source_batch` says batch `20260922T000921` wrote `vix9d`/`vix3m`/`vix6m` at
   value_date 09-21 and inserted **nothing** for VIX or SKEW. Same adapter, same run.
3. A manual `ingest --source cboe` hours later inserted **exactly one row each** for VIX and
   SKEW, and zero for the other three.

So the upstream history files for VIX and SKEW appear to update *later* than the term-structure
ones, and an ingest running soon after midnight ET catches some and misses those two. The gap
is closed — `vol.vix` and `vol.skew` now print 09-21 — and the pattern is written into the
skill with the instruction to re-run the ingest before drawing a vol-regime conclusion from a
VIX that looks a day behind.

The publication-timing explanation is **inferred, not proven**: confirming it needs observation
across several days. Stated as inference, which is the discipline this initiative exists to
enforce.

### d. The hardcoded track record — deleted, not updated

As decision 1 requires. Updating the numbers would have re-armed the same trap. The section now
calls `gex_track_record` and carries the two readings the tool encodes — resolved means
`result_r IS NOT NULL`, and `untriggered` is not a loss — plus the contamination caveat for
fade history spanning 2026-09-22, which `T99` created and this is the right place to record.

Acceptance 2 is met: **`SKILL.md` contains no number that can rot.** The one date-stamped figure
left is inside the explanation of what the old block got wrong, which is a historical fact
rather than a live claim.

### Beyond the brief: the skill no longer teaches SQL

Every `sql` code fence is gone. Freshness points at `desk_status`, levels at `gex_levels`, the
track record at `gex_track_record`. This was the deeper half of `F7d`: the skill taught
hand-written SQL *because* the domain tools were once too awkward for the common case, and
reaching for `query_sql` skips the caveats those tools carry. `T105`/`T106` removed the reason;
this removes the habit.

The levels section also gained what `T101` and `T102` made possible — say what fraction of a
wall survives the week, and group by `session_date` rather than `captured_at`.

### Found while working, not fixed here

- **The 03:00 nightly terminal ingest did not run on 2026-09-22.** Batches exist at 00:09-00:34
  (the boot-time run after the deploy) and nothing at 03:00; the host appears to have been down
  between roughly 00:40 and 08:38. Nothing reported it. This is the `F4` pattern in the
  terminal module, and `T104` watches GEX captures only — worth its own task.
- **Workers still cannot write their version stamps.** `terminal-ingest` logs
  `PermissionError: /data/run` at every boot, which is why `GET /health` lists only the
  backend. `T98` handles it gracefully — a warning, not a crash — and names the one-command
  fix: `sudo install -d -o 10001 -g 10001 /data/run/versions`. It has not been run.
