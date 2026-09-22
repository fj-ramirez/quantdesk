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
