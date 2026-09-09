# 00 · Foundation: daily bars

## Goal

Four of the six continuation tools (breakout ledger, trend scorer, rotation, cross-asset strip)
need daily OHLCV for 60–80 symbols, most of which have no option chain in this app. Build the
ingestion once, behind a provider interface, so every later tool consumes one repository
function and never fetches anything itself.

## What the user sees

Nothing directly. `GET /api/bars/{symbol}?start=&end=` and `GET /api/universe` exist for the
pages that follow, and `/api/health/capture` gains a `bars` block (last bar date per symbol,
count of stale symbols) so a broken bars job is visible the same way a broken capture is.

## Data

Default provider **Yahoo Finance chart endpoint** (free, keyless, JSON):
`https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d&includeAdjustedClose=true`.
Optional second provider **Tiingo** (free tier, token, 50 symbols/hour) selected by
`BARS_PROVIDER=tiingo`, mirroring how `PROVIDER` and `MARKETDATA_TOKEN` work today.

### Live verification, 2026-09-09 (supervisor)

**Stooq is dead as a keyless source and is NOT to be implemented.** `stooq.com/q/d/l/` and the
`stooq.pl` mirror both answer **HTTP 200 with `Content-Type: text/html`** and a JavaScript
proof-of-work browser-verification page (`<noscript>This site requires JavaScript to verify
your browser.</noscript>` plus a SHA-256 grinding loop that POSTs to `/__verify`), for
`spy.us` and `%5Espx` alike, with and without a browser `User-Agent`. Do not build a
challenge solver. Do not add a `stooq.py`.

Yahoo was verified live in its place, against SPY, `^GSPC`, `^VIX`, XLK, GDX, EWZ, XLRE, IWM,
UNG, FXY and ITB. The record the T42 module docstring must carry, and which the agent should
re-confirm with one request rather than rediscover:

- **Success shape.** HTTP 200, `application/json`. `chart.result[0]` holds `meta`,
  `timestamp` (a list of epoch seconds) and `indicators.quote[0]` with parallel
  `open/high/low/close/volume` lists, plus `indicators.adjclose[0].adjclose` when
  `includeAdjustedClose=true`. `range=5y&interval=1d` returned **1255 rows** for SPY.
- **Adjustment.** `close` is **split-adjusted, not dividend-adjusted**; `adjclose` is both.
  SPY 2021-09-09 read `close` 448.98 against `adjclose` 419.63. Store the OHLCV block as
  served — that is the split-adjusted series the plan wants — and set `source` to
  `yahoo-splitadj`. Do not store `adjclose`, and never mix the two for one symbol.
- **Unknown symbol.** HTTP **404** with a structured body: `chart.result` is `null` and
  `chart.error` is `{"code": "Not Found", "description": "No data found, symbol may be
  delisted"}`. Map to `SymbolNotSupported`, not to a rate-limit error. A non-200 whose body
  is not that shape, and any 429, maps to `UpstreamUnavailable`.
- **The last row is today's bar, and it is partial -- while the session is still open.**
  Probed intraday on 2026-09-09, SPY's final row carried `volume` 3,792,889 against a normal
  full day of ~44,000,000, and `^GSPC` 371,359,355 against ~4,966,930,000. **The provider must
  drop a bar dated strictly after today outright, and must drop today's own bar only while
  today's session has not yet settled** (NY-local now before `MARKET_CLOSE` plus a short
  settle buffer, or today is not a trading day at all) -- import `MARKET_CLOSE` and
  `is_trading_day` from `app.jobs.calendar` rather than reimplementing market-hours logic.
  The naive rule ("drop any bar whose date is not strictly before the current NY trading
  date") looks right against an intraday probe but is wrong for the job that actually runs:
  the 17:30 ET job fires 90 minutes *after* the close, when today's row is the complete,
  final bar, so an unconditional drop would leave `daily_bars` permanently one trading day
  stale on every single run, forever -- silently pairing every later join against same-day
  option data with yesterday's price action. This is the single most damaging failure mode in
  the task and needs its own tests covering: before the close, inside the settle buffer,
  after the settle buffer (admitted), and a non-trading day (dropped regardless of time).
- **Dates come from the exchange timezone, not from UTC.** Timestamps are session-open
  instants: SPY rows are stamped 13:30 UTC (09:30 ET) and `^VIX` rows 07:00 UTC under
  `exchangeTimezoneName: America/Chicago`. Converting an epoch to a UTC date happens to work
  for the ET names today and silently breaks on any row stamped after 19:00 ET. Derive
  `date` by converting the epoch into `meta.exchangeTimezoneName` and taking `.date()`.
- **`^VIX` volume is always 0.** Treat `volume` as nullable, and do not read a zero as a
  data error.
- **SPX is `^GSPC` on Yahoo**, not `^spx`. `^SPX` is not a Yahoo symbol.
- **`^VIX` carries bars on equity-market holidays.** Confirmed against the real 5-year
  backfill (2026-09-09): `^VIX` returned 1255 rows where SPY, SPX and GLD each returned 1253,
  and the two extra dates are **2026-05-25 (Memorial Day)** and **2026-09-07 (Labor Day)** —
  days the equity market was closed. `^VIX` covers every date SPY has, so nothing is missing;
  the series simply is not date-aligned with the equity ones. Any math that joins `^VIX`
  against an equity series — T54's correlation and term structure above all — must align on an
  explicit shared date index rather than assuming equal-length arrays.

  **Do not "fix" this by filtering non-trading days inside the provider.** `is_trading_day`
  resolves an unknown year as *not a holiday* and logs at ERROR every time it is asked (see
  `app/jobs/calendar.py`'s module docstring), and its `_HOLIDAYS_BY_YEAR` covers 2026–2027
  only — so a 5-year backfill would both fail to drop pre-2026 holiday rows and emit thousands
  of ERROR lines per symbol. Extending the calendar back to the earliest backfilled year is
  the prerequisite for any provider-side filter; until then, align downstream.
- **Yahoo also drops null-OHLC rows on those same closed days.** The live backfill skipped 19
  `^VIX` rows over five years with a `null` in the parallel OHLC arrays. Skipping (never
  storing a fabricated `0.0`) is correct and cost no real trading day.

Yahoo's chart endpoint is undocumented and carries no ToS blessing for programmatic use; the
user accepted that trade-off on 2026-09-09 over requiring a Tiingo signup. It is exactly why
the provider ABC below is not optional: when Yahoo goes the way Stooq just did, the
replacement must be a new module plus a `BARS_PROVIDER` change, and nothing else.

Storage is **Postgres**, a new `daily_bars` table. Invariant 5 (per-contract rows only in
Parquet) is about option contracts; bars are a different, small dataset (80 symbols × 5 years
≈ 100k rows) that every scan query will filter and join, which is what Postgres is for.

## Design decisions

- **Provider routing by symbol group, not one global provider.** T54 adds a Cboe index-history
  provider for `^VIX`-style series where a vendor-native index history is preferable. `BarProviderRegistry.for_symbol()`
  resolves a symbol to a provider using a config map of group → provider; the default group is
  everything. Keep it to that: no plugin discovery.
- **Callers use plain tickers** (`SPY`, `^VIX`, `SPX`). Any vendor symbol mapping — Yahoo's
  `SPX` -> `^GSPC` — lives inside the Yahoo provider and nowhere else. `symbol` as stored in
  `daily_bars` and as accepted by `/api/bars` is always the plain ticker.
- **Adjustment policy is a judgment call the agent must make and document.** Breakout and
  trend math needs split-adjusted prices; dividend adjustment changes highs and lows slightly
  and makes stored prices disagree with the user's chart. **Settled by the verification above:**
  store Yahoo's `close` (split-adjusted, not dividend-adjusted), record it as `yahoo-splitadj`
  in the `source` column, ignore `adjclose`, and never mix adjustment types for one symbol.
- **Missing day ≠ holiday.** Use `app/jobs/calendar.py` to tell an expected-but-missing bar
  from a market holiday, and surface the former in the health block.
- **Incremental by default.** The job fetches from the last stored date minus 5 days (to pick
  up vendor revisions) and upserts on `(symbol, date)`.
- **Never inside the 16:20 capture.** The bars job is its own scheduler entry at 17:30 ET
  weekdays. A bars failure must be incapable of delaying or breaking the option capture, which
  is the P0 guardrail.

## Tasks

### T42 · Opus · T01, T05, T31
**Daily bars: provider interface, Yahoo implementation, table, job, backfill, API**

Paths: `backend/app/providers/bars.py` (ABC + registry), `backend/app/providers/yahoo.py`,
`backend/app/providers/tiingo.py` (only the token-guarded skeleton; a working implementation
is welcome but not required), `backend/app/models/bars.py` (`DailyBar` Pydantic model:
`symbol, date, open, high, low, close, volume, source`; `date` is a `datetime.date`, not a
datetime, because a daily bar has no instant), `backend/app/models/db.py` (add `DailyBar`
table `daily_bars`, unique `(symbol, date)`, index on `symbol`), one Alembic migration,
`backend/app/storage/bars_repository.py` (`upsert_bars`, `read_bars(symbol, start, end) ->
pd.DataFrame`, `last_bar_date(symbol)`, `read_universe_closes(symbols, start, end) -> wide
DataFrame`), `backend/app/jobs/bars.py` (`update_bars_job`, per-symbol try/except, one failure
never stops the loop, summary log line with counts), `backend/app/jobs/scheduler.py`
(register at 17:30 ET mon–fri, additive), `backend/app/bars_backfill.py` CLI
(`uv run python -m app.bars_backfill --years 5 [--symbols ...]`), `backend/app/api/bars.py`
(mount under `/api`), `backend/app/config.py` (`BARS_PROVIDER: str = "yahoo"`,
`TIINGO_TOKEN: str = ""`, `SCAN_UNIVERSE: str = <default below>`, `BAR_PROVIDER_GROUPS: str = ""`),
`backend/app/api/health.py` (additive `bars` block), tests under `backend/tests/`.

Default `SCAN_UNIVERSE` (the user may widen it; see the initiative README):

```
SPY,QQQ,DIA,IWM,RSP,
XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC,
SMH,XBI,KRE,XOP,ITB,XHB,XRT,IGV,ARKK,JETS,
GLD,SLV,USO,UNG,DBA,GDX,COPX,
TLT,IEF,HYG,UUP,FXE,FXY,
EEM,EFA,FXI,EWJ,EWZ,EWG
```

The five option underlyings are in the universe under their ETF proxies; `SPX` itself is
fetched (as `^GSPC`, inside the provider) so the regime board can use index bars. Add `SPX`
and `^VIX` to the default `SCAN_UNIVERSE` string above — T54 needs the latter and the Yahoo
provider already serves it.

Constraints: work only in the paths listed; do not modify `capture_eod_job`,
`capture_eod_safety_net_job`, `app/gex/*`, or any existing table. Provider selection must be
a config change, never a caller edit (invariant 6). Never kill processes by image name.

Acceptance: `uv run python -m app.bars_backfill --years 2 --symbols SPY,XLK,SPX` populates
rows and a second run inserts zero new rows; `GET /api/bars/SPY?start=2026-01-01` returns
them; `update_bars_job` with a provider that raises on one symbol still updates the others and
logs the failure; a test feeds a captured Yahoo payload whose final row is today's partial bar
through the parser and asserts that row is **not** returned; a test feeds the recorded 404
`chart.error` body through the parser and asserts `SymbolNotSupported` rather than a row; a
test asserts a `^VIX`-shaped payload with zero volume parses to a bar with null/zero volume
rather than raising; a test asserts an `America/Chicago` row lands on its exchange-local date;
`uv run pytest` and `ruff check .` pass; the module docstring carries the live-verification
record reproduced under *Data*.

## Verified facts (2026-09-09)

- `Settings` (`backend/app/config.py`) is a pydantic-settings class reading `.env`; `SYMBOLS`
  drives the option capture and must not be reused for the bars universe.
- Scheduler (`backend/app/jobs/scheduler.py`) registers jobs in `build_scheduler()` with
  `CronTrigger(day_of_week="mon-fri", hour=…, minute=…, timezone=_TZ)`; existing jobs at 16:20
  and 20:00 ET.
- Tables today: `snapshots`, `gex_levels`, `gex_by_strike`; Alembic versions live in
  `backend/alembic/versions/`. Tests run the same models on SQLite, so use only core column
  types (the `UTCDateTime` docstring in `models/db.py` explains why).
- `httpx` is already a dependency; the Cboe provider shows the injectable-client pattern tests
  rely on (`httpx.MockTransport`). Reuse it.
- Python is pinned `>=3.12,<3.13`; pandas, numpy, scipy, pyarrow available.

## Likely first-contact failures

- Yahoo throttling during a backfill of 45+ symbols. Backfill must be resumable (skip symbols
  whose `last_bar_date` is within 5 days) and rate-limited with a configurable sleep between
  requests. A 429 is `UpstreamUnavailable` and must not abort the remaining symbols.
- **Persisting today's partial bar -- and its mirror-image bug, dropping today's *settled*
  bar and leaving the store permanently a day stale.** See the verification record: the naive
  fix ("drop any bar dated on or after the current NY trading date") quietly corrupts every
  downstream scan in the opposite direction, because the bars job runs at 17:30 ET, after the
  close, when today's row is already final. Drop a bar dated after today outright; drop
  today's own bar only while its session has not yet settled (before `MARKET_CLOSE` plus a
  short buffer, or on a non-trading day).
- Deriving `date` from the UTC epoch instead of `meta.exchangeTimezoneName`.
- `^` in symbols breaking URL encoding or route matching. Route as `/api/bars/{symbol:path}`
  or percent-encode; test both `SPY` and `^VIX`.
- Yahoo volumes for `^VIX` are zero; treat `volume` as nullable.
- Parallel `null`s inside `indicators.quote[0]` on a halted or untraded day — a row where any
  of open/high/low/close is `null` must be skipped, not stored as 0.
- Windows: `date` round-trip through SQLite as string; assert the type on read.

## Out of scope

Intraday bars, single-stock universe, any paid source, storing bars in Parquet.
