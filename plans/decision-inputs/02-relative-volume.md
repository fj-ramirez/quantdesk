# Relative volume (T92)

The cheapest item on the eval's list that survived verification: the column is populated, and
nothing reads it.

## Goal

Every breakout and trend signal can be qualified by how much volume confirmed it, using data
already stored.

## What the user sees

A relative-volume reading beside each breakout and trend row, and the ability to tell a move
that carried volume from one that did not.

## Data

`gex.daily_bars.volume`: **149,560 non-null of 157,152 rows, across 120 symbols**, current to
2026-09-21. The five symbols with no volume are `^SKEW`, `^VIX3M`, `^VIX6M`, `^VIX9D` and
`^VVIX` — index quotes that correctly have none, not a gap to repair.

No scan module reads the column. `breakouts.py:177` and `trend.py:200` mention `volume` only in
docstrings describing the frame they accept.

The eval's motivating example, verified — IWM against its trailing 60-day average:

| Date | Volume | Rel |
|---|---|---|
| 2026-09-10 | 27,044,700 | 1.29 |
| 2026-09-11 | 26,353,300 | 1.26 |
| 2026-09-14 | 26,060,900 | 1.24 |
| 2026-09-15 | 25,300,500 | 1.22 |
| 2026-09-16 | 27,599,400 | 1.34 |
| 2026-09-17 | 22,159,600 | 1.07 |
| **2026-09-18** | **31,106,300** | **1.51** |
| 2026-09-21 | 18,829,268 | 0.91 |

Seven consecutive sessions above average into Friday's move, then back to normal today. The
eval quoted 1.49 for Friday; 1.51 here. The difference is the averaging convention, which is
the first thing the task has to pin down.

## Design decisions

**Trailing average, excluding the current bar.** The table above uses
`ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING`. Including the day being measured damps exactly the
spike the indicator exists to detect, and on a 60-day window a 3× day pulls its own denominator
up by 3 %. Excluding it also means the reading is computable intraday against a fixed
denominator. Whichever is chosen, the docstring states it — the 1.49-versus-1.51 discrepancy
above is entirely this.

**Lives in `scan/indicators.py`, as a pure function over the bars frame.** That module is
already the home of `atr`, `adx`, `efficiency_ratio`, `choppiness`, `realized_vol` and
`variance_ratio`, all with the same shape: a DataFrame in, a Series out, a named period
constant. A sixth belongs there and nowhere else.

**Null-safe by construction, because five symbols have no volume at all.** The function returns
`NaN` for a symbol with no volume rather than zero — invariant 3's reasoning applied one layer
up: "no volume reported" and "traded zero" are different facts, and a relative-volume filter
that reads the index symbols as 0× would silently exclude every one of them from every signal.

**A reading, not a filter, in this task.** Compute and expose it; do not yet change which
breakouts qualify. Changing signal membership is a change to what the desk is told to trade and
deserves its own before/after, not a ride-along on an indicator addition.

## Tasks

## T92 · Sonnet · —

Add `relative_volume(bars, period=REL_VOLUME_PERIOD)` to `app/modules/gex/scan/indicators.py`:
current bar's volume over the mean of the previous `period` bars, excluding the current one,
`NaN` where volume is absent or the window is short. Default period 60, as a named constant
beside the others.

Surface it on the breakout and trend rows that already carry the bars frame, and in whichever
API schema exposes them.

Tests: the IWM series above reproduces the numbers in the table; a symbol with all-null volume
yields all-`NaN` and never `0`; a window shorter than `period` yields `NaN` rather than a
partial average.

## Verified facts

Measured 2026-09-21:

* 149,560 / 157,152 rows carry volume, 120 of 125 symbols.
* The five without are all `^`-prefixed index quotes.
* No scan module currently reads the column.
* IWM's seven-session run is real, and Friday was 1.51 on a trailing-exclusive 60-day mean.

## Acceptance

* Suite green with the three tests above.
* The IWM numbers in this file reproduce exactly, or the docstring explains the convention that
  makes them differ.
* Index symbols appear with `·` rather than `0` wherever the reading is surfaced.

## Likely first-contact failures

* **Including the current bar** and then not understanding why Friday reads 1.47 instead of 1.51.
* **`volume.fillna(0)` somewhere in the chain**, which turns the five index symbols into
  permanent zero-volume names and quietly drops them from any filter built on this later.
* **Computing across symbols without grouping.** The bars table is long-format; a rolling mean
  over an ungrouped frame averages across symbol boundaries and produces numbers that look
  plausible and are nonsense.

## Out of scope

Using the reading to filter or rank signals, intraday relative volume from `gex.intraday_bars`
(6 symbols, two days of history), and dollar-volume or turnover variants.

---

## Result — T92

**Done 2026-09-21.** 1,127 backend tests green (9 added), 403 frontend tests green, both linters
clean.

`relative_volume(bars, period=REL_VOLUME_PERIOD)` in `app/modules/gex/scan/indicators.py`, the
sixth pure indicator, following the established shape exactly. The trailing window excludes the
current bar via `closed="left"`, and there is a test pinning that specifically — an inclusive
window reads 2.54 where the exclusive one reads 3.0 on the same fixture, so a later
"simplification" fails loudly rather than shifting every number by a few percent.

Surfaced on both rows that already carry the bars frame: `TrendComponents.rel_volume` (the
latest bar) and `BreakoutEvent.rel_volume` (**the event bar**, not the last bar — that is the
bar the question is about). Both fields default to `None`, which is what kept the change
additive across ~10 existing construction sites in the tests; `test_score_symbol_populates_
rel_volume` is what stops that default quietly becoming the value the API serves. Mirrored on
`TrendComponentsOut` and `BreakoutEventOut`, and in the frontend's `types.ts`.

Null-safety held up under test: an all-null volume series yields all-`NaN` and never `0.0`, and
a zero trailing baseline yields `NaN` rather than `inf`.

Per the design decision, this is a **reading only** — `rel_volume` does not enter the trend
composite and is not percentile-ranked, so no symbol's score moved.

**Two things a reader should know:**

* **No UI column was added.** The value reaches the client and is typed, but the trend and
  breakout tables do not render it yet, so the user sees nothing until a column is added. That
  was not in this task's scope and is a small follow-on.
* **The IWM acceptance test reproduces the ratios against a constructed baseline**, not against
  the live 60-bar history — the suite is offline by design. Six of the seven sessions match the
  eval's figures to 0.01; Friday reads 1.51 against the eval's 1.49, which is the inclusive-
  versus-exclusive window difference this file predicted.

**Found in passing, not fixed, and worth its own task:** `variance_ratio` raises
`ZeroDivisionError` on a perfectly flat close series (`indicators.py:549`, `var_q / var_1` with
`var_1 == 0`). That breaks `score_symbol`'s documented "Never raises on `bars` alone" contract,
and every other indicator in the module guards its own `0/0` case by returning `NaN`. Reachable
for any symbol that does not move across the whole 126-bar window.
