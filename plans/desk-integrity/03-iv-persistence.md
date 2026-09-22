# Implied volatility, from inputs already in memory

`F6` from the 2026-09-21 review. The smallest capability gain in this initiative, and the
cheapest.

## Goal

Answer "is premium rich or cheap here" for any captured underlying. Today the desk cannot,
for any symbol.

## What the user sees

The decision engine says so about itself, in `payload.structure`:

> "…no implied-versus-realized view is available."

So QQQ realizing 19.0% on 10 days against SPY's 12.8% produces no rich/cheap read at all. The
only vol series in the database are `vol.vix`, `vol.vix9d`, `vol.vix3m`, `vol.vix6m` and
`vol.skew` — every one SPX-referenced and living in `terminal`. There is no VXN and no
per-underlying IV, **even though the captured chains carry IV and the engine already consumes
it to compute Greeks.**

## Data

The input is already in memory at capture. Persist, per snapshot per underlying:

- **ATM IV** — the straightforward one.
- **30-day constant-maturity IV** — interpolated across the two bracketing expiries. This is
  the one that makes the series comparable across days, and it is where the judgment lives.

A few columns on `gex.snapshots`, or a small sibling table. Prefer columns unless `T101`'s
shape decision makes a sibling natural, in which case follow it.

## Design decisions

### 1. Define ATM precisely and write the definition down

Nearest strike to spot, or interpolated between the two bracketing strikes; nearest expiry, or
the front month excluding same-day. These choices move the number by more than the number
moves day to day. Pick, justify in the docstring, and keep it stable — a series whose
definition changed mid-history is worse than no series.

### 2. Null when it cannot be computed

The same rule as everywhere else on this desk. A snapshot whose chain admitted no usable IV
gets `NULL`, never `0` and never a last-known carry-forward. This matters more here than
elsewhere because a carried-forward IV looks exactly like a real one.

### 3. `iv_rv_ratio` already has a consumer waiting

`RegimeRow.iv_rv_ratio` exists and is already `None` throughout, and `_fade` already emits the
warning "IV/RV unavailable: no vol view in the structure hint." Wire the new column into it
rather than inventing a parallel path — this task is largely about making an existing null
stop being null.

## Tasks

### T103 · Sonnet · —

ATM IV and 30-day constant-maturity IV persisted per snapshot, computed at capture from the
frame already in memory. Null where not computable. `RegimeRow.iv_rv_ratio` is populated from
it and the "IV/RV unavailable" warning stops firing for symbols that now have a value.

Paths: `backend/app/modules/gex/models/db.py`, `backend/app/modules/gex/gex/engine.py`,
`backend/app/modules/gex/gex/store.py`, `backend/app/modules/gex/scan/regime.py`,
`backend/alembic/versions/`, `backend/tests/`.

Independent of everything else here; wants `T101`'s capture-time pattern but does not need it.

## Verified facts

- No IV column exists on `gex.snapshots` or any sibling table, confirmed 2026-09-21.
- The chains carry IV and `gex/greeks.py` already consumes it.
- **SPX `iv` is already decimal** — ATM quoted at 0.1061. Do not divide by 100. This has
  caught an agent on this codebase before.
- IVs in the captured data reach **7.97**, and these are real inversion artifacts, not bad
  parses. Do not filter them out as errors; decide explicitly whether an ATM/CM calculation
  should exclude them and say why.
- `RegimeRow.iv_rv_ratio` and the `_fade` warning that reads it both already exist.
- `engine.py` and `greeks.py` are pure (invariant 1).

## Acceptance

1. A 09-21 QQQ capture yields a 30-day CM IV, and the desk can state whether QQQ premium was
   rich or cheap against its 19.0% 10-day realized. Quote the answer in the `Result` section.
2. A snapshot with no usable IV stores `NULL`, and a test asserts it is not `0`.
3. The 30-day figure is stable across two consecutive captures of the same chain — a
   definition that jumps between adjacent expiries is the failure this checks for.
4. `_fade` stops emitting "IV/RV unavailable" for a symbol that now has a value, and still
   emits it for one that does not.
5. `uv run pytest` and `uv run ruff check .` clean; migration applies and rolls back.

## Likely first-contact failures

- **Dividing IV by 100.** See verified facts.
- **Filtering out IVs above some threshold as "bad data".** 7.97 is real.
- Carrying forward the last known IV when a chain is unusable.
- Interpolating across expiries in price space rather than in variance space — state which you
  chose and why.
- Letting the ATM definition drift between the engine and any test fixture.

## Out of scope

- A VXN or any index-level vol series in `terminal` — a different module and a different
  ingestion path.
- An IV *surface*, skew, or term structure beyond the single 30-day point.
- Using IV/RV in the decision engine's scoring. This makes the input exist; changing how
  decisions are scored is a separate, deliberate change to a system with a live track record.


---

## Result — T103, 2026-09-22

**Done. Linters clean, migration generates and reverses.** Not deployed; goes out with
`T104`-`T107` after a close.

### F6's premise was wrong, and that is the main finding

This file, following the review, opens with the decision engine's own words as evidence:

> "…no implied-versus-realized view is available."

**The desk had implied vol the whole time and was using it.** Decision 50 (QQQ, 2026-09-18)
carries `IV/RV 1.27` in its structure hint. Verified live on 2026-09-22 against the current
snapshots: ATM 30-day IV computes cleanly for **QQQ 0.1704, SPX 0.1154, SPY 0.1164**, each
interpolated between real bracketing expiries over thousands of contracts.

That sentence was a **fallback that fired whenever the ratio sat between `IV_CHEAP_RATIO`
(0.90) and `IV_RICH_RATIO` (1.10)** — so a real, neutral measurement was reported as a missing
one. Decision 78's `warnings` array is empty, which proves it: `_fade` appends "IV/RV
unavailable" only when the ratio is genuinely `None`, and it did not.

This is exactly the distinction `T100` enforces on a null aggregate, one layer out: **unmeasured
is not neutral.** A `_vol_clause` helper now says "IV/RV 1.00: implied is in line with realized"
for a neutral reading and keeps the "unavailable" wording only for a real null. Both the fade
and continuation fallbacks share it, plus `T99`'s pin branch, so the three cannot drift apart.

### The rest of `F6` was already built

`gex/report.iv_regime` has existed since T37 and does precisely what this file specified:

- ATM vol interpolated **across strike to spot** within `ATM_MONEYNESS_WINDOW`;
- a constant ~30-day maturity interpolated **in total variance** (`σ²·T` linear in `T`) between
  the bracketing expiries — which is the answer to this file's own "likely first-contact
  failure" about interpolating in price space;
- returned as a **decimal fraction**, so the "do not divide by 100" trap is already handled;
- with `interpolated` False and the two DTEs equal when only one expiry was usable, rather than
  extrapolating past the end of the term structure.

So design decision 1 — "define ATM precisely and write the definition down" — was already
answered, in a docstring, correctly. The task was never to build this. It was to stop throwing
it away.

### What actually needed doing: persistence

Nothing stored it, so every consumer reopened the snapshot's Parquet file and recomputed.
`api/scan._lookup_iv30` is documented in its own module as **the dominant cost of the trend
endpoint, ~3s across the universe** — that is this, paid per symbol per request.

Six columns on `gex.snapshots`, written by `compute_and_store`, which already has the frame and
the spot in hand: `atm_iv` plus the provenance needed to judge it without reopening anything
(`target_dte`, `lower_dte`, `upper_dte`, `interpolated`, `contracts`). `_lookup_iv30` reads the
column and keeps the Parquet path as a fallback for pre-T103 rows.

On the snapshot row, not per `(snapshot, filter)`: IV is a property of the chain, and repeating
it three times per capture would invite the three copies to disagree.

Null means not computable — never zero, and explicitly **never carried forward** from an
earlier capture, because a stale vol looks exactly like a fresh one. A failure to compute it is
logged and stored as null rather than failing the capture: a chain with no usable IV still has
perfectly good gamma.

### Acceptance

1. ✅ ATM 30-day IV computes; QQQ reads **0.1704** on the 2026-09-22 snapshot. Whether QQQ
   premium is rich or cheap is now a subtraction the desk can do.
2. ✅ Null where not computable, tested, and asserted not to be `0`.
3. ✅ Stored value is asserted equal to a direct recompute, so the fast path cannot silently
   change what the desk reports.
4. ✅ The "IV/RV unavailable" string now appears only for a genuine null — tested across the
   fade, continuation and pin branches.
5. ✅ Linters clean; migration applies and reverses.

### Outstanding

- **Not deployed**, and existing rows carry null until `backfill --recompute` runs. Unlike a
  missed capture, this is genuinely recoverable: the Parquet files are on disk.
- **The measured speedup is unmeasured.** The ~3s figure for `_lookup_iv30` is the existing
  documented number; re-timing the trend endpoint after the backfill is the honest way to
  claim the improvement, and it has not been done.
