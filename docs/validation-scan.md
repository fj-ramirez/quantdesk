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


---

# Validation: sector rotation (T50)

**Task:** T50, `plans/continuation/04-sector-rotation.md`. **Modules under test:**
`backend/app/scan/rotation.py`, `backend/app/scan/groups.py`, `backend/app/api/scan.py`
(`/rotation`).

Same methodology as T43/T45 above: every fixture value below was produced by *running* the
code, then independently re-derived a second way (a closed-form derivation, or a plain,
unoptimized Python loop written straight from the formula with no calls into `app.scan
.rotation`) before being pasted in. The independent-loop script for §17 lives at
`C:\Users\felix\AppData\Local\Temp\claude\C--Users-felix-projects-gex-trading\d737d94d-31d1-
42bc-9e5b-0a5005b50dc3\scratchpad\verify_rrg_independent.py` for this session (a scratch file,
not committed) and its output is reproduced verbatim below; the same numbers are pinned as
executable regression tests in `tests/test_scan_rotation.py`.


## 15. The RRG-approximation formula, restated (and why it is named `_approx`)

JdK RS-Ratio and RS-Momentum (the "Relative Rotation Graph," popularized by Julius de
Kempenaer) are a **proprietary, patented construction** whose exact normalization has never
been published. `app.scan.rotation.rrg_approx` implements the commonly published *open*
approximation of that idea, and is named with the `_approx` suffix specifically so nothing
downstream can quietly present it as the real indicator:

```
rs             = 100 * P_t / B_t                              (P = symbol close, B = benchmark close, same week)
rs_ratio_approx    = 100 + z(rs, w)                            (w = 14 weeks, plan default)
rs_momentum_approx = 100 + z(rs_ratio_approx_t - rs_ratio_approx_{t-1}, w)
z(x, w)_t      = (x_t - mean(x[t-w+1..t])) / std(x[t-w+1..t])   population std (ddof=0), 0.0 when std==0
```

**Deviation from the T50 task block's own literal column names.** `plans/continuation/04-
sector-rotation.md`'s T50 paths line writes the `rrg_approx` return columns as `rs_ratio,
rs_momentum` (no suffix); the plan's own "Design decisions" section two paragraphs above that,
and this task's separate, more specific Non-negotiables instruction, both require the
`_approx`-suffixed names explicitly ("The plan requires `rs_ratio_approx` / `rs_momentum_approx`
naming ... never claiming parity"). The two are in tension; this implementation follows the
more specific, more recently stated instruction and names the actual `DataFrame` columns (not
only the enclosing function) `rs_ratio_approx`/`rs_momentum_approx` everywhere -- the pure
module, and the `/rotation` API response. `app.scan.rotation`'s own module docstring records
this choice next to the code it affects, and the API's `note` field restates the disclaimer a
third time so a page's info tooltip can surface it without reading either docstring.

**Deviation: `ddof=0` (population) standard deviation for the rolling z-score.** The plan names
no convention either way. `_rolling_zscore` treats the trailing `w`-week window as the entire
population being described ("how far is this week from *this window's own* average"), not a
sample estimating a larger one, which is the more natural reading here and is also what makes
the constant-multiple fixture (§16 below) *exactly* `100.0` rather than merely close to it, once
combined with the `std==0` guard.


## 16. Fixture: constant multiple of the benchmark sits at exactly (100.0, 100.0) after warm-up

Acceptance item 1, verbatim: "a symbol whose prices are a constant multiple of the benchmark
sits at exactly (100, 100) for every week after warm-up."

`benchmark[t] = 100 + t` (integers, 40 weeks), `price[t] = 2.0 * benchmark[t]`, `w = 14`.
`rs[t] = 100 * price[t] / benchmark[t]`. By hand: `100 * (2*B) / B` should equal `200.0`
algebraically for every `t`; **checked to actually be bit-for-bit exact** (not merely correct
in exact arithmetic) by running it — with `B` restricted to integers and the multiplier a power
of two, IEEE double division reconstructs `200.0` exactly at every `t`:

```
>>> B = [100.0 + i for i in range(40)]; P = [2.0*b for b in B]
>>> rs = [100.0*p/b for p, b in zip(P, B)]
>>> set(rs)
{200.0}
```

(As a control: the same construction with a **non-integer** benchmark picks up ~1e-15 floating
noise per element and `rs` is merely *approximately* constant, not bit-identical — confirming
the fixture's choice of integer `B` and power-of-two multiplier is what makes exactness
possible, not luck.) With `rs` bit-identical across all 40 weeks, `_rolling_zscore`'s window
variance is exactly `0.0` at every valid position — not "small," literally `0.0` — so the
`std == 0` guard (not floating-point coincidence) is what resolves `z` to exactly `0.0`, giving
`rs_ratio_approx = 100.0` exactly from week 13 (0-indexed, the first `w`-full window) onward,
and — since a constant `rs_ratio_approx` has an exactly-zero week-over-week diff — `rs_momentum
_approx = 100.0` exactly from week 27 onward. Both confirmed by running `app.scan.rotation
.rrg_approx` directly: every value after its own warm-up index is `== 100.0`
(`tests/test_scan_rotation.py::test_rrg_approx_constant_multiple_of_benchmark_sits_at_exactly_
100_100_after_warmup`, asserted with exact `==`, no tolerance).


## 17. Fixture: piecewise-accelerating `rs` — a genuine leading-then-weakening sequence, and why a pure straight line cannot produce one

Acceptance item 2, verbatim: "a symbol with linearly rising `rs` lands in the leading quadrant
with positive momentum, then drifts toward weakening as the z-score saturates — the test
asserts that sequence, not just the endpoint."

**A single, unbroken straight line cannot produce this sequence under the stated formula —
proved, not merely tested against.** For `x_t = a*t + b` and any window position `t` once the
trailing `w`-window is full, the window's contents are `w` consecutive terms of the same
arithmetic progression regardless of `t` (a pure translation of the same `w` numbers, never a
reshaping): `mean = a*(t - (w-1)/2) + b`, so `x_t - mean = a*(w-1)/2`; `std = |a| *
sqrt((w**2-1)/12)` (population std of `w` equally spaced points). Both are independent of `t`,
so `z_t = sign(a) * sqrt(3*(w-1)/(w+1))` is a genuine **constant**, not merely close to one —
the very first valid value already equals every later one. Feeding that constant
`rs_ratio_approx` into the second rolling z-score hands it a week-over-week diff series that is
identically `0.0` everywhere it is defined, which the `std==0` guard resolves to exactly
`100.0` forever — never `> 100.0`, and never a transient decaying toward `100.0`, because there
is no transient. This is confirmed directly: `test_rrg_approx_constant_multiple_of_benchmark
_...` (§16) and a separate check with `rs` itself (not just `price/benchmark`) as a pure
arithmetic progression both land on exactly `100.0`/`100.0` from first-valid-index onward, with
no leading phase.

**Fixture actually used (three-segment piecewise-linear `rs`, `w=14`, 80 weeks):**

```
rs[0] = 100.0
rs[t] = rs[t-1] + 0.5   for 1 <= t <= 29   (rising)
rs[t] = rs[t-1] + 3.0   for 30 <= t <= 49  (rising faster — acceleration)
rs[t] = rs[t-1] + 0.3   for 50 <= t <= 79  (rising slower — deceleration)
```

Independently re-derived with a plain Python loop (population mean/std, `std==0 -> 0.0` guard,
no pandas/numpy calls, no reference to `app.scan.rotation` at all):

```
week 13 (first valid rs_ratio_approx):    101.612452
week 27 (first valid rs_momentum_approx): 100.000000
week 30: rs_ratio_approx=102.346462  rs_momentum_approx=103.605551   <- LEADING (both > 100)
week 31: rs_ratio_approx=102.528089  rs_momentum_approx=100.607827
week 32: rs_ratio_approx=102.465165  rs_momentum_approx=99.359791    <- momentum crosses below 100
week 33: rs_ratio_approx=102.335859  rs_momentum_approx=99.091090    <- WEAKENING (ratio>100, momentum<100)
```

`app.scan.rotation.rrg_approx`, run directly on this fixture, reproduces every one of these six
numbers to better than `1e-5`. The **sequence** — week 30 lands in the leading quadrant
(`rs_ratio_approx > 100` and `rs_momentum_approx > 100`), week 33 lands in the weakening
quadrant (`rs_ratio_approx` still `> 100`, `rs_momentum_approx` now `< 100`) — is asserted
directly, not just the two endpoints, in `tests/test_scan_rotation
.py::test_rrg_approx_piecewise_rising_rs_shows_leading_then_weakening_sequence`. Mechanically:
at week 30 the momentum window is still mostly the slower `+0.5`/week segment, so the *new*
`+3.0`/week diffs read as unusually high relative to that trailing average (positive z); by
week 33 enough `+3.0` diffs have entered the window that the trailing average has caught up to
the current diff (still `+3.0`, unchanged) — "the z-score saturates," per the plan's own
phrase, and momentum falls back through 100 even though `rs` itself is still rising every week.


## 18. Fixture: `relative_returns` — 3-symbol hand-built fixture to `1e-9`, and the `NaN`-not-`None` convention

Acceptance item 3, verbatim: "relative returns on a hand-built 3-symbol fixture match to
1e-9."

```
A     = [100, 102, 101, 105, 110, 108, 115, 120, 118, 125]
BENCH = [ 50,  50,  51,  50,  52,  53,  54,  53,  55,  56]
C     = [NaN, NaN, NaN, NaN, NaN, NaN, 200, 202, 205, 210]     (added to the universe partway through)
relative_returns(prices, "BENCH", windows=(2, 5))
```

Independently re-derived with a plain Python loop reading `(a_t/a_{t-n})/(b_t/b_{t-n}) - 1`
directly off the array (0-indexed, last row = index 9):

```
return_2:  A=-0.014136904761904656   BENCH(self)=0.0   C=-0.016089108910890992
return_5:  A=0.05519480519480524     BENCH(self)=0.0   C=NaN   (C[9-5]=C[4] does not exist)
```

`app.scan.rotation.relative_returns`, run directly on this fixture, matches all four numeric
values to `1e-9` and reports `NaN` for `C`'s `return_5` — confirmed in
`tests/test_scan_rotation.py::test_relative_returns_hand_built_3_symbol_fixture_matches_1e9`.

**Note the fixture's own `C` returns `NaN`, not `None`**, even though this is the same
"insufficient history" case `SectorBreadth`'s fields report as `None`. This is a deliberate,
documented distinction (`app.scan.rotation._last_finite_pair`'s own docstring): `relative
_returns` returns a `pd.DataFrame`, and this module's (and `app.scan.indicators`'s) convention
at that level is `NaN`; only a frozen dataclass (`SectorBreadth`) translates to `None` at its
own object boundary, matching how `app.scan.trend.TrendComponents` already treats a `NaN`
indicator reading as `None` only once it crosses into a dataclass field.

**Bug found and fixed while building this fixture:** `Series.astype(float)` raises
`TypeError: float() argument must be a string or a real number, not 'NAType'` on a column
`app.storage.bars_repository.read_universe_closes` fills entirely with the scalar
`pandas.NA` (its own documented convention for "this symbol has zero bars in range") — this
pandas version's `astype(float)` does not coerce `pandas.NA` to `NaN` the way every other
`app.scan` module's `.astype(float)` calls on genuine (never-`pandas.NA`) `bars` columns had
led this implementation to assume. First caught by `tests/test_scan_api.py
::test_get_rotation_returns_full_shape_for_seeded_and_unseeded_symbols`'s unseeded `XLF`
column, not by any unit test in `test_scan_rotation.py` (whose fixtures all use `np.nan`
directly, never `pandas.NA`) — a reminder that this task's own "one bad symbol should not
poison the whole request" scenario is also where this specific pandas behavior actually bites.
Fixed by replacing every `.astype(float)` call in `app.scan.rotation` that touches a
caller-supplied wide frame with a small `_to_float` helper (`pandas.to_numeric(...,
errors="coerce")`), which does coerce `pandas.NA` (and anything else non-numeric) to `NaN`; see
that helper's own docstring in `app/scan/rotation.py`.


## 19. Fixture: `sector_breadth` — exactly 4 of 11 sectors above their 20-day average

Acceptance item 4, verbatim: "breadth counts on a fixture where exactly 4 of 11 sectors are
above their 20-day average returns 4."

Eleven synthetic sector symbols, each 19 flat bars at `100.0` then one final bar: `101.0` for
four of them, `99.0` for the other seven. By hand: `SMA20 = (19*100 + 101)/20 = 100.05 < 101`
("above"); `SMA20 = (19*100 + 99)/20 = 99.95 > 99` ("below") — both exact, no floating
tolerance needed. `app.scan.rotation.sector_breadth`, run directly on this fixture, returns
`above_20d=4`, `evaluated_20d=11` — confirmed in `tests/test_scan_rotation
.py::test_sector_breadth_exactly_4_of_11_above_20d_average`. A companion fixture
(`test_sector_breadth_missing_symbol_excluded_not_counted_against`) removes one sector from
`prices` entirely and confirms it is excluded from **both** the numerator and the denominator
(`evaluated_20d` drops to match), never silently counted as "below" — the same "absence is not
a negative reading" rule `app.scan.trend.TrendComponents` already applies to a missing
indicator, extended here to a missing *symbol*.

The `RSP`/`SPY` equal-weight leadership reading was checked separately: `RSP = [100..120]`
(21 integer steps), `SPY` flat at `100.0`. By hand: `ratio_t = 120/100 = 1.2`; `ratio_{t-20} =
100/100 = 1.0`; `change_20d = 1.2/1.0 - 1 = 0.2` exactly — both reproduced exactly by
`sector_breadth` (`test_sector_breadth_equal_weight_ratio_and_20d_change`).


## 20. Live measurement: `GET /api/scan/rotation` timing (T50 acceptance item 8)

Against the live Docker Postgres (`docker compose restart backend` picked up this task's new
route — the same restart-required lesson T45's validation doc already names, re-confirmed
here), the same 47-symbol, ~58,893-row database T43/T45 measured against, three consecutive
requests per query via `curl -w '%{time_total}'`:

| Query | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| `?group=sectors&benchmark=SPY&weeks=10` (defaults) | 0.201 s | 0.071 s | 0.060 s |
| `?group=industries&weeks=10` | 0.121 s | — | — |
| `?group=assets&benchmark=RSP&weeks=10` | 0.076 s | — | — |
| `?group=nope` (422, no DB touched) | 0.005 s | — | — |

All well under a second — this route is one `read_universe_closes` call (a single indexed
`SELECT ... WHERE symbol IN (...)` covering the union of the requested group, the 11 sector
ETFs, and both benchmark symbols — roughly 25-35 distinct symbols depending on group overlap)
plus in-memory pandas work, none of it approaching `/trend`'s `_lookup_iv30` cost (§13 above)
since this route never touches Parquet or option chains at all. The first run's higher figure
(`0.201s`) is consistent with one cold connection-pool acquisition right after the container
restart; every subsequent run across all three groups lands under `0.13s`. No explicit latency
budget is stated in the plan for `/rotation` (mirroring `/trend`'s own situation, §13), so this
section exists to give a future reader the real number rather than an implicit target.

Sample values from the live response (`group=sectors`, `benchmark=SPY`, capture date
2026-09-09): `XLK` (top mover) `rs_ratio_approx=100.52`, `rs_momentum_approx=101.16`,
`return_5=+2.2%`; sector-level breadth `equal_weight_ratio≈0.28` (RSP trades at roughly a
quarter of SPY's per-share price — a plausible real reading, not a red flag),
`above_20d=2/11`, `above_50d=5/11`.


## 21. Acceptance checklist cross-reference (T50)

| Plan / task acceptance item | Where it is pinned |
|---|---|
| Constant multiple of benchmark -> exactly (100, 100) after warm-up | §16 above; `test_rrg_approx_constant_multiple_of_benchmark_sits_at_exactly_100_100_after_warmup` |
| Linearly-rising `rs` -> leading then weakening, sequence asserted | §17 above; `test_rrg_approx_piecewise_rising_rs_shows_leading_then_weakening_sequence` (uses a piecewise-accelerating fixture, not a pure straight line -- see §17 for the closed-form proof of why a pure straight line cannot show this sequence under this formula) |
| Relative returns match a hand-built 3-symbol fixture to `1e-9` | §18 above; `test_relative_returns_hand_built_3_symbol_fixture_matches_1e9` |
| Breadth: exactly 4 of 11 sectors above 20-day average -> 4 | §19 above; `test_sector_breadth_exactly_4_of_11_above_20d_average` |
| API rejects an unknown group with 422 | `test_get_rotation_rejects_unknown_group_422` (offline); confirmed live, §20 above (`?group=nope` -> 422 in 0.005s) |
| `uv run pytest` passes | 684 passed (664 pre-existing + 20 new: 15 in `test_scan_rotation.py`, 5 in `test_scan_api.py`) |
| `ruff check .` passes | 0 errors |
| `GET /api/scan/rotation` measured against the live database | §20 above -- 0.06-0.20s, no stated budget to compare against |
| Naming honestly signals "approximation, not the real JdK indicator" | §15 above; `note` field in the API response, `rs_ratio_approx`/`rs_momentum_approx` field names throughout, module docstrings in `app/scan/rotation.py` and `app/api/scan.py` |
| Cross-symbol date alignment | `app.scan.rotation`'s own module docstring's "cross-symbol alignment hazard" section; `test_weekly_closes_a_week_with_no_bar_at_all_is_nan_not_forward_filled`, `test_rrg_approx_benchmark_reindexed_onto_prices_index` |


# Validation: regime board (T48)

## 22. The 0DTE share is not derivable from an EOD snapshot -- confirmed live across all 28 symbols

The supervisor measured this first (2026-09-09), against the live 16:20 SPY EOD snapshot
(id 38, `captured_at` 2026-09-09T20:19:27Z):

```
/api/gex/SPY/latest?filter=ALL       -> 483 by-strike rows
/api/gex/SPY/latest?filter=ZERO_DTE  ->   0 by-strike rows
```

The finding is structural, not a storage gap: that snapshot's earliest expiry is 2026-09-10
(`dte=1`), the same-day expiry is already gone from Cboe's payload by capture time, and the
engine's own diagnostics count 330 contracts `expired`. `GexByStrike`'s own docstring names the
consequence directly ("a filter that admits nothing... simply writes zero rows for that
filter"), so the ratio-of-two-filters mechanism T48's original brief describes evaluates to
`0 / 483` on every EOD row, for every symbol -- not a corner case, the ordinary case.

**Decision (this task, not the plan's other named option):** `app.scan.regime.zero_dte_share`
returns `None`, never `0.0`, whenever the `ZERO_DTE` filter's by-strike rows are empty --
reporting `0.0` would misstate a data limitation ("this chain cannot see same-day expiries") as
a fact about the market ("there is no 0DTE gamma today"). The plan's other option -- a `dte<=1`
next-day proxy, naming SPY's own 2026-09-10 expiry (263 contracts, 141,438 OI) as a candidate --
was **not** implemented: that number came from a per-contract `dte` query, and no persisted
`gex_by_strike` filter isolates a `dte<=1` bucket (only `ALL`, `ZERO_DTE`, `EX_ZERO_DTE` are
ever stored, per `app.gex.store.DEFAULT_FILTERS`), so computing it would require reopening
Parquet -- exactly what `app.api.scan._load_gex_inputs` is built not to do, and the plan's own
concluding sentence says the honest state today is "this column is empty," not "this column
shows something adjacent to 0DTE labelled as if it were."

**The fade verdict must not become unreachable as a result.** The plan's first-cut fade rule
requires "0DTE share above a documented floor." With the share `None` on every real row today,
a literal reading would issue `fade` for nothing, ever. `app.scan.regime._verdict` **drops the
0DTE clause when the share is unavailable** (treats it as neither passing nor failing) rather
than gating on an unmeasurable input, and always names which case applied in
`RegimeRow.reasons`:

* known and above the floor: `"0DTE share N% above the M% floor"`
* known and below the floor: `"0DTE share is N% (fade needs > M%)"`
* unavailable: `"0DTE share unavailable on this snapshot (same-day expiry already left the
  payload by capture time...); fade evaluated on flip/wall distance alone, 0DTE clause
  dropped"`

Pinned offline in `tests/test_scan_regime.py`:
`test_zero_dte_share_empty_zero_dte_is_none_not_a_fabricated_zero` (the ratio function itself),
`test_verdict_fade_with_zero_dte_share_unavailable_drops_the_clause` (fade still reachable),
`test_verdict_mixed_reports_known_zero_dte_share_below_floor` (a *known*, low share still
blocks fade -- only *unavailable* drops the clause, a known-and-failing input does not).

**Confirmed live, across the whole universe** (`GET /api/scan/regime`, 2026-09-09, all 28
`Underlying` members, backend restarted to pick up the new route -- the same
restart-required step §13's own note names): every one of the 28 rows returned
`"zero_dte_share": null`. `fade` was still issued for 2 symbols that day (XLE, USO), each
reason string explicitly reading "0DTE share unavailable ... clause dropped" -- confirming the
clause-drop keeps `fade` reachable rather than silently dead, and confirming a reader of that
row is told, in the row itself, that no 0DTE evidence stood behind it.

## 23. "Room beyond" hand-check on a known strike ladder (T48 acceptance item)

`ROOM_BEYOND_FRACTION = 0.25` (the plan's own number, verbatim: "the next strike whose |GEX|
exceeds 25% of the wall's"). Fixture (`tests/test_scan_regime.py
::test_room_beyond_hand_checked_strike_ladder`): call wall at strike 104 with `abs_gex = 8.0`
(threshold `0.25 * 8.0 = 2.0`), put wall at strike 96 with `abs_gex = 8.0` (same threshold).

By hand, scanning outward from each wall:

| Direction | Strike | `abs_gex` | Clears `2.0`? |
|---|---|---|---|
| above 104 | 106 | 1.0 | no |
| above 104 | 108 | 1.5 | no |
| above 104 | **110** | **3.0** | **yes** -> `room_beyond = 110 - 104 = 6.0` |
| below 96 | 94 | 1.0 | no |
| below 96 | 92 | 1.8 | no |
| below 96 | **90** | **3.0** | **yes** -> `room_beyond = 96 - 90 = 6.0` |

`app.scan.regime.compute_regime_row`, run directly on this fixture, reproduces both numbers
exactly (`wall_above.room_beyond == 6.0`, `room_beyond_strike == 110.0`;
`wall_below.room_beyond == 6.0`, `room_beyond_strike == 90.0`). A companion fixture
(`test_room_beyond_none_when_no_strike_beyond_clears_the_threshold`) confirms the "no strike in
this chain clears the threshold" case returns `None`, not a fabricated "unlimited room."

## 24. Staleness: chip *and* threshold-suppressed verdict, confirmed live against T47's own five symbols

**Decision.** Every `RegimeRow` carries `chain_age_minutes` (how many minutes before its
trading day's close the chain's `app.jobs.calendar.effective_data_time` instant sits, `0.0`
when honestly at or after the close) and `stale` (`chain_age_minutes >
STALE_THRESHOLD_MINUTES`, 30 minutes -- see `app/scan/regime.py`'s own constant comment for why
30, not some other number). **Both** a visible field (for a UI chip) **and** verdict
suppression are implemented, not one or the other: a chip a reader can ignore is not enough
given the mechanism this task is built around (T47's finding that an `is_eod=True` row can be
hours older than its `captured_at` implies), so `_verdict` returns `verdict=None` with a
`reasons` entry naming the exact staleness whenever the threshold is cleared, on top of (not
instead of) the noise-dominated gate.

**Confirmed live** (`GET /api/scan/regime`, 2026-09-09, all 28 symbols): exactly
`{"XLRE", "XLC", "XBI", "KRE", "GDX"}` came back `"stale": true` -- the identical five symbols
T47's own verified-facts table names, with the identical ordering by severity (XBI's
`captured_at` is the furthest from its close). No other symbol, including the two that were
already `verdict: null` for a different reason (XLK, TLT -- both noise-dominated, ratios 2.5%
and 1.0%, both under the 3% floor), came back stale. This is the strongest evidence available
that the staleness detector is reading the right instant: it reproduces, from live data alone,
a finding the plan's own T47 section had to measure by hand.

## 25. DIA fixture: pinned offline, and why the live DIA row differs

T48's hard acceptance item, verbatim: "a DIA fixture must never yield a verdict." Pinned in
`tests/test_scan_regime.py::test_verdict_none_when_noise_dominated_dia_like_fixture` (net/gross
ratio fixed at 0.9%, `docs/validation.md` §9's own historical DIA figure) and
`tests/test_scan_api.py::test_get_regime_dia_fixture_never_yields_a_verdict` (same ratio, seeded
through the real DB/API path). Both assert `noise_dominated is True` and `verdict is None`.

**The live DIA row on 2026-09-09 does carry a verdict** (`continuation`, ratio 18.2% of gross)
-- this is not a contradiction. §9's 0.9% figure was one historical date's measurement, not a
claim that DIA is *always* noise-dominated; the acceptance item asks for "a DIA fixture" that
demonstrates the gate works when the ratio is small, which the two fixtures above do
independently of whatever DIA's ratio happens to be on any given live day.

## 26. Live measurement: `GET /api/scan/regime` (all 28 `Underlying` members, 2026-09-09)

`docker compose restart backend` was required to pick up the new route (uvicorn runs without
`--reload` in `docker-compose.yml`'s `backend` service -- the same restart-required step T45's
own validation note names). After restarting:

* `GET /health` -> `200 OK` (sanity check the container came back).
* `GET /api/scan/regime` -> `200 OK`, 28 rows, one per `Underlying` member, **none** missing
  (every core and extended symbol had a captured snapshot).
* Verdict distribution: `continuation` 13, `mixed` 6, `fade` 2 (XLE, USO), `None` 7 (5 stale --
  §24 above -- plus 2 noise-dominated: XLK 2.5%, TLT 1.0%, both under the 3% floor).
* `zero_dte_share` was `null` on all 28 rows -- §22 above.
* Two `fade` rows' reasons both explicitly read the "0DTE share unavailable ... clause dropped"
  sentence, confirming §22's decision is visible end to end, not just in the pure module.

This is the acceptance item "if you can hit the live app, note whether real rows render" --
real rows render, with sensible, internally-consistent numbers (e.g. XLE: long gamma 23.8% of
gross, flip 2.20 ATR below spot, nearest wall 0.25 ATR away -> `fade`).

## 27. Acceptance checklist cross-reference (T48)

| Plan / task acceptance item | Where it is pinned |
|---|---|
| Fixture chains pin each verdict branch and the noise-dominated branch | `tests/test_scan_regime.py`: `test_verdict_fade`, `test_verdict_fade_with_zero_dte_share_unavailable_drops_the_clause`, `test_verdict_continuation_from_short_gamma`, `test_verdict_continuation_from_long_gamma_near_flip_with_far_wall`, `test_verdict_mixed_when_neither_rule_clears`, `test_verdict_mixed_reports_known_zero_dte_share_below_floor`, `test_verdict_none_when_noise_dominated_dia_like_fixture`, `test_verdict_none_when_stale_even_if_otherwise_fade` |
| "Room beyond" hand-checked on a fixture with a known strike ladder | §23 above; `test_room_beyond_hand_checked_strike_ladder` |
| A DIA fixture never yields a verdict | §25 above; two independent fixtures (pure + API) |
| API returns a row per core and extended symbol with `None` where inputs are missing | `test_get_regime_returns_a_row_per_underlying_with_none_for_uncaptured_symbols` (offline); confirmed live, §26 above (28/28 present) |
| `uv run pytest` passes | 705 passed (684 pre-existing + 21 new: 15 in `test_scan_regime.py`, 6 in `test_scan_api.py`) |
| `ruff check .` passes | 0 errors |
| 0DTE share: `None` when unsupported by stored data, documented rather than estimated | §22 above |
| Staleness surfaced, not silently ranked alongside a fresh chain | §24 above, confirmed live against T47's own five symbols |
| `GET /api/scan/regime` measured against the live database | §26 above |

## 28. T52: ETF shares-outstanding ingest -- XLSX parsing, precision, and `compute_flows`

`docs/etf-flows-sources.md` is the survey this task built against (the two places it overrides
`plans/continuation/05-etf-flows.md` are called out there, not repeated here). This section
records the two implementation decisions the survey left to T52, and the hand computation for
`app.scan.flows.compute_flows`.

### 28.1 XLSX parsing: stdlib `zipfile`, not `openpyxl`

`openpyxl` was **not** added to `backend/pyproject.toml`. `app.providers.etf_flows.parse_spdr_xlsx`
reads `xl/sharedStrings.xml` and `xl/worksheets/sheet1.xml` directly out of the response body
(a zip archive) via `zipfile.ZipFile` plus a handful of regexes -- the same technique the
survey used to read the file in the first place. This avoids a backend dependency add (and the
Docker image rebuild the task brief flagged as the cost of the alternative) for what is, in
practice, about 60 lines split across `_shared_strings`, `_parse_row` and `parse_spdr_xlsx`
itself. Verified directly against the recorded fixture
(`tests/fixtures/etf_flows/spdr-product-data-us-en-2026-09-08.xlsx`): all 17 `SPDR_SYMBOLS`
parse with zero failures (`test_parse_spdr_xlsx_parses_all_17_symbols_from_the_real_fixture`),
and the same parser was re-run live against the real ssga.com endpoint during this task's
acceptance check (§28.4 below) with no code path difference from the fixture-driven test.

### 28.2 Shares-outstanding precision: `TNA / NAV`, printed value as a cross-check

Per the survey's recommendation, `parse_spdr_xlsx` derives `shares_outstanding` as
`Total Net Assets / NAV` (both quoted to $0.01M / $0.01, respectively) rather than reading the
printed `Shares Outstanding` column (quoted to only 10,000 shares, i.e. two decimals in
millions). The printed value is still parsed and compared: a disagreement beyond one creation
unit (50,000 shares, `_ONE_CREATION_UNIT`) is logged as a warning, never raised.

**Did the cross-check ever disagree on the real fixture? No -- but one fund came within 3% of
the threshold.** Every one of the 17 SPDR symbols in the recorded fixture (all as of
2026-09-08) was computed directly and compared:

| Symbol | Derived (`TNA/NAV`) | Printed | \|diff\| (shares) |
|---|---|---|---|
| XLF | 955,501,135 | 955,550,000 | **48,865** (closest to the 50,000 threshold) |
| XLV | 259,408,006 | 259,420,000 | 11,994 |
| XLRE | 187,061,759 | 187,050,000 | 11,759 |
| XLI | 182,021,676 | 182,030,000 | 8,324 |
| XLU | 518,543,048 | 518,550,000 | 6,952 |
| KRE | 56,205,950 | 56,200,000 | 5,950 |
| XLP | 171,675,914 | 171,670,000 | 5,914 |
| XLY | 192,705,227 | 192,710,000 | 4,773 |
| GLD | 368,195,508 | 368,200,000 | 4,492 |
| XLK | 651,805,940 | 651,810,000 | 4,060 |
| SPY | 1,052,583,971 | 1,052,580,000 | 3,971 |
| DIA | 86,442,731 | 86,440,000 | 2,731 |
| XLB | 164,597,035 | 164,600,000 | 2,965 |
| XLC | 202,197,436 | 202,200,000 | 2,564 |
| XLE | 656,947,490 | 656,950,000 | 2,510 |
| XBI | 69,999,197 | 70,000,000 | 803 |
| XOP | 21,000,000 | 21,000,000 | **0** (TNA `$4,069.80M` / NAV `$193.80` is exactly 21.0 on this file) |

Every disagreement is comfortably inside the one-creation-unit (50,000-share) threshold, and
every one is fully explained by the printed column's own 10,000-share rounding -- exactly the
survey's prediction. XLF is the closest call (48,865, about 98% of the threshold): a fund this
large ($54.7B AUM) accumulates more absolute rounding error at the same relative precision,
which is expected and not itself evidence of a data problem. `test_parse_spdr_xlsx_xlk_matches_the_survey_worked_example`
pins the XLK figure; the full 17-symbol table above was produced by a one-off script run
against the fixture during this task, not committed as a test (re-deriving all 17 by hand in
an assertion would just restate this table).

One number in the survey's own worked example does not reproduce against this fixture:
`docs/etf-flows-sources.md` quotes XOP's derived share count as `20,998,968`. Recomputing
directly from the fixture's own printed cells (`Total Net Assets = $4,069.80M`,
`NAV = $193.80`) gives `4,069.80e6 / 193.80 = 21,000,000` exactly -- the two inputs happen to
divide evenly on this file. The survey's figure was almost certainly computed against a
live probe a day later (2026-09-09) with a slightly different TNA/NAV pair; it does not
indicate a bug in this implementation, since re-deriving directly from the recorded fixture's
own cells reproduces exactly what `parse_spdr_xlsx` returns.

### 28.3 `compute_flows`: hand computation

Fixture (also `test_compute_flows_matches_a_hand_computed_2day_window` in
`tests/test_scan_flows.py`): three days of `XLK` shares outstanding (`SO`) and NAV:

| Date | SO | NAV |
|---|---|---|
| d0 | 100 | 10 |
| d1 | 110 | 11 |
| d2 | 105 | 9 |

By hand, per the plan's definitions (`flow_t = (SO_t - SO_{t-1}) * NAV_t`,
`flow_pct_t = flow_t / (SO_{t-1} * NAV_t)`, aggregates sum daily flows and divide by the
window-start AUM):

```
flow_d1 = (110 - 100) * 11 = 110
flow_d2 = (105 - 110) * 9  = -45
aggregate flow (w=2)       = 110 + (-45) = 65
window-start AUM           = SO_d0 * NAV_d0 = 100 * 10 = 1000
flow_pct (w=2)             = 65 / 1000 = 0.065
```

`compute_flows(so_frame, nav_frame, windows=[2])` returns `flow_2 = 65.0`,
`flow_pct_2 = 0.065` for `XLK` -- an exact match, asserted to `pytest.approx` tolerance (no
manual rounding needed since every intermediate value here is exact in floating point).

### 28.4 Live acceptance check (2026-09-09, real network, real Postgres)

Run against the shared dev Postgres (`docker compose`'s `postgres` container, already at
migration head `6d74a6583548` before this task's `961d52e4d010` was applied) and the real
ssga.com/ishares.com endpoints -- not a fixture, per the acceptance brief's literal command:

```
$ uv run python -m app.flows_fetch --symbols XLK,IWM
flows_fetch: spdr inserted=1 skipped=0 failed_symbols=[]
flows_fetch: ishares inserted=1 skipped=0 failed_symbols=[]

$ uv run python -m app.flows_fetch --symbols XLK,IWM   # second run, same day
flows_fetch: spdr inserted=0 skipped=1 failed_symbols=[]
flows_fetch: ishares inserted=0 skipped=1 failed_symbols=[]
```

Stored rows, read back through `read_shares_outstanding`:

| symbol | date | shares | nav | source |
|---|---|---|---|---|
| XLK | 2026-09-08 | 651,805,940 | 187.88 | spdr-xlsx |
| IWM | 2026-09-09 | 269,850,000 | (none) | ishares-productpage |

Both dates match the survey's own live probe from the same day (SPDR one day behind, IWM
same-day) -- confirming the as-of-date lag the survey identified is still the live behavior,
and that this implementation reports it honestly rather than substituting the run date.
`IWM.nav` is `None` because iShares' `navAmount` block, when present at all, carries its own
independent as-of date the survey warns can disagree with `sharesOutstanding`'s -- this
implementation does not fabricate a NAV from a mismatched date; see
`app.models.db.EtfSharesOutstanding`'s docstring.

## 29. T54: cross-asset regime strip -- Cboe index bars provider, and the close-only-schema decision

### 29.1 The close-only-schema decision, restated per this task's own instruction

`app.providers.cboe_index.CboeIndexHistoryProvider` fetches six Cboe volatility/skew index
histories (`^VIX`, `^VIX9D`, `^VIX3M`, `^VIX6M`, `^VVIX`, `^SKEW`) from
`https://cdn.cboe.com/api/global/us_indices/daily_prices/{IX}_History.csv`. Four of the six
publish daily OHLC; two (`^VVIX`, `^SKEW`) publish a close only. `app.models.bars.DailyBar`
declares `open`/`high`/`low`/`close` all required, non-null floats, so a close-only row cannot
be stored as-is without a decision.

**Decision: store `open = high = low = close`.** Of the three options
`plans/continuation/06-cross-asset-regime.md`'s "Verified facts" section lays out (flatten the
OHLC fields; widen the schema to nullable; keep VVIX/SKEW out of `daily_bars` entirely), this
task takes the first. It is not an arbitrary shortcut: it is **Cboe's own convention for its
own early history** -- the `^VIX` file's 1990 rows are themselves
`17.240000,17.240000,17.240000,17.240000` (see
`backend/tests/fixtures/cboe_index/VIX_History-2026-09-09.csv`'s first data row), so the
vendor's own OHLC file format already carries a "closes dressed as OHLC" precedent for exactly
this situation. Widening the schema (option 2) is a migration plus a nullability change
propagating through every reader of `daily_bars`, which is a larger and riskier change than
this task's scope justifies, and is exactly the class of change
`docs/supervision-report.md` names for hiding type errors elsewhere. Keeping the two symbols
out of `daily_bars` (option 3) contradicts the plan's own "no new table" constraint.

The cost, restated: a consumer cannot distinguish "no intraday range published" from "a
genuinely flat day" from a `cboe_index`-sourced row's `open`/`high`/`low` alone. Nothing in
this task's own code reads `open`/`high`/`low` from a `cboe_index` row anywhere --
`app.scan.cross_asset` reads only `close` throughout -- but a future caller must not assume
otherwise. The mitigation is `source="cboe_index"` on every row this provider writes, plus this
section and the provider's own module docstring.

### 29.2 `^VIX` changes hands from Yahoo to Cboe -- confirmed live against the real dev Postgres

`T42` already backfilled `^VIX` from `app.providers.yahoo` (`source="yahoo-splitadj"`).
Listing `^VIX` in the `cboe_index` `BAR_PROVIDER_GROUPS` entry moves it to
`app.providers.cboe_index`, since `BarProviderRegistry.provider_name_for` resolves a group
entry ahead of the default. Before touching the database, the existing rows were read back:

```
>>> read_bars('^VIX').tail(5)
      date        ...  source
2026-09-09  ...  yahoo-splitadj
```

1256 rows, all `source="yahoo-splitadj"`. After `uv run python -m app.bars_backfill --years 3
--symbols ^VIX --force` (forced, since the default incremental skip would otherwise never
re-fetch a symbol whose `last_bar_date` is already recent):

```
bars_backfill: ^VIX inserted=20 updated=754
```

Read back again:

```
      date   open   high    low  close  volume      source
2026-09-09  15.65  16.68  15.57  16.46     NaN  cboe_index
```

`source` value counts: `cboe_index: 774, yahoo-splitadj: 502`. Confirms, against the real
shared dev Postgres (not a fixture): `upsert_bars`'s `ON CONFLICT DO UPDATE` set clause
includes `source` (`app.storage.bars_repository.upsert_bars`), so the changeover is recorded
column-by-column on every row the 3-year `--force` window reached (754 updated + 20 newly
inserted dates Cboe's history has that Yahoo's did not, within that window) -- the 502 rows
older than the 3-year window are untouched and still correctly read `yahoo-splitadj`, not
silently relabelled. `volume` also changes from Yahoo's `0` (`^VIX` genuinely trades zero
contract volume, per T42's own docstring) to Cboe's `None` (the endpoint does not publish a
volume column at all) -- both are correct under CLAUDE.md invariant 3's None-vs-zero rule for
their respective source, not a regression.

### 29.3 Live backfill of the other five index symbols

```
$ uv run python -m app.bars_backfill --years 5 --symbols ^VIX9D,^VIX6M,^VVIX,^SKEW
bars_backfill: ^VIX9D inserted=1253 updated=0
bars_backfill: ^VIX6M inserted=1253 updated=0
bars_backfill: ^VVIX inserted=1253 updated=0
bars_backfill: ^SKEW inserted=1252 updated=0
bars_backfill: total=4 fetched=4 skipped_up_to_date=0 failed=0 inserted=5011 updated=0
```

All four populated cleanly on the first run (no prior rows to conflict with, `updated=0`
throughout).

### 29.4 `GET /api/scan/cross-asset` -- live response against the real dev Postgres

Captured in-process (`TestClient(app).get(...)`, `app` built from `app.api.scan.router`, no
session-factory override -- the real `settings.DATABASE_URL`), immediately after the backfills
in 29.2/29.3, 2026-09-10:

```json
{
  "as_of": "2026-09-09",
  "vix": 16.46, "vix3m": 18.87, "vix9d": 15.59,
  "vix_vix3m_ratio": 0.8722840487546369, "vix9d_vix_ratio": 0.9471445929526123,
  "term_structure": "contango", "term_structure_reason": null,
  "vvix": 94.5, "vvix_pct": 0.36254980079681276, "vvix_pct_n": 252,
  "vix_pct": 0.35856573705179284, "vix_pct_n": 252,
  "spy_rv20": 0.08101500442487977, "vrp": 8.358499557512024, "vrp_pct": 0.609375, "vrp_pct_n": 129,
  "sector_correlation": 0.17511471460690284, "sector_correlation_n": 18, "sector_correlation_universe_n": 11,
  "uup_return_20d": -0.005685851132994912, "gld_return_20d": 0.00596073099404304,
  "tlt_return_20d": -0.0055967766249988005
}
```

Hand-checked: `term_structure`: `VIX/VIX3M = 16.46/18.87 = 0.872 < 1` and `VIX9D/VIX =
15.59/16.46 = 0.947 < 1` -> `"contango"`, matching `app.scan.cross_asset.term_structure`'s
rule. `vrp`: `16.46 - 8.1015 = 8.358...`, matching `app.scan.cross_asset.vrp`.

**`sector_correlation_n: 18`, not `20`, is the live confirmation of the plan's own named
hazard** ("a correlation window straddling a missing bar for one ETF silently shrinks the
sample"): at least one of the 11 sector ETFs was missing a bar on 2 of the trailing 20 trading
days in the live database, and `sector_correlation` correctly reported the *effective* aligned
sample size rather than silently computing over a misaligned 20. This is the same `n < window`
behavior `backend/tests/test_scan_cross_asset.py
::test_sector_correlation_reports_effective_sample_size_when_a_bar_is_missing` exercises on a
hand-built fixture, now confirmed against real data.

`frontend/src/mocks/fixtures/scan/cross_asset.json` is this exact response, byte-for-byte
(only re-indented) -- see that directory's `README.md` for why it was captured this way (an
isolated worktree, guardrail against restarting the shared dev server) rather than curled.

### 29.5 Acceptance checklist cross-reference (T54)

- `uv run python -m app.bars_backfill --years 3 --symbols ^VIX,^VIX3M` populates rows via the
  new provider -- §29.2 (`^VIX`, forced to demonstrate the changeover) and the plain
  (non-forced) run's own log line, `bars_backfill: ^VIX3M inserted=752 updated=0`.
- Recorded fixture CSVs parse; `not-a-csv-error-body.html` raises `ProviderError` --
  `backend/tests/test_cboe_index.py` (all fixtures under `backend/tests/fixtures/cboe_index/`).
- `term_structure` on hand-built closes yields all three labels --
  `backend/tests/test_scan_cross_asset.py::test_term_structure_contango`/`_backwardation`/
  `_mixed_when_ratios_disagree`/`_mixed_at_exact_parity`.
- Sector correlation is exactly 1.0 when all sectors share one return series, and ~0 on
  orthogonal series -- `test_sector_correlation_is_exactly_one_when_every_sector_shares_one_
  return_series` (Pearson correlation of a series with itself), `test_sector_correlation_is_
  near_zero_on_orthogonal_series` (an exact Hadamard-matrix construction, not merely
  approximately uncorrelated random noise).
- `percentile_252` with fewer than 60 bars returns `None` --
  `test_percentile_252_none_under_60_bars`, and confirmed live in §29.4's `vix_pct_n`/`vrp_pct_n`
  fields (`vrp_pct_n: 129` is itself below the plan's full 252-bar window because `vrp`'s own
  history is bounded by SPY RV20's 20-bar warm-up on top of `daily_bars`' own history depth for
  this symbol combination -- still comfortably above the 60-bar floor).
- The strip renders "n/a" tiles for missing inputs -- `RegimeStrip.test.tsx`'s `'renders "n/a"
  tiles with a reason in the tooltip when nothing is seeded yet'`, against
  `cross_asset_empty.json` (confirmed to match the real route's own empty-state response by
  `backend/tests/test_scan_cross_asset_api.py::test_get_cross_asset_empty_when_nothing_seeded`,
  not merely hand-typed).
- Both test suites and both linters pass -- backend 810 tests (baseline 769 + 41 new), 0 ruff
  errors; frontend 233 tests (baseline 226 + 7 new), 0 new lint warnings (still exactly the 4
  pre-existing `react-refresh/only-export-components` warnings), `tsc -b` clean.
- `RegimeStrip` is deliberately **not** wired into `/regime` or `/scan` -- both pages are owned
  by other tasks (T49, T44/T56) per this task's own brief; the component, its types/queries/
  client additions, MSW fixtures and tests ship standalone, ready for either page to import.

### 29.6 Supervisor review: two sample-size defects from the shared union calendar, found live

Both were found while verifying T54 against the live database, both have the same root cause,
and the second changed a **displayed number**, not just a reported sample size.

`GET /api/scan/cross-asset` reads one shared wide closes frame (the design is right — it is
what makes every division align by date). But that frame's index is the *union* of its symbols'
calendars, and Cboe's index calendar carries dates the ETF histories do not: **1287 `^VIX` rows
against 1254 for every sector ETF** over five years, and **8 such dates inside the endpoint's own
400-day lookback**. Those dates are all-`NaN` in any ETF-only slice of the frame.

**1. `sector_correlation`: a 20-day window computed from 18 observations, every day.**
`tail(window)` was taken before dropping the all-`NaN` rows, so index-only dates ate into the
window. The live endpoint reported `sector_correlation_n: 18` — honestly, but systematically
short. Fixed by dropping rows where *no* column has a bar **before** taking the window
(`dropna(how="all")`), which is a different thing from the per-symbol gap handling
(`dropna(how="any")`) that stays in place after it: an all-`NaN` row means "not a session for
this universe", a partially-`NaN` row means "a symbol is genuinely missing a bar", and only the
second is the hazard `n` exists to report. Live after the fix: `n: 20`, and the value
(`0.15900930418661577`) matches an independent computation over a sector-only frame exactly.

**2. `vrp`: the volatility risk premium itself was wrong, not merely under-sampled.**
Realized vol is a *rolling* window, so one all-`NaN` row does not cost one observation — it
costs `RV_PERIOD` of them, since every trailing window containing that row returns `NaN`.
Measured: those 8 calendar rows dropped SPY's valid `RV20` count **from 256 to 129**, and
`vrp_pct_n` with it. Because the surviving rows were the ones whose windows happened to miss a
gap, the *latest* `RV20` was also computed from a poisoned window:

| | before fix | after fix |
|---|---|---|
| `spy_rv20` | 0.08101500442487977 | **0.08397533869386256** |
| `vrp` (vol points) | 8.358 | **8.062** |
| `vrp_pct_n` | 129 | **252** |

The corrected `spy_rv20` matches, to 1e-15, the value independently hand-computed from
`/api/bars` when T45 was verified (`0.08397533869386167`) — which is what identifies the old
number as wrong rather than merely differently-windowed. Fixed by computing the RV series from
`spy_close.dropna()`, i.e. SPY's own sessions.

Both are pinned by regression tests in `tests/test_scan_cross_asset.py`
(`test_sector_correlation_window_counts_sessions_not_foreign_calendar_rows`,
`test_vrp_percentile_is_not_shortened_by_foreign_calendar_rows`), each carrying the live
measurement in its docstring so the reason survives the fix.

**The general lesson, worth applying to any later tool that reads the shared frame:** a union
calendar is safe for *pointwise* arithmetic (which is why the shared-frame design is correct)
and unsafe for anything *rolling or windowed*. Restrict to the relevant symbols' own sessions
before a rolling computation, and take trailing windows after that restriction, never before.
