# charter-mt5: the broker's own prices on the desk (2026-10-08) — experiment, T143–T147

> **Experiment branch `experiment/charter-mt5`.** Nothing here merges to `main` without the user
> deciding so explicitly. Steps 1–3 below are filed. Step 4 (automated demo execution) and step 5
> (live journal) are **not filed**: see *Out of scope*.

## Goal

The desk measures SPX, SPY, QQQ, DIA and GLD, and the user trades US500, NAS100, US30 and XAUUSD
CFDs on Axi. Everything in between is done by hand or not at all: the CFD ratio comes from two
spots the user types in (T41), and the edge-research skill lists spread, swap, contract spec and
basis under *"model these or the backtest lies"* with no source for any of them.

This initiative gives the desk its own MetaTrader 5 terminal, logged into an **Axi demo** account,
and builds three things on it:

1. **CFD market data.** Bars, ticks, spreads and contract specs for the traded instruments,
   captured continuously and stored point-in-time (T143, T144).
2. **Measured basis.** The offset and ratio between each desk symbol and its CFD at every 5-minute
   close, so GEX levels can be stated in the price the user actually trades (T145).
3. **Price action as code.** Swings, structure, zones, bar patterns and sweeps as pure, lookahead-safe
   functions (T146), and an event study that tests them on CFD bars through EdgeLab's honesty
   rules (T147). The most promising variants are price action *at desk levels*.

Origins: [carbon-copy](../../../carbon-copy) has already run MT5 headless under Wine in Docker on
the homeserver, and [charter](../../../charter) already has a working MT5 data client. Both are
reused here rather than rewritten.

## What the user sees

- A new `broker` module. It has **no screens** in this round (charts were dropped 2026-10-08).
  Its read surface is the MCP connector: `query_sql` over the new `broker.` schema, plus one tool
  (`broker_levels`) that returns GEX walls and the flip in CFD price with the basis it used.
- An `mt5` container and a `broker-ingest` worker on the homeserver.
- Event-study results recorded in the trial registry, so they show up on the research leaderboard
  **with their noise ceiling**.

## Data

| What | Where | Why there |
|---|---|---|
| M1 bars (OHLC, tick volume, spread in points) per CFD | `broker.bars` (Postgres) | A few hundred thousand rows a year per symbol. Same shape as `gex.intraday_bars`, which is also in Postgres |
| Ticks (bid, ask, time_msc) | Parquet under `DATA_DIR/broker/ticks/<symbol>/<date>.parquet`, indexed in `broker.tick_files` | Volume (XAUUSD can print 100k+ a day). Same reasoning as invariant 5 |
| Contract spec snapshots | `broker.symbol_specs`: one row per *change*, never an update | Swap rates and stop levels change. A backtest has to use the spec that was in force at the time |
| Basis at each desk 5-minute close | `broker.basis` | Computed once, read by `broker_levels` and the event study |
| Event-study trials | `research.trials` through `Registry` | One registry, invariant 9 |

Symbols: **US500, NAS100, US30, XAUUSD**. Axi's exact symbol names and suffixes are verified at
first contact, not assumed. They live in config (`BROKER_SYMBOLS`), never in code.

## Design decisions (judgment calls, named as such)

1. **The Wine terminal and the desk's Python are two containers, joined by a small TCP JSON-lines
   protocol.** The `MetaTrader5` package only runs in Windows Python, so inside Wine. Carbon-copy
   connects the two over stdin/stdout in one container, and that would mean putting the whole
   backend's dependencies into a Wine image. Here the `mt5` container runs the terminal plus a
   bridge that listens on the internal compose network only (no published port). `broker-ingest`
   is the ordinary backend image and is the bridge's only client. The bridge stays thin: it
   translates MT5 calls and holds no logic.
2. **For this round the bridge exposes read operations only.** These are `account`, `symbols`,
   `spec`, `rates_range`, `ticks_range` and `tick`. There is no `order_send` path. The terminal
   should log in with the account's **investor password**, which MT5 itself refuses to trade
   from. Step 4 changes that deliberately, in its own task, and at the same time amends the
   invariant on this branch. Until then, "no order routing" holds unchanged.
3. **MT5 timestamps are server wall-clock, not UTC** (charter's `client.py` documents it).
   Axi's server runs on a broker timezone that moves with DST. The offset is **measured, never
   configured**: the bridge reports `tick().time` against the container's UTC clock, rounded to
   the nearest 15 minutes, and every ingest batch records the offset it used. Everything is
   stored in tz-aware UTC (invariant 4). A batch that runs while the measured offset changes is
   rejected and re-run, because that is the DST boundary.
4. **The forming bar is never stored.** Same rule as EdgeLab's `_drop_forming_bar` and charter's
   `copy_rates_from_pos(…, 1, …)`.
5. **Spec snapshots add rows.** Each check compares against the latest row and inserts only on
   a difference. This is the terminal's point-in-time rule (invariant 10) applied to broker
   specs. Commission is not in the MT5 spec, so it is a config value with its source recorded.
6. **Basis is measured at the desk's bar time.** At each `gex.intraday_bars` 5-minute close for
   SPX, SPY, QQQ, DIA and GLD, take the CFD's mid at that same UTC instant from M1 bars (the
   close of the M1 bar ending at that time) and store `offset = cfd − desk` and
   `ratio = cfd / desk`. Index families use the offset, so SPX→US500 is roughly a fair-value
   offset. ETF→CFD uses the ratio: SPY→US500 is about 10× with drift, GLD→XAUUSD has the expense
   ratio. Levels are translated with the most recent basis at or before the level's
   `captured_at`, never a later one. QQQ→NAS100 is an NDX proxy, so its ratio is labelled
   `proxy` and is noisier by design. This supersedes T41's "user-entered, never stored" rule
   *only on this branch*. T41's input remains as a fallback when no basis exists.
7. **Price action lives in `app/modules/gex/scan/price_action.py`, beside `indicators.py`.** It
   follows the same pure contract (no I/O, no clock) and reuses `atr`. Every detector returns
   both **`occurred_at`** and **`confirmed_at`**. A swing high at bar *t* with a right-hand
   window of *k* is confirmed at *t+k*, and any consumer may only see it from `confirmed_at`
   onwards. Lookahead is the classic way price-action backtests lie, so it is made structural
   rather than left to discipline.
8. **The event study scores in R, intrabar-conservative, with doubled measured costs.** It
   carries over T61/T115's rules from `outcomes.py` to M1 resolution:
   - stop before target on an ambiguous bar;
   - gaps fill at the open;
   - the fill bar never pays.

   The cost is the **spread actually recorded on the entry bar**, plus commission, then doubled
   (EdgeLab's doubled-cost gate). Swap applies only to holds that cross the broker's rollover.
9. **Every variant tested is a trial.** One trial is one pattern × parameter set × symbol ×
   timeframe × exit rule, recorded through `Registry`, so the noise ceiling grows with the
   pattern zoo. Splits are walk-forward and fixed before the first run. GEX-conditioned variants
   (rejection at a wall, sweep through the flip) can only use data since the capture began in
   mid-September. They are reported as **leanings that feed a stage-0 spec**, never as gate
   passes.

## Tasks

### T143 · Opus · —
**`mt5` container + bridge, read-only.**
- Paths:
  - `docker/mt5/` (Dockerfile, entrypoint, bridge)
  - `compose.yaml`, `compose.override.yaml`, `compose.prod.yaml`
  - `backend/app/modules/broker/{__init__,client}.py`
  - `backend/app/core/schemas.py`
  - one migration creating schema `broker` and extending the read-only role's grants
- Port carbon-copy's Wine and embedded-Python image stages (pinned versions as carbon-copy has
  them) and its template-login flow over VNC. Use a VNC port other than 5900, which carbon-copy
  holds on the homeserver. One terminal only.
- The bridge is a TCP JSON-lines server with the read operations of decision 2. It reconnects
  to the terminal the way carbon-copy's `Agent.connect` does, and refuses to serve while
  `account_info()` is not the configured login.
- `client.py` is the async Linux-side client with typed results. Tests run it against a fake
  bridge.
- The container's healthcheck is the bridge answering `account`.
- **Acceptance:**
  - On the homeserver, `account` returns the Axi demo login with `trade_mode == DEMO`.
  - `rates_range` returns M1 bars for all four configured symbols.
  - `docker compose up` on this Windows dev host does **not** start `mt5`: it sits behind a
    compose profile, because Docker Desktop crashes MT5 (verified fact below).

### T144 · Sonnet · T143
**`broker-ingest` worker: bars, ticks, specs.**
- Paths:
  - `backend/app/workers/broker_ingest.py`
  - `backend/app/modules/broker/{tables,ingest,store}.py`
  - its migration
  - tests
- Backfill M1 bars as deep as Axi serves them (record the depth found), then poll every minute.
  Ticks are pulled per closed hour into Parquet. Spec snapshots are checked hourly (decision 5).
- Server-time handling as decision 3, with a unit test across a DST boundary built from a
  synthetic fixture labelled as such.
- Idempotent: a re-run of any window is a no-op. Bars upsert on `(symbol, timeframe, ts)`. These
  are market prices, not point-in-time observations, but a *changed* closed bar is logged as a
  warning.
- Health: `GET /api/broker/health` reports the last bar per symbol and the measured server
  offset.
- **Acceptance:**
  - After 24 hours on the homeserver, `broker.bars` has no gap longer than the broker's own
    session break.
  - Bar timestamps line up with `gex.intraday_bars` to the minute on at least one spot check:
    US500 versus SPX direction over a known move.

### T145 · Opus · T144
**Basis and `broker_levels`.**
- Paths:
  - `backend/app/modules/broker/basis.py` (pure)
  - `backend/app/modules/broker/jobs/basis.py`
  - `backend/app/modules/broker/router.py`
  - `backend/app/mcp/` (one tool)
  - tests
- Decision 6. `GET /api/broker/levels?symbol=SPX&cfd=US500&as_of=` returns the walls and the flip
  from `gex.gex_levels`, translated with the basis in force at `captured_at`. The basis row used
  (its time, offset/ratio and method) is returned alongside.
- **Acceptance:**
  - The SPX→US500 offset is stable within a session, with a slow drift that makes sense (fair
    value).
  - The GLD→XAUUSD ratio lies within 1% of the T41 hand-entered ratio on a day the user
    supplies one.
  - The MCP tool states the basis it used.

### T146 · Opus · —
**`scan/price_action.py`, pure.**
- Paths: `backend/app/modules/gex/scan/price_action.py`, `backend/tests/…`
- Detectors:
  - swing highs and lows (left/right window), with `confirmed_at`;
  - market structure (HH, HL, LH, LL), break of structure, change of character;
  - support/resistance zones from swing clusters (ATR-scaled tolerance);
  - inside, outside, pin and engulfing bars;
  - liquidity sweeps (a wick through a prior swing, then a close back inside).
- **The test that matters is a property test.** For random bar series and every *t*, detectors
  run on `bars[:t]` must agree with detectors run on the full series filtered to
  `confirmed_at <= t`. If the two ever differ, the code is looking ahead.
- **Acceptance:** the property test passes, along with hand-built fixtures for each detector, and
  `ruff` is clean.

### T147 · Opus · T144, T145, T146
**Event study through EdgeLab.**
- Paths:
  - `backend/app/modules/research/events.py` (the M1 R evaluator, pure)
  - `backend/app/modules/research/pa_study.py` (the harness and CLI)
  - tests
  - results written to `plans/charter-mt5/` under *Result*
- Decisions 8 and 9. The search space is declared up front in one file and kept small: about 6
  patterns × 2–3 parameter sets × 4 symbols × M5/M15/H1 × 2 exits, about 300 trials. A
  GEX-conditioned family is added on top, using `broker_levels`.
- Output per trial:
  - number of trades;
  - mean R before and after doubled costs;
  - out-of-sample Sharpe against the noise ceiling;
  - the cost share (cost ÷ gross edge).
- **Acceptance:**
  - The study runs end to end, and every trial is visible in `research.trials`.
  - The *Result* lists every trial above its ceiling after doubled costs, or says plainly that
    none are.
  - Anything promising becomes a stage-0 spec in `docs/edges/`, not a strategy.

Order: T143 → T144 → (T145 ∥ T146) → T147. T146 can start immediately on desk bars, because it
needs no broker data to be written and tested.

## Verified facts (2026-10-08)

- **MT5 under Wine works on the homeserver and crashes on Docker Desktop** (WSL2 kernel; MT5's
  copy protection). Source: carbon-copy README §Requirements. Cost per terminal: about 200 MB RAM
  and about 10% of a core when idle (measured on an i3-5010U).
- Carbon-copy's image uses Debian trixie, `winehq-staging`, embedded Python 3.13.16 and
  `MetaTrader5==5.0.6231`. Its template has to be logged in once over VNC so it learns the
  broker's servers. Without that the bridge fails with `IPC timeout`.
- Charter: MT5 tick and rate times are **server wall-clock**. XAUUSD's `last` is 0, so the bid
  is the price. The first `copy_rates_range` for a symbol can come back incomplete while the
  terminal downloads history, so it needs a short retry.
- The desk has 5-minute bars for `SPX,SPY,QQQ,GLD,DIA,^VIX` in `gex.intraday_bars`, in Postgres
  (`INTRADAY_BARS_SYMBOLS`).
- `noise_ceiling(n, T) = sqrt(2 ln n) / sqrt(T)` on annualized out-of-sample Sharpe
  (`research/backtest.py`). Shrinking the search space is the only way to lower it.
- The read-only role's grants loop over `app.core.schemas.SCHEMAS`, so a new schema needs that
  tuple extended *and* a migration that grants on it.

## Likely first-contact failures

- Axi symbol names carry a suffix, or US30 is called `US30.cash`. Fix this in config, not code.
- The template terminal was logged into carbon-copy's account. Use a **separate** demo login,
  because two terminals on one login kick each other off, which is the Theta Terminal lesson.
- M1 history depth on the demo server is shallower than expected (months, not years). If so,
  T147's out-of-sample window shrinks and the ceiling rises: say so, and do not substitute
  Dukascopy silently. Dukascopy is a proxy and would need to be its own decision.
- The DST week: US and broker DST switch on different dates, so for one or two weeks a year the
  offset to New York changes twice.
- Tick volume on demo may differ from live. The spread on demo may be tighter than on live. Treat
  both as **lower bounds** on cost.

## Acceptance (initiative)

T143–T147 have landed on the experiment branch. `broker_levels` answers for all five desk
symbols, and the event study's *Result* is written here, including a null result if that is
what it is.

## Out of scope (this round)

- **Charts and charter's UI.** Dropped 2026-10-08. Charter's Flex Renko engine can be ported as a
  pure module later, when charts come back.
- **Step 4, the demo-only executor.** Agreed in principle on 2026-10-08, with these terms:
  - a demo `trade_mode` check in code;
  - order intents in the database, sent by a separate executor container;
  - the stop-loss always attached to the order;
  - hard limits outside the strategies;
  - reconciliation on restart, reusing carbon-copy's;
  - only gated strategies, flip-cross first.

  It is filed when steps 1–3 land. At that point the "no order routing" invariant is amended
  **on this branch**, with the reason, rather than deleted.
- **Step 5, the live journal** (demo fills compared with the backtest, kill-rule status).
- MT5's Strategy Tester. EdgeLab is the one backtester.
