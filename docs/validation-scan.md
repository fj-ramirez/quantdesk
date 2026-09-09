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


## 6. Acceptance checklist cross-reference

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
