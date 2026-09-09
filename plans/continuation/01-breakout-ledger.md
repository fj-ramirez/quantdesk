# 01 · Breakout ledger

## Goal

Answer the user's question empirically: for every symbol in the universe, how often has a
breakout of the N-day range actually continued over the last six months, and which breakouts
are open right now. No indicator interpretation, just recorded outcomes.

## What the user sees

A new `/scan` route (nav tab "Scan"), first view "Breakouts". A table, one row per symbol,
sorted by continuation rate:

| Symbol | Events | Continued | Rate | Follow-through (ATR) | Last event | Status |

Rate is blank with an "n < 5" note when there are fewer than five events; a rate from three
events is noise and the app does not print noise as a number. A second panel, "Open
breakouts", lists events whose evaluation window has not closed yet: symbol, direction, level,
bars elapsed, current excursion in ATR. Clicking a row opens the symbol's event list and a
lightweight-charts price chart with event markers. `?n=20&k=5` in the URL selects the range
length and holding period, same mechanism as `filter` in `state/urlState.ts`.

## Data

`read_universe_closes` and per-symbol `read_bars` from T42. Nothing else.

## Design decisions

The definitions below are the deliverable's contract. The agent may propose better ones but
must implement these first and record any alternative in `docs/validation-scan.md`.

- **Breakout event** at bar t: up if `close[t] > max(high[t-N..t-1])`, down if
  `close[t] < min(low[t-N..t-1])`. N ∈ {20, 55}, default 20.
- **Clustering.** After an event, no new event of the same direction is recorded until k bars
  have passed (the first event owns the window). Opposite-direction events are always recorded.
- **Level** = the range boundary that was broken, not the close.
- **Outcome after k bars** (k ∈ {3, 5, 10}, default 5): `continued` if
  `sign · (close[t+k] − level) > 0`, else `failed`. `pending` while fewer than k bars exist.
- **Follow-through** = `sign · (close[t+k] − level) / ATR14[t]`, reported as the mean over
  resolved events. Max favourable and max adverse excursion over the window, in ATR units, are
  stored per event for the detail view.
- **Lookback** for the summary: 126 bars by default.
- **Minimum events to print a rate: 5.** Below that the rate is `None`.

## Tasks

### T43 · Sonnet · T42
**Breakout ledger: pure module and API**

Create the package `backend/app/scan/` with the same purity contract as `app/gex/engine.py`:
no HTTP, DB, filesystem or logging in `app/scan/*.py`. `backend/app/scan/breakouts.py`:
`detect_events(bars: pd.DataFrame, n: int, k: int) -> list[BreakoutEvent]`,
`summarize(events, lookback) -> BreakoutSummary`, both returning frozen dataclasses;
`atr(bars, 14)` lives in `backend/app/scan/indicators.py` so T45 reuses it.
`backend/app/api/scan.py`: `GET /api/scan/breakouts?n=&k=&lookback=` returning per-symbol
summaries plus open events for the whole `SCAN_UNIVERSE`; `GET
/api/scan/breakouts/{symbol}` returning the event list. Compute on request from the bars
table; do not add a table. Start `docs/validation-scan.md` with the fixture values.

Paths: `backend/app/scan/__init__.py`, `backend/app/scan/breakouts.py`,
`backend/app/scan/indicators.py`, `backend/app/api/scan.py`, `backend/app/main.py` (mount
only), `backend/tests/test_scan_breakouts.py`, `backend/tests/test_scan_api.py`,
`docs/validation-scan.md`.

Acceptance: a synthetic monotone series produces events that all resolve `continued`; a
sawtooth that pokes above the range and closes back inside within k bars produces events that
all resolve `failed`; the clustering rule is pinned by a test where two consecutive up-closes
above the range produce one event; a symbol with three events reports `rate=None`; the API
returns within 2 s for 45 symbols × 500 bars on the dev machine (measure it and record the
number in the docstring); `uv run pytest` and `ruff check .` pass.

### T44 · Sonnet · T55
**Scan page: breakouts and trend views**

> **UI spec superseded (2026-09-09):** the page spec and task block for this view now live in
> [07-ui.md](07-ui.md); the paragraph below is kept for history. Dispatch from 07-ui.md.

Paths: `frontend/src/pages/Scan.tsx`, `frontend/src/components/scan/BreakoutTable.tsx`,
`frontend/src/components/scan/EventChart.tsx` (lightweight-charts, already a dependency),
`frontend/src/api/queries.ts` and `types.ts` (additive), `frontend/src/state/urlState.ts`
(add `n`, `k`), `frontend/src/App.tsx` and `components/layout/AppShell.tsx` (route + nav,
preserving `location.search` as the other links do), `frontend/src/mocks/handlers.ts` and
`fixtures/` (MSW), tests alongside.

Acceptance: renders against MSW fixtures including the `rate=None` row and an empty
open-breakouts panel; deep link `/scan?n=55&k=10` drives the query; `npm test` and `npm run
lint` pass; the supervisor loads it against the live backend and sees real rows.

## Verified facts (2026-09-09)

- Routes are declared in `frontend/src/App.tsx`; nav links in `AppShell.tsx` carry
  `location.search` deliberately so URL state survives navigation.
- Query hooks live in `frontend/src/api/queries.ts` with a `queryKeys` factory; MSW handlers
  in `frontend/src/mocks/handlers.ts`.
- `lightweight-charts` 5.x and `echarts` 6.x are already installed; do not add a charting
  dependency.

## Likely first-contact failures

- Symbols with gaps (missing bars) producing false breakouts across the gap. Detect on a
  contiguous index; drop symbols with more than 2 % missing bars in the lookback and report
  them in the response's `excluded` list.
- Off-by-one on the range window including the current bar (a close can never exceed its own
  high, so the event count would collapse to zero). The monotone-series test catches this.

## Out of scope

Intraday breakouts, volume confirmation, any signal generation or alerts.
