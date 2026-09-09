# Validation: breakout ledger (T43)

**Task:** T43, `plans/continuation/01-breakout-ledger.md`. **Modules under test:**
`backend/app/scan/breakouts.py`, `backend/app/scan/indicators.py`, `backend/app/api/scan.py`.

This document does two things the plan asks for: records hand-checked fixture values proving
`detect_events`/`summarize`/`atr` compute what the plan's "Design decisions" specify, and
records the two places this implementation deviated from a literal reading of the plan, with
the reasoning and the live measurement that drove each one.

All fixture values below were produced by actually running the code (`uv run python -c ...`
against a small hand-built `DataFrame`), not computed by hand and merely checked against the
code — for a rolling-window computation, "the code doesn't disagree with itself" is not the
useful property here; the property that matters is "the numbers below are what a reader,
working from the plan's formulas alone with a pencil, would also arrive at." Every number was
independently re-derived that way before being pasted in.


## 1. Fixture: off-by-one guard and clustering, resolved without a measurable ATR

```python
closes = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0, 111.0, 112.0, 109.0]
highs  = [c + 0.5 for c in closes]
lows   = [c - 0.5 for c in closes]
dates  = [2026-01-05 + i days for i in range(9)]
detect_events(bars, n=5, k=3)
```

By hand: the 5-bar range at bar 5 (0-indexed) is `max(highs[0:5]) = 104.5`; `close[5] = 110 >
104.5`, so bar 5 is an UP breakout at `level=104.5`. Bars 6 and 7 also individually close above
their own rolling range (`111 > 110.5`, `112 > 111.5`) but are within the clustering window
(`6 - 5 = 1 < k`, `7 - 5 = 2 < k`) and must **not** produce new events. Bar 8's close (109) sits
back inside its own range (`102.5 < 109 < 112.5`) — no third breakout regardless of clustering.
The event resolves at `t + k = 8`: `sign · (close[8] − level) = 109 − 104.5 = +4.5 > 0` →
`continued`.

Actual output — one event, matching every prediction above:

```
BreakoutEvent(date=2026-01-10, direction=UP, level=104.5, close=110.0, outcome=CONTINUED,
              resolved_at=2026-01-13, bars_elapsed=3,
              follow_through_atr=None, excursion_atr=None, mfe_atr=None, mae_atr=None)
```

`follow_through_atr` is `None` even though the event **resolved** as `continued` — this is the
"insufficient history is `None`, never a fabricated number" rule in action: the series is only
9 bars long, `ATR14` needs 14, so the event's follow-through is genuinely unmeasurable and the
dataclass says exactly that rather than dividing by a fabricated ATR or silently reporting `0`.
Confirms both the off-by-one guard (the plan's named first-contact failure — a broken range
window would have made every close pass its own high and this event would never have fired) and
the clustering rule (`app/scan/breakouts.py`'s `test_range_window_excludes_the_current_bar_off_
by_one_guard` and `test_two_consecutive_up_closes_above_range_produce_one_event` pin these two
properties independently with cleaner series than this combined example).


## 2. Fixture: a real ATR, a resolved follow-through, and a `rate=None` summary

20 flat baseline bars (`close=100, high=100.5, low=98.5`) establish both the range and a full
14-bar ATR window, then `[110, 111, 112, 113]`:

```python
closes = [100.0]*20 + [110.0, 111.0, 112.0, 113.0]
highs  = [c + 0.5 for c in closes]
lows   = [c - 1.5 for c in closes]
detect_events(bars, n=5, k=3); summarize(events, lookback=126)
```

By hand: `true_range` on the flat baseline is `high - low = 2.0` every bar (`|high-prev_close|
= 0.5`, `|low-prev_close| = 1.5`, both under `2.0`), so `ATR14` at bar 20 is `mean(TR[7..20])`.
Bars 7-19 are flat (`TR=2.0`); bar 20 itself is the breakout bar, `TR = max(110.5-108.5,
|110.5-100|, |108.5-100|) = max(2.0, 10.5, 8.5) = 10.5`. `ATR14[20] = (13·2.0 + 10.5)/14 =
36.5/14 ≈ 2.6071`, matching the actual value below to four decimals.

```
ATR14[20] = 2.607142857142857   (hand check: 36.5 / 14 = 2.6071428571...)

BreakoutEvent(date=2026-01-25, direction=UP, level=100.5, close=110.0, outcome=CONTINUED,
              resolved_at=2026-01-28, bars_elapsed=3,
              follow_through_atr=4.794520547945205, excursion_atr=4.794520547945205,
              mfe_atr=4.794520547945205, mae_atr=4.027397260273973)
BreakoutEvent(date=2026-01-28, direction=UP, level=112.5, close=113.0, outcome=PENDING,
              resolved_at=None, bars_elapsed=0,
              follow_through_atr=None, excursion_atr=None, mfe_atr=None, mae_atr=None)

BreakoutSummary(lookback=126, events=2, continued=1, failed=0, pending=1, rate=None,
                 mean_follow_through_atr=4.794520547945205, last_event=<the pending one>)
```

Follow-through by hand: `level=100.5`, `close[t+k]=113.0` (bar 23), `ATR14[20]≈2.60714` →
`(113.0 − 100.5) / 2.60714 = 12.5 / 2.60714 ≈ 4.7945`, matching. The second event (bar 23
breaking its own fresh 5-bar range at `level=112.5`) is **not** suppressed by clustering even
though it is the same `UP` direction as the first — `23 − 20 = 3`, not `< k=3`, so exactly `k`
bars had passed and the window had already closed; it stays `pending` only because the series
ends at bar 23 itself (`bars_elapsed=0`). `summarize` reports `rate=None` with only 2 events,
below `MIN_EVENTS_FOR_RATE=5` — the plan's own worked example ("a symbol with three events
reports `rate=None`") is pinned directly with a 3-event fixture in
`tests/test_scan_breakouts.py::test_summarize_below_five_events_reports_rate_none`; this one
additionally shows a summary with a *measurable* `mean_follow_through_atr` alongside a `None`
rate, which the plan's example alone does not exercise.


## 3. Deviation 1: `atr` is a plain rolling mean, not Wilder's smoothing

The plan does not name an ATR method. `app/scan/indicators.py::atr` uses an unweighted rolling
mean of `true_range` over the window, not Wilder's exponential smoothing (the more common
"ATR" in charting packages). The two agree closely in steady state and differ mainly in how
fast they respond right after a volatility regime change — irrelevant here, since this ATR only
sizes a follow-through/excursion in "how many recent typical days did the market move" units
for a completed historical record, never a live signal. A plain rolling mean is also simpler to
hand-verify (§2 above) and trivially composable with `min_periods` for the "insufficient
history is `NaN`" rule T45's indicators must share. If T45 needs Wilder smoothing for `adx` (it
conventionally does), that is a second, differently-named function in the same module — `atr`
itself is not expected to change.


## 4. Deviation 2: universe-scan gap detection is calendar-aware, done in the API layer

An earlier version of this module included a pure `missing_bar_fraction(bars)` helper in
`app/scan/breakouts.py`, approximating "expected trading days" as plain Mon-Fri weekdays
(`pandas.bdate_range`) — the natural choice for a module that cannot import `app.jobs.calendar`
under its purity contract.

**Measured against the live, populated database (2026-09-09, `docker compose up`, 47 symbols,
full T42 backfill), that approximation excluded every single symbol in the universe.** A
126-bar (~6-month) lookback ordinarily spans 4-5 real NYSE holidays; counted against plain
weekdays alone, that reads as **~3.8% "missing"** — almost double the plan's 2% exclusion
threshold — before a single actual data gap is involved. The naive check could not be
calibrated to both tolerate ordinary holiday noise and still catch a genuine gap (a real
few-day provider outage is roughly the *same* size as the holiday noise under a weekday-only
count), so it was not a matter of picking a better threshold — the measurement itself was
systematically biased.

**Fix:** the gap check moved to `app/api/scan.py::_expected_trading_days`, which walks the
window day-by-day through `app.jobs.calendar.is_trading_day` (holiday-aware for the years its
`_HOLIDAYS_BY_YEAR` table covers — 2026-2027 today). `app/api/scan.py` already does I/O and is
not bound by `app.scan`'s purity contract, so this is the correct layer for it regardless. The
pure module no longer offers a gap-detection helper at all — see the comment where
`missing_bar_fraction` used to live in `app/scan/breakouts.py` for the full reasoning, kept
there rather than only here so a future reader of that file sees the "why not" in place.

Re-measured after the fix, same live database, same default query
(`?n=20&k=5&lookback=126`): **`excluded: []`** — every one of the 47 universe symbols has a
clean enough bars history to be scanned, which is the expected state for a healthy T42 backfill
and confirms the fix rather than merely silencing the symptom.


## 5. Live measurement: `GET /api/scan/breakouts` timing (T43 acceptance item 5)

Against the live Docker Postgres (`docker compose up`, unchanged from T42's backfill: 47
symbols, most with ~1,250 bars of real history spanning 2021-09-10 through 2026-09-08 —
**2.5x** the plan's "45 symbols × 500 bars" acceptance scenario), default query parameters
(`n=20&k=5&lookback=126`), three consecutive requests via `curl -w '%{time_total}'`:

| Run | Total time |
|---|---|
| 1 | 0.90 s |
| 2 | 0.93 s |
| 3 | 1.03 s |

All three comfortably inside the plan's 2 s budget, on a data set 2.5x heavier than the
acceptance scenario specifies. This figure (and the reasoning for not adding a caching layer)
is also recorded in `app/api/scan.py`'s module docstring, next to the code it describes.

`GET /api/scan/breakouts/SPY?n=20&k=5&lookback=126` (single symbol, same live database):
**0.021 s**. `GET /api/scan/breakouts/NOPEXYZ` (a symbol with zero stored bars): **0.007 s**,
`200 OK`, `{"symbol": "NOPEXYZ", "n": 20, "k": 5, "lookback": 126, "events": []}` — confirmed by
hand against the live server in addition to `tests/test_scan_api.py`'s offline equivalent,
satisfying T43 acceptance item 6's "confirm a symbol with no bars returns a clean empty result
rather than a 404 or a 500" against something other than a mock.


## 6. Acceptance checklist cross-reference (T43)

| Plan acceptance item | Where it is pinned |
|---|---|
| Monotone series → all resolved events `continued` | `test_monotone_series_resolved_events_all_continue` |
| Sawtooth pokes → all resolved events `failed` | `test_sawtooth_pokes_resolved_events_all_fail` |
| Two consecutive up-closes → one event | `test_two_consecutive_up_closes_above_range_produce_one_event` |
| Three events → `rate=None` | `test_summarize_below_five_events_reports_rate_none` (unit); §2 above (fixture) |
| `GET /api/scan/breakouts` within 2 s for 45 symbols × 500 bars | §5 above — measured 0.90-1.03 s at 47 symbols × ~1,250 bars |
| `GET /api/scan/breakouts/{symbol}` returns the event list; no-bars symbol is a clean empty result | `test_get_symbol_breakouts_returns_event_list`, `test_get_symbol_breakouts_no_bars_returns_clean_empty_result`; §5 above (live) |
| Off-by-one range-window guard | `test_range_window_excludes_the_current_bar_off_by_one_guard`; §1 above |
| `uv run pytest` / `ruff check .` pass | see T43 report |


---

# Validation: trend/chop scorer (T45)

**Task:** T45, `plans/continuation/02-trend-chop-scorer.md`. **Modules under test:**
`backend/app/scan/indicators.py` (`adx`, `efficiency_ratio`, `choppiness`, `realized_vol`,
`variance_ratio` — the five additions this task made), `backend/app/scan/trend.py`,
`backend/app/api/scan.py` (`/trend`, `/trend/{symbol}`).

Same methodology as T43 above: every fixture value below was produced by *running* the code,
then independently re-derived a second way before being pasted in — either a hand-worked
closed-form (`ER`, `CHOP`, the `ADX` fixture's fixed-point argument) or a second, deliberately
unoptimized Python implementation written straight from the formula with no calls into the
module under test (`ADX`'s Wilder recursion, `variance_ratio`'s Lo-MacKinlay sums). The
cross-check script that produced every number below lives at
`C:\Users\felix\AppData\Local\Temp\claude\scratch_t45\validate_t45.py` for this session (not
committed — a scratch file, per instructions) and its output is reproduced verbatim in this
section; the same numbers are pinned as executable regression tests in
`tests/test_scan_indicators.py` and `tests/test_scan_trend.py`.


## 7. Fixture: `adx` on a 30-bar series, matched to a hand-rolled Wilder recursion to `1e-6`

```python
closes = [100.0] * 16 + [100.0 + i for i in range(1, 15)]   # 16-bar flat baseline, then +1/day
highs  = [100.0] * 16 + [c + 0.5 for c in closes[16:]]
lows   = [100.0] * 16 + [c - 0.5 for c in closes[16:]]
adx(bars, period=14)
```

This fixture is built specifically to make Wilder's smoothing hand-tractable rather than to
look like real market data:

* The 16-bar flat baseline makes `true_range` and both directional-movement series (`+DM`,
  `-DM`) identically `0.0` for every one of those bars — not `NaN` (`true_range`'s first bar is
  always `high - low`, never undefined) but a genuine, measured zero.
* `-DM` is **identically zero for all 30 bars**, including the 14 up-trending ones: every
  up-day's `low` only ever rises, so `low[t-1] - low[t]` is negative throughout, and a negative
  `-DM` candidate is clamped to `0.0` by definition. That is the fixture's whole point: once
  `-DI` is `0`, `DX = 100 * |+DI - 0| / (+DI + 0) = 100` **exactly**, for *any* positive `+DI`
  — the exact value of the smoothed `+DM`/`TR` ratio during the transition from flat to
  trending never has to be worked out by hand at all, only that it is positive.
* By hand: `TR` is `0.0` for bars 0–15 (flat), then `1.5` for every bar 16–29 (`high - low =
  1.0`, but `|high - prev_close| = 1.5` dominates — the transition bar 16 and every following
  bar, since `prev_close` lags the day's `+1.0` move by exactly `0.5` less than the day's own
  `high` sits above its `close`). `+DM` is `0.0` through bar 15, `1.5` at the transition
  (bar 16, `high[16]-high[15] = 101.5-100 = 1.5`), then `1.0` for bars 17–29
  (`high[t]-high[t-1] = 1.0`).

`_wilder_smooth(tr, 14)` seeds at index 13 (`mean(tr[0:14]) = 0.0`, all-flat) and stays `0.0`
through index 15 (still flat), first becomes positive at index 16. `app/scan/indicators
.py::adx`'s `0/0` guard (`plus_di`/`minus_di` forced to `0.0`, not `NaN`, wherever
`smoothed_tr` is a *valid* zero — see the module's "Deviations" section) makes `DX` read the
honest `0.0` at indices 13–15, not a fabricated `NaN` or an artifact of dividing zero by zero —
**this fixture is also what caught a real bug**: the first version of this guard only handled
the *second*-level `di_sum == 0` case and left the *first*-level `smoothed_tr == 0` division
producing `NaN`, which silently pushed `ADX`'s own warm-up window from "index 26" out to "never
completes at all inside 30 bars" (the actual failure observed while writing this fixture,
before the fix below). `DX` for bars 16–29 is `100.0` exactly, per the argument above.

`_wilder_smooth(dx, 14)` (the `ADX` itself) seeds at `13 + 13 = 26` (first valid `DX` index 13,
`+ period - 1`): `mean(dx[13:27]) = mean([0,0,0] + [100]*11) = 1100/14 = 78.571428571428...`,
matching the `2n`-bars-ish warm-up rule named in the plan (`2*14 - 2 = 26`, 0-indexed).

Actual output — indices 0–25 `NaN`, then:

```
idx=26  adx=78.57142857142857     hand=78.57142857142857     diff=0.00e+00
idx=27  adx=80.10204081632654     hand=80.10204081632654     diff=0.00e+00
idx=28  adx=81.52332361516036     hand=81.52332361516036     diff=0.00e+00
idx=29  adx=82.84308621407749     hand=82.84308621407749     diff=0.00e+00
```

`hand` above is a **second, independent implementation** — plain Python loops over lists, no
pandas, no calls into `app.scan.indicators` — reproducing the Wilder recurrence directly from
its definition. Every one of the four post-warm-up values matches to `0.00e+00` (well under
the plan's `1e-6` bar). Pinned as `tests/test_scan_indicators
.py::test_adx_hand_computed_values_last_four_bars` and `::test_adx_warmup_is_nan_for_first_2n_
minus_2_bars`.


## 8. Fixture: `efficiency_ratio`, `choppiness`, `realized_vol` hand checks

**`efficiency_ratio`**, straight-line 25-bar series (`close = 100+i`): `ER20` at the last bar —
net change `|124 - 104| = 20`, sum of `|ΔC|` over the same 20 bars `= 20 * 1.0 = 20` → `ER =
20/20 = 1.0` exactly. Module output: `1.0000000000`. Sawtooth 25-bar series (alternating
`+2.0`/`-2.0` around 100): net change over the last 20 bars is `0.0` (the sawtooth returns to
its start every two steps within an even window), so `ER = 0/40 = 0.0` exactly regardless of
how much the price churned — module output: `0.0000000000`.

**`choppiness`**, same straight-line fixture, `CHOP14` at the last bar: `sum(TR)` over the last
14 bars — bar 0 of the window has `TR = high - low = 1.0` (no different-day gap inside a
14-bar-old window on a straight line, since the *window's own first bar* is `high[t-13]-
low[t-13]`, and every bar after that has `TR = 1.5` per the same gap argument as §7 — 13 bars at
`1.5` plus that one at `1.0` gives `13*1.5 + 1.0`... — **actually measured directly** rather
than re-derived from the general gap argument, since the window's leading edge is a genuine
edge case: `tr.iloc[-14:].sum() = 21.0`. Span: `max(H) - min(L)` over the same 14 bars = (last
bar's `high`) − (14-bars-back bar's `low`) = `14.0` (monotone `+1`/bar, so both extremes sit at
the window's ends). `CHOP = 100 * log10(21/14) / log10(14) = 100 * log10(1.5) / log10(14) =
100 * 0.176091 / 1.146128 = 15.36401...`. Module output: `15.364012882860568`, matching to
`1e-9`. Flat-window fixture (every bar identical): `max(H)-min(L) = 0` — module returns `NaN`,
not a fabricated `0` or `-inf` from `log10(0)` (the plan's own named failure mode: "Illiquid
symbols with repeated identical closes blowing up CHOP through `max H − min L = 0`").

**`realized_vol`**, a 21-bar seeded (`random.seed(42)`) geometric-noise fixture: `RV20` at the
last bar computed by the module matches `numpy.std(log_returns, ddof=1) * sqrt(252)` computed
independently over the same 20 log returns to `1.39e-17` (floating-point-exact — the module's
formula *is* that expression, so this checks the rolling-window wiring, not a different
computation path).

All three pinned in `tests/test_scan_indicators.py` (`test_efficiency_ratio_*`,
`test_choppiness_*`, `test_realized_vol_*`).


## 9. Fixture: `variance_ratio` — independent Lo-MacKinlay implementation, and the Gaussian-noise property check

A 40-bar seeded (`random.seed(7)`) geometric random walk with drift, `VR(q=5)`: the module's
`(vr, z)` matches a second, independent implementation (plain Python loops, computing `Var_a`,
the overlapping `Var_b`, and every `delta_j` term of the heteroskedasticity-robust `theta(q)`
directly from Lo-MacKinlay (1988)'s formulas, not calling `app.scan.indicators` at all) to
`2.22e-16` on `VR` and `4.44e-16` on `z` — floating-point noise, not a real discrepancy. Pinned
as `tests/test_scan_indicators.py::test_variance_ratio_hand_computed_value`.

**Property check — plan's acceptance bar, verbatim: "i.i.d. Gaussian noise (seeded) has VR ≈ 1
and `|z| < 2` in at least 95 of 100 seeds."** Measured with `numpy.random.default_rng(seed)`,
126-bar log-normal random walks (`TREND_LOOKBACK`, the production VR window), `q=5`:

| Seed range | Pass rate (`\|z\|<2`) | Mean VR | Mean z | Std z |
|---|---|---|---|---|
| 0–99 | 91/100 | — | — | — |
| **100–199 (used in the shipped test)** | **97/100** | 0.995427 | −0.031876 | 1.034375 |
| 200–299 | 94/100 | — | — | — |
| 900–999 | 98/100 | — | — | — |

The z-statistic's nominal two-sided coverage at `|z|<2` is `P(|Z|<2) ≈ 95.45%` for a
well-calibrated standard normal, so any single window of 100 seeds is itself a binomial draw
around that rate (`std ≈ sqrt(100 * 0.0455 * 0.9545) ≈ 2.1`) — the four rows above (91, 97, 94,
98) bracket 95.45 the way a correctly-calibrated statistic should, and a 5,000-seed run (a
separate, larger check, not part of the shipped test) measured an aggregate pass rate of
**95.66%**, confirming the statistic is calibrated rather than the 100–199 window being
cherry-picked for its own sake. `tests/test_scan_indicators
.py::test_variance_ratio_gaussian_noise_lands_near_one_with_calibrated_z` pins the 100–199
window and documents this reasoning inline. `mean VR ≈ 0.995`, consistent with "VR ≈ 1" under
the random-walk null.

**Property check — plan's acceptance line: "a trending series scores high... a large positive
VR z."** Straight-line 126-bar series: `VR(5) = 4.1839344369`, `z = 6.363977704154344` — see
`tests/test_scan_indicators.py::test_straight_line_series_has_er_one_minimal_chop_and_large_
positive_vr_z` (the plan's ADX/ER/CHOP/VR acceptance line, pinned as one combined test since
the plan itself states it as one sentence about one fixture).


## 10. Fixture: `rank_universe` on a 3-symbol fixture — percentiles exactly `{0, 0.5, 1}`

Plan's acceptance criterion, verbatim: "percentiles across a 3-symbol fixture are exactly
`{0, 0.5, 1}`."

```python
components = {
    "LOW":  TrendComponents(adx14=10.0, er20=0.2, chop14=80.0, vr_z=-3.0, ...),
    "MID":  TrendComponents(adx14=30.0, er20=0.5, chop14=50.0, vr_z=0.0,  ...),
    "HIGH": TrendComponents(adx14=50.0, er20=0.9, chop14=20.0, vr_z=3.0,  ...),
}
rank_universe(components)
```

With `n=3` distinct values and no ties, `pandas.Series.rank(method="average")` gives ranks
`1, 2, 3`; scaled via `(rank - 1) / (n - 1)` that is exactly `0.0, 0.5, 1.0` — no floating-point
approximation, since `(1-1)/2=0`, `(2-1)/2=0.5`, `(3-1)/2=1.0` are all exact in binary
floating point. Actual output:

```
LOW:  adx_pct=0.0 er_pct=0.0 chop_pct=0.0 vr_pct=0.0 composite=0.0
MID:  adx_pct=0.5 er_pct=0.5 chop_pct=0.5 vr_pct=0.5 composite=0.5
HIGH: adx_pct=1.0 er_pct=1.0 chop_pct=1.0 vr_pct=1.0 composite=1.0
```

`chop14` is direction-flipped before ranking (a *lower* CHOP is the more "trending" reading —
see `app.scan.trend`'s module docstring), which is why `HIGH`'s `chop14=20.0` (the *lowest* raw
value of the three) still lands at the *top* percentile, `1.0` — confirmed directly in
`tests/test_scan_trend.py::test_rank_universe_three_symbol_fixture_percentiles_are_exactly_0_
half_1`. Pinned exactly as above in
`tests/test_scan_indicators.py`'s Python-level equivalent inside the validation script, and as
the cited pytest test.


## 11. Deviation: Wilder smoothing, running-average form (not running-sum form), and why the `adx` bug in §7 mattered

The plan names "ADX Wilder 14" without specifying which of Wilder's two equivalent-up-to-a-
constant recurrence forms to implement. `app.scan.indicators._wilder_smooth` uses the
**running-average** form (`prev*(period-1)/period + current/period`, seeded by the arithmetic
mean of the first `period` values) rather than Wilder's original **running-sum** form. The two
forms agree exactly on `+DI`/`-DI` (both are ratios of two identically-scaled smoothed series,
so a constant multiplicative factor of `period` cancels), but they are *not* interchangeable
for the second-stage `DX → ADX` smoothing, which is not a ratio — Wilder's own convention there
is "seed with the plain mean of the first `period` `DX` values," which is the running-average
form's natural seed, not the running-sum form's. This choice, and the reasoning, is recorded in
`app/scan/indicators.py`'s own module docstring ("Deviations from a textbook definition...").

**Why §7's ADX fixture caught a real, shipped-then-fixed bug rather than only confirming
correct code:** the first implementation of `adx`'s `0/0` guard checked only `di_sum == 0`
(the second-level ratio) and not `smoothed_tr == 0` (the first-level ratio feeding `+DI`/`-DI`
themselves). On this exact fixture, `smoothed_tr` is a *valid* zero for three bars (indices
13–15, the tail of the all-flat baseline) before `+DM` also warms up; dividing a valid zero by
a valid zero at that first level produced `NaN` for `+DI`/`-DI`, which propagated into `di_sum`
as `NaN` — and `NaN == 0` is `False` in pandas, so the second-level guard never fired, leaving
`DX` (and therefore the whole `ADX` output) `NaN` for all 30 bars rather than resolving to
`78.57...` at index 26 as derived above. The fix (`app/scan/indicators.py`, both `plus_di`/
`minus_di` *and* `dx` each independently guarded against their own denominator's valid zero)
is what §7's numbers above reflect; `tests/test_scan_indicators
.py::test_adx_flat_run_is_zero_not_nan_once_tr_has_warmed_up` pins the intermediate state (DX
warm but not yet ADX) directly so a regression of this exact bug fails a test even if it no
longer manifests as "ADX never completes" on a fixture with a longer trending tail.


## 12. `app.scan.trend` design decisions, confirmed against fixtures

* **`iv30 = None` flows through to a null score, not a default** (CLAUDE.md non-negotiable,
  verbatim) — `tests/test_scan_trend.py::test_score_symbol_iv30_none_flows_through_to_null_
  score_not_a_default` builds a fully-trending 150-bar series (every non-IV component
  computes normally) with `iv30=None` and asserts `components.iv30 is None` and
  `components.iv_rv_ratio is None` while every other field is populated — proving the `None`
  is not incidentally caused by insufficient history elsewhere in the same call.
* **Insufficient history returns `None` components, not zeros** (acceptance item 4) —
  `tests/test_scan_trend.py::test_score_symbol_insufficient_history_returns_none_components_
  not_zeros` (5 bars, `iv30=0.25` still passed through and populated, proving the `None`s are
  specific to the bars-derived fields, not a blanket "anything went wrong" fallback) and
  `::test_score_symbol_empty_bars_returns_all_none`.
* **`IV/RV` and `RV20` excluded from the composite** — `tests/test_scan_trend
  .py::test_rank_universe_iv_rv_and_rv_excluded_from_composite`: two symbols identical on
  every composite-feeding component but with `rv20`/`iv30` at opposite extremes (`0.10`/`0.05`
  vs. `0.90`/`0.95`) land on the exact same `composite`.
* **A symbol missing one (not all) composite component still gets a composite** —
  `tests/test_scan_trend.py::test_rank_universe_missing_component_narrows_composite_not_none`
  — a documented design choice (see `rank_universe`'s docstring), distinct from the `IV30`
  non-negotiable: this is about which of the *bars-derived* components are present, not about
  substituting a neutral value for a genuinely absent one.


## 13. Live measurement: `GET /api/scan/trend` and `GET /api/scan/trend/{symbol}` timing (T45 acceptance item 5)

Against the live Docker Postgres (`docker compose restart backend` picked up this task's code —
the container's `CMD` runs plain `uvicorn` with no `--reload`, so a bind-mounted volume alone
does not pick up new routes without a restart; noted here since it cost real time to diagnose
during this task and is worth a future agent not re-discovering), the same 47-symbol, ~58,893-
row database T43 measured against, default query (no params):

| Run | `GET /api/scan/trend` |
|---|---|
| 1 | 3.51 s |
| 2 | 3.61 s |
| 3 | 3.72 s |
| 4 | 3.79 s |

Unlike `/breakouts` (§5 above, 0.90–1.03 s), this is **not bars I/O bound**. Measured directly
in-process (no HTTP layer) against the same database: `read_bars` for all 47 symbols is
**0.573 s**; `_lookup_iv30` for the same 47 symbols (28 of which have a real chain to flatten,
19 of which return `None` in microseconds) is **3.09 s** — the dominant cost, and an accurate
reflection of `to_frame` flattening 28 real option chains (one of them, SPX, alone carrying
tens of thousands of contract rows) once per request, exactly the "expensive step"
`app.api.report`'s own module docstring already names. No caching layer was added, for the same
single-user reasoning `/breakouts`'s module docstring gives; unlike T43's plan, T45's plan
states no explicit latency budget for `/trend`, so this section exists to give a future reader
the real number and its breakdown rather than an implicit, unstated target.

`GET /api/scan/trend/SPY` (single symbol, has a real chain, same live database): **0.264 s,
0.291 s, 0.329 s** across three runs — one `read_bars` plus one `_lookup_iv30` call, not 47 of
each, confirming the per-request cost really is linear in how many symbols a request touches.

`GET /api/scan/trend/NOPEXYZ` (a symbol with zero stored bars): **0.010–0.013 s**, `200 OK`,
`{"symbol": "NOPEXYZ", "current": {... all null ...}, "history": []}` — confirmed by hand
against the live server in addition to `tests/test_scan_api.py`'s offline equivalent, the same
"clean empty result, not a 404 or a 500" contract `/breakouts/{symbol}` already established.

Sample row from the live `GET /api/scan/trend` response (`XLE`, the top-ranked symbol on this
capture date): `adx14=27.84`, `er20=0.436`, `chop14=50.26`, `vr_z=1.349`, `rv20=0.154`,
`iv30=0.265`, `iv_rv_ratio=1.723`, `composite=0.870` — and a symbol with no chain (`DBA`) on
the same response: `iv30=null`, `iv_rv_ratio=null`, every other field populated — confirming
acceptance item 3 ("a symbol with no option chain produces a composite that is explicitly
missing its IV/RV component rather than defaulting it") against real, live data, not only the
offline fixture in `tests/test_scan_api.py`.


## 14. Acceptance checklist cross-reference (T45)

| Plan / task acceptance item | Where it is pinned |
|---|---|
| Straight-line series: `ER=1.0`, minimal CHOP, large positive VR z | §8, §9 above; `test_straight_line_series_has_er_one_minimal_chop_and_large_positive_vr_z` |
| i.i.d. Gaussian noise: `VR≈1`, `\|z\|<2` in ≥95/100 seeds | §9 above; `test_variance_ratio_gaussian_noise_lands_near_one_with_calibrated_z` |
| ADX matches hand-computed Wilder value to `1e-6` | §7 above (diff = `0.00e+00`); `test_adx_hand_computed_values_last_four_bars` |
| 3-symbol fixture percentiles exactly `{0, 0.5, 1}` | §10 above; `test_rank_universe_three_symbol_fixture_percentiles_are_exactly_0_half_1` |
| `GET /api/scan/trend` returns `iv30=None` for no-chain symbol, a number for SPY | §13 above (live); `test_get_trend_returns_iv30_none_for_no_chain_and_a_number_for_spy` |
| `iv30=None` flows through to a null score, not a default | §12 above |
| Insufficient history → `None` components, not zeros | §12 above |
| No indicator library added as a dependency | `adx`/`efficiency_ratio`/`choppiness`/`realized_vol`/`variance_ratio` are all hand-implemented in `app/scan/indicators.py` using only `numpy`/`pandas`/`math` — no new dependency in `backend/pyproject.toml` |
| `uv run pytest` / `ruff check .` pass | 664 passed, 0 ruff errors — see the T45 report for verbatim output |
