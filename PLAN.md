# GEX Trading App — Build Plan

Scope agreed on 2026-09-04:

- Instruments: US index options — SPX (incl. SPXW weeklies), SPY, QQQ. **Added 2026-09-05
  (T38):** GLD and DIA. GLD is a commodity ETF (a gold bullion trust), not an index option,
  but the user asked for gold exposure and the Cboe delayed-quotes feed serves its options
  chain identically to SPY/QQQ (bare-ticker URL, P.M.-settled, single vendor root) — no
  engine or schema change was needed, so it was in scope for a one-task addition. DIA is the
  Dow ETF, added for the same reason. DIA's net GEX is small relative to its gross (0.9 %)
  and sensitive to the carry parameter (see §1's Recommendation and `docs/validation.md`)
  until T33 fits the carry from parity; its per-strike walls are unaffected.
- Freshness: end-of-day first, then 15-min delayed intraday, then real-time.
- Purpose: analysis and charts only. **No order routing, ever.**

  **Amended 2026-09-05 (T39).** This line originally read "analysis and charts only" with no
  qualification, and the report view the user asked for on 2026-09-05 — an "options
  intelligence" report with a premium-selling screen and a trading playbook — cuts against it.
  The supervisor flagged the contradiction rather than letting it stand silently, and the user
  confirmed they want those sections. So the scope is now stated precisely:

  - The app **emits trade suggestions**: screening output computed from the current chain
    ("these quoted contracts sit beyond the computed walls in this DTE window") and
    deterministic scenarios built from computed levels ("this wall is the level an upside
    break would cross"). `app/gex/report.py` produces them and every renderer labels them as
    screening output, never as recommendations.
  - The app **still never routes, places, modifies or cancels an order**, and holds no broker
    credentials. That half of the line is not negotiable and is not weakened by the above.
  - Every number in a suggestion traces to a computed level. Where no computed level exists,
    the field is null and the UI shows a dash — the report never fills a target or a stop with
    a percentage of spot or a rule of thumb (see `app/gex/report.py`'s module docstring for
    the example-report failures this rule was written against).
- Stack: Python backend + React frontend. Data budget under $50/month.

---

## 1. Data sources

### What GEX actually needs

Per contract: strike, expiry, type, open interest (OI), implied volatility (IV), gamma (or enough to compute it: spot, rate, time to expiry), plus spot price. Everything else is optional.

Two facts drive the source choice:

1. **OI is published once per day** (by OCC, overnight). No feed gives intraday OI. "Real-time GEX" means re-pricing gamma in real time against yesterday's OI, optionally adjusted by today's volume.
2. **Data delayed more than 15 minutes carries no OPRA license fee.** That is why delayed chains are free or nearly free and real-time is not.

### Verified sources (checked 2026-09-04)

| Source | Cost | Latency | OI | Greeks/IV | History | Notes |
|---|---|---|---|---|---|---|
| **Cboe delayed quotes JSON** (`cdn.cboe.com/api/global/delayed_quotes/options/{_SPX,SPY,QQQ}.json`) | $0, no key | 15 min | Yes | Yes (delta, gamma, vega, theta, IV) | None. Must self-capture | Verified live: 28,650 SPX contracts across 56 expiries, 12,456 SPY, 11,006 QQQ in one call. Unofficial endpoint, no SLA |
| MarketData.app Free | $0 | 24 h | Yes | Yes | 1 yr | 100 credits/day. Cached chain call = 1 credit. Enough for 3 symbols EOD. Official API, key required |
| Tradier Sandbox | $0 | 15 min | Yes | Yes (ORATS, hourly) | No | Requires signup. Good second delayed source |
| MarketData.app Starter | $12/mo annual, $30 monthly | 15 min options | Yes | Yes | 5 yr | 10k credits/day |
| Massive (ex-Polygon) Options Starter | $29/mo | 15 min | Yes | Yes | Yes | Official, unlimited calls, snapshot endpoint |
| **Tradier Brokerage account** | $0 data with funded account; Tradier Pro $10/mo | **Real-time** (WebSocket) | Yes | IV/Greeks hourly (ORATS) | No | Cheapest real-time path. Must open a brokerage account. Compute gamma locally |
| ThetaData Options Value | $40/mo | Real-time | Yes | IV only | 1-min from 2020 | |
| **ThetaData Options Standard** | $80/mo | Real-time, 10k streamed contracts | Yes | 1st/2nd/3rd order | Tick from 2016 | Best research-grade option. Retail/personal license |
| Alpaca Basic (indicative) | $0 | 15 min, derived quotes | **No** (market data API omits OI; trading API's `/v2/options/contracts` carries it separately) | Yes | Feb 2024+ | Evaluated 2026-09-11. Free paper account. Two-API join for OI. Index-option market-data coverage undocumented |
| Alpaca Algo Trader Plus | $99/mo | Real-time (OPRA) | **No**, same split as above | Yes | Feb 2024+ | Over budget, and loses to ThetaData Standard at $80. See `plans/continuous-feed/04-realtime-paid.md` |
| Massive Options Advanced | $199/mo | Real-time | Yes | Yes | Yes | |
| Databento OPRA Standard | $199/mo | Real-time | No (trades/quotes only) | No | PAYG history | Raw feed, overkill here |
| Cboe DataShop EOD | $400/request | EOD | Yes | Optional | 2018+ | Institutional pricing |
| Unusual Whales / SpotGamma | $50+/mo, API extra | Near real-time | Pre-computed GEX | | | Buys the answer, not the data. Useful as a benchmark only |

### Recommendation

**Free (phases 1–4): Cboe delayed JSON, with MarketData.app Free as fallback.**
The Cboe endpoint returns exactly the fields the GEX engine needs for all three symbols, 15 minutes delayed, with no key. A quick naive net-GEX computed from it during research came out at roughly +52 B USD per 1% move for SPX, which is in the range published by commercial GEX vendors, so the data is fit for purpose. Risks: it is undocumented and could change or be rate-limited. Mitigation: poll no more than once per symbol per 15 minutes, keep a provider abstraction so a swap is one class, and store every snapshot so history accumulates from day one.

**Paid, within budget (phase 5): Tradier brokerage account, $0–10/month.**
Real-time options quotes over WebSocket are included for account holders. That is the only real-time OPRA path under $50. Trade-off: Greeks are refreshed hourly, so the app computes gamma itself from live quotes and IV (which it should do anyway for the gamma profile). Eligibility confirmed: Tradier's permitted-countries list includes the Dominican Republic.

**Rejected as a primary source (2026-09-11): Alpaca.** Its market data API does not return open
interest at all — OI lives on the trading API's contracts endpoint, so a provider would be a
two-API join, and under invariant 3 a partial join failure deflates GEX silently rather than
failing loudly. Real OPRA is $99/month, double the budget and worse value than ThetaData
Standard at $80. Index-option market-data coverage is undocumented and SPX is the headline
symbol. Retained only as a possible second *free delayed* source; full finding and the two
questions a spike would have to answer are in `plans/continuous-feed/04-realtime-paid.md`.

**Paid, worth it if budget grows: ThetaData Options Standard, $80/month.**
Real-time streaming plus tick history to 2016 with full Greeks. This is what unlocks backtesting GEX levels against realized moves and building intraday gamma-profile history without waiting months of self-capture. Not needed until you want research, not just a dashboard.

**Licensing note.** The app is single-user (the owner only), so real-time data falls under OPRA "non-professional" use at about $1.25/month, passed through by the vendor. No redistribution license is needed. Delayed data has no fee at all.

---

## 2. Architecture

```
gex-trading/
  backend/                 Python 3.12, FastAPI
    app/
      providers/           base.py (OptionChainProvider), cboe.py, marketdata.py, tradier.py, thetadata.py
      models/              SQLAlchemy models: snapshots, contracts, levels
      gex/                 engine.py (pure functions), greeks.py (Black-Scholes / Black-76), levels.py
      api/                 routers: chains, gex, levels, history, stream
      jobs/                scheduler (APScheduler): EOD snapshot, intraday poll
      storage/             Parquet writer/reader for raw chains
    tests/
  frontend/                React 18 + Vite + TypeScript
    src/
      api/                 TanStack Query hooks
      components/          GexByStrike, GammaProfile, KeyLevels, ExpiryFilter, LevelHistory, PriceChart
      pages/               Dashboard, History, Settings
  docker-compose.yml       postgres, backend, frontend
  PLAN.md
```

Key design choices:

- **Provider abstraction.** One interface, `fetch_chain(symbol) -> ChainSnapshot`, with a normalized contract schema. Swapping free to paid, or delayed to real-time, is a config change.
- **Storage split.** Raw chain snapshots go to Parquet files on disk (cheap, columnar, trivially backfilled). Postgres holds computed results: per-strike GEX, key levels, and the snapshot index. Timescale is not needed at this volume.
- **GEX engine is pure Python/NumPy** with no I/O, so it is unit-testable and reusable for backtests.
- **Compute Greeks locally** from IV, spot, rate and DTE (Black-Scholes for SPY/QQQ, Black-76 style on forward for SPX). Vendor gamma is used as a cross-check only. This is required for the gamma profile (gamma at hypothetical spot levels) and for real-time recomputation later.
- **Scheduling in-process** with APScheduler. No Celery/Redis until there is a reason.
- **Frontend charts:** ECharts for GEX bars and profile curves; TradingView Lightweight Charts for the price panel with levels overlaid.

---

## 3. GEX methodology (what the engine computes)

Per snapshot and symbol:

1. **Per-contract GEX** = gamma × OI × contract multiplier × spot² × 0.01 (dollar gamma per 1% move). Sign convention: calls positive, puts negative (dealers assumed long calls, short puts). Also keep an "absolute" variant.
2. **Per-strike net GEX** and **per-expiry net GEX**, with filters: 0DTE, this week, monthly, all. SPX and SPXW roots merged.
3. **Key levels:** call wall (max positive strike GEX), put wall (max negative), largest absolute gamma strike, and the **gamma flip / zero-gamma level** found by evaluating the total gamma profile across a grid of hypothetical spot prices and locating the sign change.
4. **Gamma profile curve:** total dealer gamma as a function of spot, computed by re-pricing gamma at each grid point with each contract's IV. Shown for all expiries and ex-0DTE.
5. **Stretch (phase 4+):** vanna and charm exposure, volume-weighted intraday GEX adjustment, level-vs-realized-move statistics.

Validation: unit tests against hand-computed Black-Scholes values; sanity comparison of net GEX and flip point against publicly displayed figures from Unusual Whales / SpotGamma free pages.

---

## 4. Phases

### Phase 0 — Scaffold (1 day)
- Repo layout above, `pyproject.toml` (uv), `docker-compose.yml`, `.env.example`, linting (ruff, eslint), pytest and vitest wired up, README.

### Phase 1 — Ingestion, EOD (2–3 days)
- `CboeDelayedProvider` and `MarketDataAppProvider` behind the base interface.
- Normalized `ChainSnapshot` model; Parquet writer; snapshot index table.
- Scheduler job: capture SPX/SPY/QQQ at 16:20 ET daily (after the 15-min delay clears the close), plus a manual `POST /snapshots/capture`.
- Start this job as early as possible so history accumulates.

### Phase 2 — GEX engine (2–3 days)
- `greeks.py`: BS/Black-76 price, delta, gamma, vanna, charm; vectorized with NumPy.
- `engine.py`: per-contract, per-strike, per-expiry GEX; expiry filters; gamma profile grid; flip point; walls.
- Persist computed levels per snapshot. Tests.

### Phase 3 — API and dashboard (3–4 days)
- Endpoints: latest snapshot, GEX by strike, profile, levels, level history, snapshot list.
- React dashboard: symbol switcher, expiry filter, GEX-by-strike bar chart, gamma profile with flip point marked, key-levels panel, price chart with walls/flip overlaid, history table of levels by date.

### Phase 4 — Delayed intraday (2 days)
- Poll Cboe every 15 min during regular hours; store intraday snapshots.
- Server-Sent Events endpoint pushes new computations to the frontend.
- Intraday timeline view: how walls and flip point moved through the session.

### Phase 5 — Real-time (3–5 days, requires paid source)
- `TradierProvider` with WebSocket quotes (or `ThetaDataProvider`).
- Real-time loop: keep the day's OI fixed, update IV/spot from the stream, recompute gamma and levels on a 1–5 s cadence, push via SSE/WebSocket.
- Optional: volume-based intraday OI adjustment.

### Phase 6 — Research (ongoing, optional)
- Backtest harness over accumulated snapshots (or ThetaData history): distance to flip vs. next-day realized range, wall hit rates, 0DTE profile behavior.

---

## 5. Resolved and open items

- Residence: Dominican Republic. Tradier accepts accounts from there, so Phase 5 can use Tradier.
- Users: owner only. Non-professional OPRA status applies; no redistribution licensing.
- Open: whether historical backtesting justifies ThetaData Standard at $80/month, above the stated budget. Decide after Phase 4.

## 6. Execution

Work is delegated to Sonnet agents per `TASKS.md`. Tasks that need deeper reasoning are marked for Opus there.
