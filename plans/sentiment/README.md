# sentiment: market sentiment inputs and one desk-wide conditions reading (2026-10-15) — T168–T173

**Status:** approved 2026-10-15; T168–T172 built the same day on `feat/sentiment` (see *Result*). T173 is conditional and open.

## Why

The desk already reads two regimes and one sentiment number, in three places that never meet:

- **Dealer gamma regime**, per optioned symbol (`/gex/regime`, T48): gamma sign, distance to the
  flip, room to the walls, IV vs RV, a verdict with reasons.
- **Macro regime** (`/terminal/regime`, T79–T80): real yield, HY OAS and broad USD over 20 days →
  risk-on / risk-off / rates-led / quiet.
- **P/C ratio** on the GEX report's *Market sentiment* panel — the desk's own chains, one symbol
  at a time, no history and no percentile.

What is missing is (a) the market-wide sentiment inputs the desk does not ingest — Cboe's
exchange put/call ratios and the AAII survey — and (b) one point-in-time reading that puts
gamma, macro and sentiment side by side, so "what kind of market is this" is one request, not
three pages.

## The rules

1. **A reading, not a signal.** The composite describes conditions. Nothing here reaches
   `broker.order_intents` or the executor (invariant 11). Whether sentiment extremes predict
   anything on the CFDs is T173, a stage-1 test run through the `edge-research` skill with its
   search space declared first (invariant 9).
2. **Point-in-time, through the terminal.** Every input is a `terminal.observations` series with
   an honest `as_of` (invariant 10); the composite is computed from what was published by
   `as_of`, so the reading for 2022-06-16 is the one the desk could have had that evening.
3. **No fitting.** Fixed components, fixed orientation, equal weights, fixed bands — all named
   constants in one module. Fitting weights to returns would be an EdgeLab trial pretending to be
   plumbing.
4. **Missing is `·`, never imputed.** A component without a value as of `as_of` (or older than
   twice its cadence) drops out and is listed as missing. Fewer than four components →
   `insufficient`, the same honesty as the macro regime's `quiet`.
5. **Every label carries its evidence** (T48's rule): each component's raw value, percentile,
   own `as_of` and orientation is in the response.

## Verified facts (2026-10-15, live)

- **Cboe put/call, history CSVs** — `https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/{totalpc,equitypc,indexpc}.csv`,
  200 without auth. A disclaimer line, a product line, then `DATE,CALL,PUT,TOTAL,P/C Ratio`,
  dates `M/D/YYYY`. `equitypc.csv` runs **2006-11-01 → 2019-10-04** and stops there.
- **Cboe put/call, daily JSON** — `https://cdn.cboe.com/data/us/options/market_statistics/daily/{YYYY-MM-DD}_daily_options`,
  200 for 2019-10-07, 2020-06-01, 2023-01-03, 2026-10-14. `ratios[]` carries `TOTAL`, `INDEX`,
  `EXCHANGE TRADED PRODUCTS` and `EQUITY PUT/CALL RATIO` as strings. **The two sources join with
  no gap** (CSV ends Friday 10-04, JSON starts Monday 10-07). A not-yet-published day returns
  **403 AccessDenied** (S3), not 404 — the adapter must read 403 on today as "not yet", and 403
  on an old weekday as an error. One request per day: ~1,800 requests for the backfill, so pace
  it and backfill once.
- **AAII sentiment** — `https://www.aaii.com/files/surveys/sentiment.xls`, 200, legacy `.xls`
  (needs `xlrd`, not a current dependency). Header on row 3 (`Date, Bullish, Neutral, Bearish,
  Total…`), weekly from **1987-06-26**, values as fractions (0.4025 = 40.25 %), last row
  2026-10-08 (a Thursday, the release day). Trailing rows hold yearly counts (`Count '25`) —
  filter on a parseable date.
- **CNN Fear & Greed** — `production.dataviz.cnn.io` returns **418 "You're a bot"**. Not a source.
  It is itself a composite of things the desk has or is adding here, so the desk builds its own.
- **NAAIM exposure** — the page renders its table client-side; no file link in the HTML.
  Deferred; not needed for the first version.
- Already in the terminal and reused as-is: `vol.vix`, `vol.vix3m`, `vol.skew` (Cboe), `credit.hy.oas`,
  `eq.spx` (FRED), the CFTC E-mini S&P series (plans/cot), and `analytics/regime.classify`.
- `derive.rolling_percentile` exists; the cross-module pattern for reading another module's
  tables into terminal series is T91's `prices` adapter over `gex.daily_bars`.

## The composite (T171)

Each component is a percentile over a trailing **756-observation (≈3-year)** window, oriented so
**100 = greed / complacency, 0 = fear**:

| Component | Series | Transform | Orientation |
|---|---|---|---|
| Volatility level | `vol.vix` | level | inverted |
| Vol term structure | `vol.vix` / `vol.vix3m` | ratio (>1 = backwardation) | inverted |
| Equity put/call | `sent.pc.equity` | 10-day mean | inverted |
| Retail survey | `sent.aaii.bull` − `sent.aaii.bear` | 4-week mean | direct |
| Credit | `credit.hy.oas` | 20-day change | inverted |
| Trend | `eq.spx` | close / 125-day mean − 1 | direct |
| Spec positioning | CFTC E-mini S&P leveraged-funds net | 3-year percentile (already computed) | direct |

Score = mean of the available components. Bands: `<20` extreme fear, `20–40` fear, `40–60`
neutral, `60–80` greed, `≥80` extreme greed. SKEW is shown, not scored — its direction is
ambiguous.

## The conditions reading (T171)

`GET /api/terminal/conditions?as_of=` returns three legs and a synthesis:

- **macro** — `classify()` as `/terminal/regime` returns it today;
- **gamma** — SPX and QQQ: net GEX sign and spot-vs-flip distance, from the T170 series;
- **sentiment** — the composite above, with every component;
- **synthesis** — a fixed lookup over (gamma sign × sentiment band), each cell a one-line
  reading with its reason, e.g. *negative gamma + fear → "stressed: dealers amplify, moves
  extend"*, *positive gamma + greed → "complacent: dealers dampen, vol is cheap"*. The table
  lives in the module, not in the frontend.

## Tasks

### T168 · Sonnet · —
**Cboe put/call adapter.** Extend `adapters/cboe.py` (or a sibling `cboe_pc.py` if the shapes
diverge too far) with `sent.pc.total`, `sent.pc.equity`, `sent.pc.index`: CSV for history to
2019-10-04, the daily JSON from 2019-10-07. `derived_lag`; `as_of` = value date plus a
publication time measured on first contact (record the earliest observed time the JSON exists;
default to next day 08:00 ET until measured). 403 handling as above. Register in `universe.py`.
**Acceptance:** a backfill from 2006 has no gap at the 2019-10 seam; 2026-10-14's equity value
matches the JSON; a 403 for today logs "not yet published" and does not fail the run.

### T169 · Sonnet · —
**AAII sentiment adapter.** `adapters/aaii.py`: `sent.aaii.bull`, `sent.aaii.neutral`,
`sent.aaii.bear`, stored in percent. `as_of` = the reported Thursday at 12:00 ET
(`derived_lag`). Add `xlrd` to the backend dependencies. If AAII revises a past week, ingest
adds a row (invariant 10) — never update.
**Acceptance:** 1987-06-26 → latest ingested; 2026-10-08 bull = 40.25; a re-ingest of an
unchanged file adds nothing.

### T170 · Sonnet · —
**Desk gamma as terminal series.** A cross-module adapter in T91's pattern reading
`gex.gex_levels` (filter `ALL`) joined to `gex.snapshots`: `desk.gex.spx.net`,
`desk.gex.spx.flip_dist`, same for QQQ. `as_of` = `gex_levels.computed_at` (UTC; see *Result*); one
value per session — the last EOD snapshot of the session, never an intraday one mixed with EOD.
**Acceptance:** values for the last 20 sessions match `/api/gex/levels` for the same snapshots.

### T171 · Opus · T168, T169, T170
**Sentiment composite and the conditions endpoint.** `analytics/sentiment.py` (the method
above, constants named, evidence returned), `GET /api/terminal/sentiment` and
`GET /api/terminal/conditions`, both `as_of`-aware, schemas in the terminal API's style. A
brief section and an MCP tool (`desk_conditions`) so a market question reaches it.
**Acceptance:** the score for a fixed `as_of` is reproducible; an `as_of` before AAII's Thursday
release excludes that week; a component older than twice its cadence is listed as missing; the
March 2020 and October 2022 readings land in fear or extreme fear (sanity, not a fit).

### T172 · Sonnet · T171
**The screen.** A *Conditions* panel on the terminal's Regime page — the three legs, the
synthesis line, the composite as a 0–100 bar with each component's percentile beneath it — and
a compact tile on the launcher tape. MSW handlers for both endpoints. Run `npm run build`.

### T173 · Opus · T171 — conditional
**Stage-1: do sentiment extremes carry a forward return on the index CFDs?** Through the
`edge-research` skill. Declare first: horizons (5 and 20 sessions), thresholds (<20, >80),
instruments (`S&P.fs`, `NAS100.fs`), from 2007. Noise ceiling, doubled cost, walk-forward. Only
if the user asks for it after T172 lands.

## Result

**T168–T172, built 2026-10-15** on `feat/sentiment`. Backend 1,590 passed / 16 skipped, ruff clean;
frontend 434 passed, lint 0 errors, `npm run build` green.

What landed:

- `adapters/cboe_pc.py` (`sent.pc.total|equity|index`), `adapters/aaii.py`
  (`sent.aaii.bull|neutral|bear`, percent), `adapters/desk_gex.py`
  (`desk.gex.{spx,qqq}.{net,flip_dist}`), registered as sources `cboe_pc`, `aaii`, `desk_gex`.
  `xlrd` added for AAII's legacy `.xls`.
- `analytics/sentiment.py`: the composite, gamma reading and synthesis; `GET
  /api/terminal/sentiment` and `/api/terminal/conditions`; a *Sentiment* subsection in the brief's
  "What's priced"; MCP tool `terminal_conditions` (runs the analytics as `quantdesk_ro`).
- Frontend: `ConditionsPanel` on `/terminal/regime`, `LauncherConditions` beside the launcher tape.

Verified live (no database; adapters fetched and parsed, the composite fed in memory):

- Put/call backfill 2019-09-23 → 2026-10-14: 1,776 days per series, **no gap over 4 days at or
  after the 2019-10-04/07 seam**; 2026-10-14 equity = 0.61, matching the JSON. The first backfill
  took **10 minutes** (~1,800 uncached requests); later runs request only the last
  `MISSING_SETTLE_DAYS` (7) and anything new.
- Publication time, from S3 `Last-Modified`: recent days land 21:20–22:50 ET the same evening;
  2026-06-15's file was rewritten 13:58 ET the next day. Hence `as_of` = next day 06:00 ET, and the
  cache only freezes a day once it is older than 7 days.
- AAII: 2,044 weekly rows 1987-07-24 → 2026-10-08, no duplicate dates; 2026-10-08 bull 40.2524.
  Most rows are Thursdays (1,696), the older ones Fridays and a few Wednesdays — the
  "reported date at 12:00 ET" convention applies to all of them. `Last-Modified` 2026-10-08 10:40 ET.
- Composite on real history (VIX, VIX3M/VIX, equity P/C, AAII, FRED S&P and HY OAS; COT left out
  of this offline check because it needs `derive`):

  | as of | score | band | | as of | score | band |
  |---|---:|---|---|---|---:|---|
  | 2018-12-24 | 1.3 | extreme fear | | 2021-01-08 | 77.0 | greed |
  | 2020-03-16 | 3.9 | extreme fear | | 2021-11-08 | 76.6 | greed |
  | 2022-06-16 | 6.7 | extreme fear | | 2023-07-31 | 78.3 | greed |
  | 2022-10-12 | 5.5 | extreme fear | | 2024-07-16 | 79.0 | greed |
  | 2025-04-08 | 8.1 | extreme fear | | 2026-10-15 | 28.6 | fear |

  Sanity only — nothing was tuned to these dates.

Judgment calls:

- **Desk gamma `as_of` is `computed_at`, not `captured_at`.** The number did not exist until the
  engine ran, and a recompute then adds a row instead of colliding in the loader (which raises on
  a changed value under the same `as_of`). Cost: a history backfilled later is only visible from
  the backfill date onward, which is the honest answer.
- **The credit component is short.** FRED serves only a trailing three years of ICE's HY OAS, so
  the 20-day-change percentile needs ~18 months of stored history and is `short_history` or
  `no_data` before roughly 2024 in a fresh store. Known, documented in the brief already.
- **An AAII revision would raise.** Like Cboe's VIX files, `derived_lag` rows carry a fixed
  `as_of`, so a changed past value under the same `as_of` stops the `aaii` source loudly (the
  loader's rule). None observed; if it happens, the fix is an ingest-time `as_of` for the changed
  row, not an overwrite.

Not verified here: an ingest into Postgres and the endpoints against the homeserver's data (no
local database; the MCP connector was unreachable this session). First deploy: run
`python -m app.modules.terminal.cli ingest --source cboe_pc` once by hand (10 minutes), then
`--source aaii` and `--source desk_gex`, then `derive`, and read `/api/terminal/conditions`.
