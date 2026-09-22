# A wall is named by its gamma, never by its position

`F1`, `F1b`, `F2` from the 2026-09-21 review. **The P0 of this initiative.**

Read [docs/state-review-2026-09-21-verification.md](../../docs/state-review-2026-09-21-verification.md)
§F1 before starting. The original review's stated cause and stated fix are both wrong, and its
fix would reintroduce a bug the code handles on purpose.

## Goal

A wall's *name* comes from the sign and magnitude of its net gamma. Its *side* comes from its
position relative to spot. These are two independent facts and the code currently derives both
from the second one.

## What the user sees

Today, decision id 78 (QQQ, 09-21) says, verbatim:

> "Dealers are long gamma (25% of gross): hedging sells strength and buys weakness, so a move
> into **the put wall at 740.00** meets demand."

740 is the **call wall**, and it carries **+662mn** — the most positive strike in the book. The
trade is defensible; the sentence is not. After this task the same row names the level
correctly and explains the regime it is actually in.

## Data

No schema change. Everything needed is already in memory.

| fact | where it already lives |
|---|---|
| the wall's net gamma | `WallInfo.net_gex` (`scan/regime.py:205`) |
| the wall's magnitude | `WallInfo.abs_gex` — already read by `_fade` |
| the book's largest magnitude | `KeyLevels.max_abs_gex` |
| which side of spot | whether the object arrived as `wall_below` or `wall_above` |

## Design decisions

### 1. Keep the positional selection. Replace only the naming.

`scan/regime.py:413` `_nearest_walls` picks by position **deliberately** — its docstring cites
DIA's 2026-09-05 capture, where both of the most negative strikes sat above spot. That is
correct and stays. The defect is that it returns bare `(strike, gex)` tuples whose identity is
then re-invented from position at `scan/decisions.py:553` and `:699`.

**Do not** make `_fade` read `gex_levels.put_wall` and assume it is below spot. That is what
the review proposed and it breaks the DIA case.

### 2. Enumerate the four combinations. Do not collapse them into one assertion.

This is the judgment call of the task, and the reasoning must be in the code, not just here.
The review proposed a single guard — refuse to emit unless the sign matches the key — which is
right for one of these four rows and wrong for five.

| position | net gamma | what it is | dealer hedging into it | emit |
|---|---|---|---|---|
| above spot | **> 0** | call wall above spot — the classic cap | sells strength: resists | **`FADE_CALL_WALL`**, side `SHORT` |
| below spot | **< 0** | put wall below spot — the classic floor | buys weakness: resists | **`FADE_PUT_WALL`**, side `LONG` |
| below spot | **> 0** | positive-gamma strike at/below spot — a pin | buys weakness: holds price | **`GAMMA_PIN`**, side `LONG` |
| above spot | **< 0** | put wall spot has broken **below** | chases: **amplifies** | **suppress** |

Rows three and four are why a sign assertion is the wrong shape. In row three the stated
mechanism *holds* — a +662mn strike below spot really does attract dealer buying on a dip, so
the trade is sound and only the name was wrong. In row four the mechanism is **inverted**: the
thesis claims "hedging sells strength" at the most short-gamma strike in the book, where
hedging does the opposite. That is the one genuine trade error in the six, and it is the one
the original review did not find.

Row four is suppressed, not renamed: the long-gamma fade rationale is the entire basis of the
setup and it does not hold there. Emitting a short-gamma continuation instead is the
continuation engine's job and is out of scope.

### 2b. Row three is `GAMMA_PIN`, and it is a scored setup

**Decided by the user, 2026-09-21: its own key, scored separately.**

The key is **`GAMMA_PIN`**, side `LONG`. Fix the name now rather than delegating it: a scored
key is a permanent identifier in the track record, and renaming it later fragments the history
it exists to accumulate.

**Why "pin" and not "the cap has become a floor".** The review framed row three as spot
breaking above the call wall and the ceiling becoming support. The distances say otherwise —
in all five rows spot is resting *on* the strike, not travelling away from it:

| id | sym | spot | strike | gap | net gamma there |
|---|---|---|---|---|---|
| 78 | QQQ | 741.0 | 740 | **0.14%** | +662mn |
| 77 | SPY | 773.2 | 772 | **0.16%** | +1.39bn |
| 32 | XLE | 65.12 | 65 | **0.18%** | +51mn |
| 50 | QQQ | 722.0 | 720 | **0.28%** | +715mn |
| 80 | DIA | 519.9 | 516 | **0.75%** | +41mn |

Spot sitting on the single most positive-gamma strike in a strongly long-gamma book (QQQ was
+4.80bn overall on 09-21) is a **magnet**, not a boundary: dealer hedging sells every move up
and buys every move down, holding price near the strike. The thesis text must say that, and
must not be the fade sentence with a different noun in it.

**Scope it narrowly: positive-gamma strike *below* spot only.** Do not reclassify the
above-spot case.

This is deliberate and slightly unsatisfying, so here is the reasoning. What really separates a
pin from a wall is **distance**, not side — a +662mn strike 0.1 ATR away is a magnet whether it
sits just above or just below spot, and the same strike 2 ATR away is a ceiling either way. A
principled classifier would therefore key on proximity and would reclassify some existing
`FADE_CALL_WALL` rows as pins. **That would rewrite the meaning of a key that already has a
live track record** (n=7, +1.162R), which is exactly the contamination this initiative exists
to remove. So: fix the broken case, leave the working one alone, and file the symmetry question
as open.

**A pin needs a proximity gate.** A positive-gamma strike below spot at 3 ATR is not a pin, it
is a level price has left behind. Decide whether the existing `FADE_REACH_ATR` /
`WATCH_REACH_ATR` gates are tight enough or whether `GAMMA_PIN` warrants its own, and measure
the choice against the five rows above — all of which are inside 0.8%.

**Wire it into scoring, not just emission.** A new key that is emitted but not scored is half
the decision. Check every place that enumerates keys — `scan/outcomes.py`, `api/decisions.py`,
the factor cap from `T93` — and make sure `GAMMA_PIN` appears in the track record rather than
silently falling out of it. **`T106`'s track-record MCP tool must derive its keys from the
data, never from a hardcoded list of four**; that task runs in parallel with this one and the
two will collide if it does not.

### 3. The magnitude floor is relative, and the threshold is a judgment call

`engine.py:1353` takes `argmin(net)` unconditionally. Over an all-but-positive array it returns
the least-positive strike and calls it a put wall. Observed, live:

| captured_at | book max | reported `put_wall_gex` |
|---|---|---|
| 09-11 19:44 | +1.79e9 | **−1.87** |
| 09-11 19:59 | +2.00e8 | **−5.48e−37** |
| 09-21 19:59 | +4.35e6 | **−2.17e−20** |

A sign guard passes all three. The floor must be **relative to `max_abs_gex`**, not absolute —
an absolute dollar threshold would be wrong for SLV and QQQ at the same time.

Working suggestion: `WALL_MIN_ABS_FRACTION = 0.01`, and a wall below it resolves to `None`
rather than to a bad strike. **Check your chosen value against the three rows above and against
a normal SPX/QQQ `ALL` capture before committing to it** — a floor that nulls a real wall is a
worse bug than the one being fixed. Put the measured justification in the constant's docstring,
in the style of `STALE_THRESHOLD_MINUTES` in `scan/regime.py`.

This belongs in `engine.key_levels`, not in `decisions.py`: it is a statement about what the
chain contains, and putting it in the engine means `gex.gex_levels` stops *storing* the bad
walls too. `engine.py` stays pure — this adds arithmetic, no I/O.

### 4. Recompute historic levels — but lock the fixture first

**Decided by the user, 2026-09-21: backfill.** Historic `gex.gex_levels` is recomputed after
the fix, so the −2.17e−20 put walls stop being served to every future reader.

**The ordering matters and it is a trap.** The `F1` fixture identifies mislabeled decisions by
joining `gex.decisions` to `gex.gex_levels` on `entry = l.call_wall`. Recomputing the levels
changes the right-hand side of that join, so a fixture that re-derives its expectations from
live levels will quietly stop finding the six rows. **Store the six expected `(id, key)` pairs
as literals in the test**, verify against them, and only then run the backfill.

`gex.decisions` is **not** recomputed — it is append-only and the track record depends on that.
The six wrong rows stay wrong in history; only the levels they point at get corrected.

### 5. `F2` is not a separate fix

The review filed the thesis prose as its own finding. It is not. `decisions.py:610`
interpolates the same `wall_word` assigned at `:553`, and so do `entry_label`, `stop_label`,
`target_label`, `beyond_word` and three of the four invalidation clauses. Fix the assignment
and every surface follows. Do not patch the prose separately — that is how the two drift apart
again.

## Tasks

### T99 · Opus · —

A wall is named from its net gamma, never from its position relative to spot.

`engine.key_levels` gains a relative magnitude floor below which a wall is `None`
(design decision 3). `scan/decisions.py` derives `wall_word` and the opportunity `key` from
`wall.net_gex` instead of from `side`, enumerating the four position/sign combinations
(decision 2): two keep their existing keys, one becomes the new scored `GAMMA_PIN` key with its
own thesis text and proximity gate (decision 2b), one is suppressed. A guard refuses to emit any
fade whose wall sign contradicts its key, so the class cannot silently return. `GAMMA_PIN` is
wired into the track record, not just into emission.

Paths: `backend/app/modules/gex/gex/engine.py`,
`backend/app/modules/gex/scan/decisions.py`, `backend/app/modules/gex/scan/regime.py`,
`backend/app/modules/gex/scan/outcomes.py`, `backend/app/modules/gex/scan/factors.py`,
`backend/app/modules/gex/api/decisions.py`,
`backend/tests/test_scan_decisions.py`, `backend/tests/test_gex_engine.py`.

## Verified facts

Measured 2026-09-21 against live Postgres. Do not re-derive these.

- **The six mislabeled decisions** — a ready-made regression fixture:

  | id | sym | date | emitted key | entry | spot | net gamma at entry | correct handling |
  |---|---|---|---|---|---|---|---|
  | 23 | SLV | 09-10 | `FADE_CALL_WALL` | 58 | 57.44 | −7.5mn | **suppress** |
  | 32 | XLE | 09-11 | `FADE_PUT_WALL` | 65 | 65.12 | +51.5mn | new key |
  | 50 | QQQ | 09-18 | `FADE_PUT_WALL` | 720 | 722.0 | +715mn | new key |
  | 77 | SPY | 09-21 | `FADE_PUT_WALL` | 772 | 773.2 | +1.39bn | new key |
  | 78 | QQQ | 09-21 | `FADE_PUT_WALL` | 740 | 741.0 | +662mn | new key |
  | 80 | DIA | 09-21 | `FADE_PUT_WALL` | 516 | 519.9 | +41.4mn | new key |

- **Perfect separation.** Across all 19 `FADE_PUT_WALL` rows ever emitted, every mislabel has
  `spot > call_wall` and every correct one has `spot < call_wall`. There is no third case in
  the data and no ambiguity to resolve.
- `WallInfo.net_gex` already exists and is already populated. `_fade` already reads
  `wall.abs_gex` two lines below the mislabel. **Nothing needs plumbing.**
- `_nearest_walls`' positional logic is correct and documented. The DIA 2026-09-05 capture it
  cites is real.
- The track record is contaminated by this bug: excluding the two mislabeled resolved rows,
  `FADE_CALL_WALL` goes +1.162R (n=7) → +1.394R (n=6) and `FADE_PUT_WALL` +0.133R (n=4) →
  +0.510R (n=3).

## Acceptance

Run these, do not assert them:

1. **The fixture.** A test reconstructing each of the six rows above from its stored snapshot
   produces the correct key — `GAMMA_PIN` for the five, no opportunity at all for SLV id 23.
   This is the headline check and it must use the real stored levels, not a hand-built chain.
2. **No regression on the thirteen correct rows.** The other `FADE_PUT_WALL` and
   `FADE_CALL_WALL` decisions in the table still emit the same key at the same entry.
3. **The straddle case still works.** A synthetic chain with both walls above spot — the DIA
   2026-09-05 shape — still selects walls by position and does not crash or return `None` for
   both. This is the test the review's proposed fix would have failed.
4. **The magnitude floor.** The three `ZERO_DTE` rows in decision 3 return `put_wall = None`.
   A normal `ALL` capture for QQQ and SPX returns both walls unchanged.
5. **Prose follows the name.** Regenerating decision 78's thesis names 740 as a call wall and
   describes the **pin** — price held near the strike by dealer hedging — not a fade. Grep the
   module for any remaining string that derives a wall's noun from `side`.
6. **`GAMMA_PIN` reaches the track record.** Emit one, resolve it in a test, and confirm it
   appears as its own row in the per-key breakdown rather than vanishing or being folded into a
   fade bucket. A key that is emitted but unscored fails this task.
7. **Historic `gex.gex_levels` recomputed** via `gex/backfill.py` once 1–5 are green, so the
   junk walls in `F1b` stop being served from storage. See decision 4 for the ordering trap.
8. `uv run pytest` and `uv run ruff check .` clean.

## Likely first-contact failures

- **Assuming `put_wall` is the one below spot.** The single most likely wrong turn, because it
  is what the source review recommends. `_nearest_walls`' docstring is the counter-argument.
- **Fixing `decisions.py` and leaving `engine.py` alone.** Then `gex.gex_levels` keeps storing
  −2.17e−20 put walls and the next consumer hits `F1b` fresh.
- **Putting the magnitude floor in `decisions.py`** because that is where the symptom was
  reported. It belongs in the engine; see decision 3.
- **Choosing an absolute dollar floor.** It cannot be right for SLV and SPX simultaneously.
- **Rewriting the six historic decision rows.** The table is append-only and the track record
  depends on that. Fix the emitter; leave the history.
- **Suppressing all six** by taking the review's guard literally. Five of them are sound trades
  with a wrong noun.
- **`GAMMA_PIN` emitted but never scored.** Anything enumerating keys — `scan/outcomes.py`,
  `api/decisions.py`, the `T93` factor cap, the frontend decision views — is a place the new key
  can silently fall out of the track record. Acceptance 6 exists for this.
- **Reclassifying near-spot `FADE_CALL_WALL` rows as pins** because the logic obviously
  generalises. It does, and doing it here rewrites a key with live history. Decision 2b.
- **No proximity gate**, so `GAMMA_PIN` fires on a positive-gamma strike 3 ATR below spot.

## Out of scope

- Whether the broken-above regime deserves its own sizing, scoring bucket or track record.
  `T99` gives it an honest name and an honest thesis. The product question is the user's.
- Any change to the continuation engine, including a short-gamma continuation to replace the
  suppressed row four.


---

## Result — T99, 2026-09-21

**Done. 1,207 backend tests green (19 added), 408 frontend (1 added), both linters clean.**
The backfill (acceptance 7) is the one item outstanding; see below.

### What was verified live

Decision 78 regenerated from its captured geometry. Before:

> "Dealers are long gamma (25% of gross): hedging sells strength and buys weakness, so a move
> into **the put wall at 740.00** meets demand."

After, as `GAMMA_PIN` / `pin` / `LONG`, grade A:

> "Spot is sitting on the largest positive-gamma strike in the book (740.00, $662m net).
> Dealers are long gamma, so hedging sells every move up and buys every move down: the strike
> acts as a magnet, not a boundary."
>
> "The **call wall** carries $662m of gamma and sits 0.14 ATR below spot…"
>
> "The gamma flip is 4.00 ATR below spot: **the pin holds** inside a long-gamma regime."

### Judgment calls made, and what moved them

- **`WALL_MIN_ABS_FRACTION = 1e-4`, not the 1e-2 this file proposed.** Measured first, as the
  spec demanded, and the data moved the number. Across the 688 stored `ALL`/`EX_ZERO_DTE` rows
  the weakest wall is **3.88 %** of its book maximum, so any floor below that is safe there.
  But across 171 `ZERO_DTE` rows a 1 % floor would have nulled 19 rows whose weak side runs up
  to **$44.8mn** -- a real level opposite a large 0DTE call side. The natural break is an order
  of magnitude lower: 12 rows sit below 1e-4 with a weak side of at most **$3,866**, and the
  next band up starts at $24,523. 1e-4 rejects residue and keeps small-but-real walls.
  Relevance is left to the ATR reach gates downstream, where it belongs.
- **`PIN_REACH_ATR = 1.0`.** The five historical pins sat 0.10, 0.11, 0.17, 0.25 and 0.72 ATR
  from spot. 1.0 covers every observed case with margin and excludes the 1.0-3.0 ATR band
  `WATCH_REACH_ATR` would otherwise have emitted, where a positive-gamma strike below spot is
  a level price has left behind rather than a magnet holding it.
- **A pin scores in the fade *family* while carrying its own `setup` label.** `_score`
  compares `setup` against the regime *verdict*, and the verdict a pin occurs under is
  `fade` -- a long-gamma, range-bound book is the precondition for both. Passing `"pin"` there
  scored the pin as contradicting the very regime that makes it work, and printed that
  contradiction in the breakdown. Caught by the acceptance test, not by review.
- **The engine fix is symmetric.** `argmax(net)` over an all-negative book had the same latent
  defect as `argmin` over an all-positive one, so `call_wall` gained the mirrored sign and
  magnitude test. No stored row exhibits it yet; it would have been the next bug.

### Cheaper than expected

Nothing downstream enumerates decision keys. `scan/outcomes.py` never mentions them,
`scan/factors.py` treats them as opaque strings, and the frontend derives its label
generically (`setupLabel('GAMMA_PIN')` -> "Gamma pin"). The only edit needed outside the two
modules was widening `OpportunitySetup` in `types.ts` to admit `'pin'`. Tests were added at
both ends to keep it that way -- a setup that needs a frontend edit before it renders is a
setup that vanishes from the desk on the day it starts emitting.

### Deployed and verified, 2026-09-22 00:0x

**Acceptance 7 is done.** `backfill --recompute` (added by `T101`) ran against the homeserver
in the 00:00 window, after the fixture was locked as literals exactly as decision 4 requires.
Confirmed live through the read API:

- QQQ `ZERO_DTE` now has **zero** level rows with `|put_wall_gex| < 1000`. Before the recompute
  that set included walls carrying **-1.87**, **-5.48e-37** and **-2.17e-20** dollars.
- **13** QQQ `ZERO_DTE` rows now report `put_wall = None` -- the honest answer where `argmin`
  used to name the least-positive strike.
- No full-chain wall was lost, as the 1e-4 floor was measured to guarantee.

Unchanged and deliberately so: the six wrong rows in `gex.decisions`. The table is append-only
and the track record depends on that. `FADE_PUT_WALL` (n=4) and `FADE_CALL_WALL` (n=7) remain
contaminated by two mislabeled losses until enough correctly-named rows accumulate to make the
old ones a small minority -- `T107` should carry that caveat wherever per-key fade history is
quoted.
