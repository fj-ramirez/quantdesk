# An empty aggregate is null, not zero

`F3` from the 2026-09-21 review. Confirmed at source; the fix is larger than the review states.

## Goal

`net_gex = 0` must mean "the book is flat". Today it also means "nothing was measurable", and
the two are indistinguishable to every consumer.

## What the user sees

Two snapshots on 09-21 — both the session's first capture, both with full chains — recorded
`net_gex = 0` with every level column null:

| id | sym | captured_at | contract_count | strike rows | net_gex |
|---|---|---|---|---|---|
| 206 | QQQ | 09-21 13:43:03Z | 10,560 | 0 | **0** |
| 204 | SPX | 09-21 13:44:39Z | 29,518 | 0 | **0** |

A reader sees "dealers are gamma-flat", which is a strong and entirely fabricated claim. After
this task the same snapshot reads `·` and the claim cannot be made.

## Data

No migration. `GexLevel.net_gex` is **already** `nullable=True` — the schema was built for
this and only the engine disagrees with it. The `GexLevel` docstring argues the case:

> Storing `0.0` in either case would misreport "no level exists" as "level at strike zero",
> exactly the kind of silent meaning-corruption this schema avoids everywhere else.

## Design decisions

### 1. The domain type is what changes, not the value

`engine.py:1316`:

```python
if strike_gex.empty:
    return KeyLevels(net_gex=0.0, call_gex=0.0, put_gex=0.0, abs_gex=0.0, call_wall=None, ...)
```

The four aggregates are `0.0`; every level is already correctly `None`. But
`KeyLevels.net_gex`, `call_gex`, `put_gex` and `abs_gex` are declared `float`, so this is a
type widening to `float | None` plus a consumer audit — not the value swap the review implies.

Known consumer: `scan/regime.py:499` passes `levels.net_gex` and `levels.abs_gex` straight into
`dealer_positioning(...)`. **Find the rest; do not assume it is the only one.** `ruff` and the
type checker will not catch a consumer that merely does arithmetic on it.

### 2. `dealer_positioning` must return "unknown", not a ratio

With `net_gex` null there is no positioning ratio and no verdict to give. The honest output is
a regime row that says the chain admitted nothing — not a row with `ratio = 0`, which is the
same bug one layer up. Decide how `RegimeRow` represents this and say so in its docstring.

### 3. Do not try to separate "empty bucket" from "nothing usable" — yet

The review asks whether `ZERO_DTE` legitimately empty (no 0DTE contracts exist) should be
distinguishable from a chain that admitted nothing. It should, and this task does **not** do
it: both correctly become null here, which is strictly better than both being `0`. The
distinction needs a "why was this empty" field that `T101`'s capture-time work is a better home
for. Note it in the docstring as a known remaining ambiguity rather than inventing a
representation now.

## Tasks

### T100 · Opus · T99

Empty aggregates resolve to `None`. `KeyLevels.net_gex` / `call_gex` / `put_gex` / `abs_gex`
widen to `float | None`; the `strike_gex.empty` branch returns `None` for all four. Every
consumer is found and given an explicit null path — `dealer_positioning` and the regime verdict
in particular return "unknown" rather than a fabricated zero. `engine.py` stays pure.

Paths: `backend/app/modules/gex/gex/engine.py`,
`backend/app/modules/gex/scan/regime.py`, `backend/app/modules/gex/gex/store.py`,
`backend/tests/test_gex_engine.py`, `backend/tests/test_scan_regime.py`.

**Depends on `T99`** — shares `engine.py` and `regime.py`; must not run in parallel with it.

## Verified facts

- `GexLevel.net_gex` is already `nullable=True`. No migration is needed for the GEX levels
  table.
- `KeyLevels.net_gex` is declared `float` at `engine.py:403`. This is the actual blocker.
- `scan/regime.py:499` is a confirmed consumer. There are likely others.
- The two affected snapshots are ids 204 and 206, both 09-21, both recoverable as fixtures.
- Inferred cause, unconfirmed: at 09:43 ET the provider has not yet published prior-session
  open interest, so every contract has `OI = None` and is correctly excluded per invariant 3.
  The exclusion is right. **Do not "fix" the exclusion.**
- `ZERO_DTE` rows at EOD show the same shape legitimately, so the fixture set must include one
  of each or the test will encode the wrong intent.

## Acceptance

1. Snapshots 204 and 206 recompute to `net_gex = NULL`, not `0`.
2. A `ZERO_DTE` EOD snapshot with no same-day contracts also recomputes to `NULL`, and a test
   asserts both cases land in the same state deliberately (decision 3).
3. A normal full capture is byte-identical to before — this task must not move a single real
   number.
4. The regime row for an empty chain reports unknown positioning and does not emit a verdict
   derived from a zero.
5. `uv run pytest` and `uv run ruff check .` clean.

## Likely first-contact failures

- **Changing the open-interest exclusion.** Invariant 3 is correct and is not what is broken.
- **Widening the type and leaving a consumer doing `abs(net_gex)`** on a `None`. The runtime
  error will surface in the capture worker at 09:43 ET, not in tests, unless the fixtures
  cover it.
- **Making `dealer_positioning` return `0.0` for unknown**, which reproduces the bug one layer
  up and is harder to see.
- **Backfilling historic rows.** Out of scope; the two known rows stay as they are.
- Touching `greeks.py` — nothing here needs it.

## Out of scope

- Recomputing historic `gex_levels` rows.
- Rejecting or flagging zero-contract captures at ingestion. The review raises it; nulls are
  the designed answer and a rejection policy is a separate decision with retention
  consequences.
- The "empty bucket versus nothing usable" distinction — deferred to `T101`, see decision 3.
