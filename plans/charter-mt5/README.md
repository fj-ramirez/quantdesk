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

Symbols: the **futures-based** Axi CFDs for the three indices, plus `XAUUSD`. The user said on
2026-10-08 that cash CFDs have a 1-lot minimum on live accounts, against 0.01 for the futures-based
ones, so the futures-based ones are what gets traded. Names are as Axi spells them, verified at
first contact (see the T143 result). They live in config (`BROKER_SYMBOLS`), never in code.

What futures-based means for the plan:

- **Rolls.** The contracts expire. Their price history jumps at each roll, and EdgeLab's
  futures roll-gap caveat (invariant 9) applies to T147. Specs carry `start_time` and
  `expiration_time`, so T144 can detect a roll and T147 can refuse to hold a trade across one,
  or price the jump explicitly.
- **Basis** to SPX/NDX/DJI includes the futures' fair value. It decays toward expiry and jumps
  at the roll. T145 resets its basis series at a detected roll instead of smoothing over the
  jump.
- **Swap** may be zero or different on these contracts. Read it from the spec snapshots; never
  carry it over from the cash CFD.

## Design decisions (judgment calls, named as such)

1. **The Wine terminal and the desk's Python are two containers, joined by a small TCP JSON-lines
   protocol.** The `MetaTrader5` package only runs in Windows Python, so inside Wine. Carbon-copy
   connects the two over stdin/stdout in one container, and that would mean putting the whole
   backend's dependencies into a Wine image. Here the `mt5` container runs the terminal plus a
   bridge that listens on the internal compose network only (no published port). `broker-ingest`
   is the ordinary backend image and is the bridge's only client. The bridge stays thin: it
   translates MT5 calls and holds no logic.
2. **For this round the bridge exposes read operations only.** These are `account`, `symbols`,
   `spec`, `rates_range`, `ticks_range` and `tick`. There is no `order_send` path, and the
   terminal's ini disables Experts and live trading. Axi demo accounts offer no investor
   password (user, 2026-10-08), so the login is the full one, and the code is the guard.
   Step 4 changes that deliberately, in its own task, and at the same time amends the
   invariant on this branch. Until then, "no order routing" holds unchanged.
3. **MT5 timestamps are server wall-clock, not UTC** (charter's `client.py` documents it).
   Axi's server runs on a broker timezone that moves with DST. **Revised 2026-10-08:** a
   measured offset describes only the present, and backfilled bars from last summer were
   stamped under a different one. So conversion uses the **New York close convention**: server
   time = New York + 7 h, UTC+2 in US winter and UTC+3 in US summer
   (`app/modules/broker/servertime.py`). It is used **only while a fresh measurement confirms
   it**. A measurement is taken from a tick that changed since the previous poll, so it is at
   most about a minute old, and is rounded to the quarter-hour. Bars and ticks are written only
   when a confirming check was made in the last 8 days and no fresh check disagreed since. A
   disagreement stops writes and is logged as an error. Every bar stores the `offset_s` it was
   converted with, and every check is recorded in `broker.clock_checks`. Everything is stored
   in tz-aware UTC (invariant 4). T144's live acceptance adds a data-level check: `S&P.fs`
   lines up with the desk's SPX 5-minute bars at lag 0, not at ±1 h.
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
10. **No Alembic migration while this is an experiment.** The homeserver runs one database. A
    migration on this branch would move its `alembic_version` to a revision `main` has never
    seen, and the next deploy from `main` would fail `alembic upgrade head` in the backend's
    boot step, taking the API down. So `broker-ingest` (T144) creates `broker.` and its tables
    itself with an idempotent `create_all` and grants `SELECT` to the read-only role. This is
    the one place the module breaks the repo's migration convention, and only until merge: if
    the experiment merges, the tables become a proper migration in the same PR. Dropping the
    schema undoes the whole experiment.
11. **Deploying from the branch deploys only the broker services.** `deploy.sh` pulls whichever
    branch is checked out and, by default, rebuilds the whole stack. On a branch other than
    `main` it now refuses unless services are named (`scripts/deploy.sh mt5`, later
    `mt5 broker-ingest`) or `--any-branch` is passed. Capture, the API and the other workers
    keep running `main`'s images.

## Tasks

### T143 · Opus · —
**`mt5` container + bridge, read-only.**
- Paths:
  - `docker/mt5/` (Dockerfile, entrypoint, bridge)
  - `compose.yaml`, `compose.override.yaml`, `compose.prod.yaml`
  - `backend/app/modules/broker/{__init__,client}.py`
  - `scripts/deploy.sh` (the off-main guard, decision 11)
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
  - `compose*.yaml` (the `broker-ingest` service, `broker` profile)
  - tests
- Owns the `broker` schema per decision 10 (`create_all` plus the read-only grant). `broker`
  goes into `app.core.schemas.SCHEMAS` only at merge, with the migration.
- Backfill M1 bars as deep as Axi serves them (record the depth found), then poll every minute.
  Ticks are pulled per closed hour into Parquet. Spec snapshots are checked hourly (decision 5).
- Server-time handling as decision 3, with a unit test across a DST boundary built from a
  synthetic fixture labelled as such.
- Idempotent: a re-run of any window is a no-op. Bars upsert on `(symbol, timeframe, ts)`. These
  are market prices, not point-in-time observations, but a *changed* closed bar is logged as a
  warning.
- Health is the worker's log and `query_sql` on `broker.*`, not an API route. An API route would
  mean deploying the backend from this branch, and decision 11 keeps it on `main`.
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

## Result

### T146 — landed 2026-10-08

- `app/modules/gex/scan/price_action.py` provides `swings`, `structure`, `breaks` (BOS/CHoCH),
  `zones`, `bar_patterns` (inside, outside, pin, engulfing) and `sweeps`. Every event is an
  `Event` with integer bar positions `occurred` and `confirmed`, and no timestamps (see the
  module docstring for why).
- The lookahead property test runs every detector on `bars[: t + 1]` across 8 seeded random
  walks and compares against the full run filtered to `confirmed <= t`.
  - **Mutation-checked:** stamping swing highs as confirmed when they occur fails the test on
    all 8 seeds.
  - The restored code passes, along with 31 module tests in total. The full backend suite
    passed (1382), and ruff is clean.
- Judgment calls:
  - Ties: a swing is strictly beyond its left window and at least level with its right window,
    so a flat top gives one swing, at its first bar.
  - An equal high is `LH` and an equal low is `HL`.
  - Highs and lows share zone clusters, so a broken resistance can become support.
  - A swing whose ATR is still `NaN` is left out of zones rather than clustered against a
    tolerance that was made up.
  - A close through a swing retires it for `sweeps`, because it has become a `breaks` event.
- Not yet run on real bars. T147 is the first consumer.

### T143 — code landed 2026-10-08; homeserver acceptance open

- `docker/mt5/` contains:
  - the image, ported from carbon-copy (same Wine, Python 3.13.16 and `MetaTrader5==5.0.6231`);
  - `entrypoint.sh`, which logs in from `MT5_ACCOUNT` / `MT5_PASSWORD` / `MT5_SERVER` in the
    stack's `.env`, with Experts and live trading disabled in the ini;
  - `bridge/server.py`, a TCP JSON-lines server on `:18812` serving `ping`, `account`,
    `symbols`, `spec`, `tick`, `rates_range` and `ticks_range`;
  - `healthcheck.py`, which fails on anything but a demo login.
- `backend/app/modules/broker/client.py` is the async client. Ten tests run the real server
  against the real client with a fake `MetaTrader5` module. They cover:
  - MT5's inclusive end bound being trimmed;
  - errors coming back as answers on a connection that stays usable;
  - reconnecting after the terminal restarts;
  - refusing the wrong account.

  An AST check fails if the bridge ever names an `order_*`, `orders_*` or `positions_*` call.
- Compose: `mt5` sits behind the `broker` profile and has no host ports in prod. VNC is the
  opt-in `compose.mt5-vnc.yaml` on `127.0.0.1:5901`, since carbon-copy holds 5900. The template
  folder goes in `./mt5/` (gitignored).
- **Not verified:** the image has not been built yet. Docker Desktop was not running on the dev
  machine, so the homeserver build is the first one. Nothing has been checked against a real
  terminal.
- Still to do on the homeserver:
  - the image builds;
  - the container turns healthy;
  - `account` reports the Axi demo login with `trade_mode == "demo"`;
  - `rates_range` returns M1 bars for the four symbols, with their Axi names recorded here.

**First contact on the homeserver, 2026-10-08:**
- The first login failed because `MT5_SERVER` was mis-capitalised. The user fixed it, logged in
  over VNC, and the bridge then attached.
- Before the fix, the bridge's 60 s `initialize` outlasted the healthcheck, and the late answer
  hit a closed socket. Fixed in `8f029e5`: initialize now waits 10 s and the healthcheck 25 s.
- `account` returned login `10067647` on `Axi-US50-Demo`, `trade_mode: demo`, leverage 1:1000.
  The user says live leverage is the same. `trade_allowed` is false, but that comes from
  Experts being disabled; this demo account has no investor password.
- Symbols matched by the first search: `US30`, `US500`, `NAS100.fs`, `XAUUSD`, `XAUUSD-PERP`
  and gold crosses. **The futures-based S&P and Dow names had not been found yet.** They are not
  `US500.fs` or `US30.fs`.

### T144 — code landed 2026-10-08; homeserver acceptance open

- Code:
  - `app/modules/broker/servertime.py` (the convention, pure);
  - `tables.py` (`bars`, `symbol_specs`, `tick_files`, `clock_checks`, plus `ensure_schema`
    with the read-only grants);
  - `ingest.py`;
  - `app/workers/broker_ingest.py`;
  - the `broker-ingest` service in compose, behind the `broker` profile.
- Defaults: `BROKER_SYMBOLS=S&P.fs,NAS100.fs,DJ30.fs,XAUUSD,US2000`. US2000 is for price
  action only and has no desk counterpart. `S&P.fs` is stored under the slug `s-p.fs` in
  Parquet paths.
- History depth is discovered. The first run takes a week, then the worker walks back a week
  per request until two empty chunks in a row, and repeats that walk once a day.
- 17 tests run on SQLite against a fake bridge with labelled synthetic bars. They cover:
  - US DST dates;
  - New York close = server midnight;
  - round trips across both switches;
  - the gate: nothing before a confirmation, nothing after a disagreement, no measurement
    from stale ticks;
  - the forming bar being skipped;
  - the backward walk stopping;
  - a backfill across 2026-11-01 converting the weekend to 50 h 01 m, i.e. DST honoured;
  - changed bars being logged;
  - specs written only on change;
  - ticks written once per closed hour.

  The full backend suite passes (1409), and ruff is clean.
- A bug the tests caught: `offset_s` was computed as if `ts` were held in nanoseconds, but this
  pandas version keeps seconds. It now uses a unit-independent subtraction.
- **Not verified:** the Postgres paths (`CREATE SCHEMA`, the grants, `ON CONFLICT` on
  Postgres), and everything against the real bridge. Both get their first run on the
  homeserver.
- **A likely first-contact failure:** MT5's *Max bars in chart* setting (Tools → Options →
  Charts). The API serves only as many bars as a chart may hold, which by default is about
  100k, roughly three months of M1. If the backward walk stops at about three months, raise it
  to *Unlimited* over VNC and the next day's walk continues.
- Still to do on the homeserver:
  - `broker.clock_checks` shows `agrees = true`;
  - bars arrive each minute for all five symbols;
  - the depth found is recorded here;
  - `S&P.fs` against SPX 5-minute bars lines up at lag 0;
  - 24 hours with no gap beyond the session break.

**T144 on the homeserver, 2026-10-08 evening.** Deployed through `scripts/deploy.sh --no-pull
broker-ingest`. No other service was rebuilt.

- **Clock:** confirmed, measured 10800 s against the convention's 10800 s.
- **Alignment:** `S&P.fs` 5-minute returns correlate 0.79 with the desk's SPX bars at lag 0,
  against −0.05 to 0.10 at ±5 and ±60 minutes.
- **Specs** (from the bridge):

  | | S&P.fs | NAS100.fs | DJ30.fs | XAUUSD | US2000 |
  |---|---|---|---|---|---|
  | Min lot | 0.01 | 0.01 | 0.01 | 0.01 | 0.1 (cash) |
  | $ per point at 1 lot | 50 | 20 | 5 | 100 | 1 |
  | Swap long / short | 0 / 0 | 0 / 0 | 0 / 0 | −61.6 / +40.5 | −7.0 / +1.0 |

  The `.fs` contracts report `expiration_time = 0`, so **rolls must be detected from price**,
  not from the spec (T145).
- **Spreads** (M1 minimum per bar, 10:00–16:00 ET): 0.90 / 2.50 / 4.00 points and $0.16 for
  gold. The median equals the 90th percentile. Spikes have to come from the ticks.
- **Five first-contact bugs found and fixed:**
  1. `MaxBars` set in the GUI does not survive a restart, so it now goes in the startup ini.
  2. MT5 answers a range it has not downloaded with nothing, so empty chunks are retried.
  3. An unwritable `/data/broker` crash-looped the worker. It is now owned by uid 10001, and
     tick-write errors are non-fatal.
  4. The unbounded walk froze live bars for 1.5 h, so it is now about 3 months per symbol per run.
  5. **Before 2019-07-17, MT5 answers an M1 request for `S&P.fs` with one bar per day.**
     6,625 such rows (2008-10-11 → 2019-07-17) were stored as minutes and then deleted by
     hand. The walk now stops at days with fewer than 30 bars.
- **Depth:** `S&P.fs` has real M1 from **2019-07-18**, about 2.5M bars. The other four were
  still walking at the time of writing.

### T145 — landed 2026-10-08

- Code: `app/modules/broker/basis.py` (pure: `measure`, `detect_rolls`, `translate`, the five
  pairs) and `basis_job.py`, which recomputes and replaces `broker.basis` and `broker.rolls`
  every hour inside `broker-ingest`. The `broker.levels` view translates every
  `gex.gex_levels` row with the newest basis at or before `captured_at`.
- **The deliverable changed: a view instead of an MCP tool.** A new tool would mean deploying the
  MCP server, which runs in the backend image, from this branch, and decision 11 keeps that on
  `main`. `query_sql` on `broker.levels` gives the same answer, and the tool can follow at merge.
- The basis is measured at the 16:00 ET close (the CFD's 15:59 M1 bar) on every day since
  2021-09, and at every desk 5-minute close since 2026-09-11. Candidate early-close days are
  left out, because the desk's calendar has no early closes.
- **Rolls, against real data:**
  - Steps above 0.45 % in log(cfd/desk) that also persist (3-observation medians differ by at
    least 80 % of the threshold).
  - SPX/`S&P.fs`: **17 of 17 quarterly rolls 2022-09 → 2026-09 found, none spurious**, all on
    roll Mondays, +0.49 % to +1.24 %.
  - `NAS100.fs` (from QQQ): 17 rolls. `DJ30.fs` (from DIA): 16 rolls from 2022-12 (its M1
    history was still being walked). `XAUUSD`: none, as expected for spot.
  - Before 2022-09, near-zero rates made the step about 0.15 %, indistinguishable from noise and
    too small to matter.
  - The persistence test rejected the one bad-print pair (see below).
- **Stability:** within a session the 5-minute log ratio has a standard deviation of
  0.007–0.009 % for every pair (worst day 0.019 %, SPY), about 0.6 points on `S&P.fs`.
  Daily-close basis noise is larger (±3–7 points) because the desk's daily close and the CFD's
  15:59 bar are not quite the same print. For translation, prefer the 5-minute basis, which the
  view does whenever one exists.
- Spot check: the SPX snapshot at 2026-10-08 20:20 UTC had spot 7,765.36 and offset +51.57,
  giving 7,816.93. `S&P.fs` closed at 7,817.
- **Roll placement on the 5-minute grid is untested on real data.** The desk has no 5-minute bars
  for 2026-09-14 → 09-18 (the known September opex outage), so the September roll is placed at
  daily resolution. The first real test is the December roll on 2026-12-14.
- **Not done:** the GLD → XAUUSD ratio against a hand-entered T41 spot. It needs the user to
  supply one.
- **Found in desk data, outside this initiative:** `gex.daily_bars` SPX closes on **2025-04-09**
  and **2026-06-26** disagree with SPY by 0.88 % and 0.54 %. Every other SPX/SPY deviation over
  0.2 % is an SPY ex-dividend date. Every scan module reads these bars, so this belongs in
  `TASKS.md` on `main`.
