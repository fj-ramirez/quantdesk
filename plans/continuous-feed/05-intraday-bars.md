# Intraday bars (T74)

The user asked for continuous bars on 2026-09-11, after noticing that "bars through Sep 10"
during the Sep 11 session looks stale even though it is correct — `daily_bars` deliberately
holds settled sessions only.

## Goal

During a session, the app has a 5-minute bar series for the symbols it actually plots, and the
in-progress daily bar is available without corrupting the settled daily history that every
scan module reads.

## What the user sees

- Freshness on the symbol pages reads *today*, not the last close.
- Today's open/high/low/last and range, live.
- An intraday series to plot, and the data T72's live-spot overlay needs.

## Scope, decided with the user

- **Both halves**: store the 5-minute series *and* surface the in-progress daily bar.
- **Six symbols**: `SPX`, `SPY`, `QQQ`, `GLD`, `DIA`, `^VIX`. Six requests per poll, ~72/hour at
  a 5-minute cadence. The 125-symbol universe was rejected: ~1,500 requests/hour is where an
  unofficial endpoint starts throttling, and losing Yahoo would take *daily* bars down with it
  — the scan pipeline depends on the same provider.

## Verified facts (probed live 2026-09-11, 15:12–15:14 ET, a trading Friday)

These decide the design, so they were measured rather than assumed.

* **Yahoo's intraday chart data is effectively real-time, not delayed.** At 15:12:11 ET the last
  SPY row was stamped 15:12:11 ET. This also settles the open question in
  [03-live-spot-overlay.md](03-live-spot-overlay.md) — that plan required the delay to be
  measured before anything was labelled "live", and for these symbols it is genuinely live.
* **Settled buckets are interval-aligned; the trailing row is not a bar at all.** A `5m`/`1d`
  pull returned rows at 09:30:00, 09:35:00 … 15:10:00, and then one final row stamped
  **15:14:17 with `volume = 0`** and `close` equal to the current quote. That last row is a
  synthetic live-quote row Yahoo appends, not a five-minute bucket.
* **One fetch therefore yields both halves of this task**: the aligned rows are the series, and
  the unaligned trailing row is the live quote.
* **Row counts** per `1d` pull: SPY and `^GSPC` 70 rows at `5m`, 344 at `1m`. `^VIX` returns 142
  at `5m` because its session is longer.
* **`^VIX` is not on the ETF clock.** Its payload declares `exchangeTimezoneName:
  America/Chicago` and its rows begin 02:15 CT, not 09:30 ET. The existing daily provider
  already derives dates from `meta.exchangeTimezoneName` rather than UTC; the intraday path must
  do the same and must not assume ETF session bounds.

## Design decisions

**The unaligned trailing row is never stored as a bar.** Its `volume` is `0`, so persisting it
would corrupt any volume aggregation and put a fake bucket in the middle of a chart. It is
returned separately as the live quote. This is the intraday analogue of the daily provider's
"never persist the vendor's in-progress session" rule, and it is the same failure mode that
module's docstring calls the most damaging in T42.

**The newest *aligned* bucket is stored while it is still forming, and converges.** Unlike the
daily job — which gets one shot per day at 17:30 and so must drop a partial — this job re-polls
every five minutes and upserts on `(symbol, interval, ts)`, so the 15:10 bucket fetched at 15:14
is simply overwritten at 15:19 with its settled values. Storing it is what makes the series
continuous rather than five minutes behind.

**`daily_bars` is not touched.** The in-progress daily bar is *derived at read time* by
aggregating today's stored 5-minute buckets (first open, max high, min low, last close, summed
volume) and taking the live quote as the close when one is available. Writing a partial row into
`daily_bars` would poison ATR, realized vol, breakout levels and every same-day join — the
precise failure `providers/yahoo.py` has four dedicated tests guarding. The derived bar is
labelled as in-progress wherever it is shown, and the settled history stays settled.

**A separate table, not a widened `daily_bars`.** Different grain (timestamp, not date),
different retention profile, different provider path. Widening the existing table would put a
nullable `interval` on every one of its ~157k daily rows and make every existing query specify
it.

**Volume is small enough to need no retention rule.** Six symbols × ~80 buckets ≈ 500 rows per
session, ~125k/year — three orders of magnitude below what made `T32` necessary for
`gex_by_strike`. Revisit only if the symbol list grows.

**Its own flag, `INTRADAY_BARS_ENABLED`.** Not folded into `INTRADAY_ENABLED`: that governs
option-chain capture against Cboe, this polls Yahoo, and one being rate-limited or broken must
not force the other off.

## Tasks

### T74 · Opus · T42, T18

**Intraday bars: 5-minute series plus the in-progress daily bar**

- `IntradayBar` model + migration: `(symbol, interval, ts)` unique, indexed for
  "this symbol, this interval, ordered by time".
- `YahooBarProvider.fetch_intraday_bars` returning `(aligned bars, live quote | None)`.
- `upsert_intraday_bars` in `storage/bars_repository.py`, same shape as `upsert_bars`.
- `intraday_bars_update` job, every 5 minutes 09:30–16:05 NY, trading days only, gated on
  `INTRADAY_BARS_ENABLED`, with the same short-misfire policy as T18's capture job and for the
  same reason.
- `GET /api/bars/{symbol}/intraday` and a derived in-progress daily bar for the symbol views.

Acceptance: during a session, two polls five minutes apart leave one row per aligned bucket with
the newest bucket's values updated rather than duplicated; no row ever carries the unaligned
quote timestamp; `daily_bars` is unchanged by the job.

## Likely first-contact failures

- Storing the trailing quote row, giving every session a `volume = 0` bucket at a ragged
  timestamp.
- Assuming ETF session bounds for `^VIX` and dropping two thirds of its rows, or stamping them
  with the wrong date near midnight.
- Keying the upsert on a naive datetime and silently duplicating every bucket once the stored
  and fetched values disagree on tzinfo — `UTCDateTime` exists for exactly this (invariant 4).
- Polling all 125 scan symbols "while we're here" and losing the Yahoo endpoint for daily bars
  too.

## Out of scope

Feeding intraday bars into the scan modules. `breakouts`, `trend`, `regime` and `rotation` are
daily-timeframe by construction — ADX, efficiency ratio, CHOP and the RRG approximation all mean
something different on 5-minute data, and rewiring them is a separate decision with its own
research, not a side effect of having the data.

---

## Result — T74, shipped 2026-09-11

`app/models/bars.py` (`IntradayBar`, `LiveQuote`), `app/models/db.py` (`IntradayBar` table),
migration `179bee3e9455`, `YahooBarProvider.fetch_intraday_bars`,
`storage/bars_repository.py` (`upsert_intraday_bars`, `read_intraday_bars`),
`app/jobs/intraday_bars.py`, the scheduler job, and `GET /api/bars/{symbol}/intraday`. 24 new
tests; backend suite 979 passed (was 955), ruff clean.

**Shipped as designed.** The trailing live-quote row is split out by the provider and never
stored; the newest aligned bucket is stored and converges across polls; `daily_bars` is
untouched and the in-progress daily bar is derived at read time.

**Decisions made while building.**

- *`LiveQuote` is its own type, not an `IntradayBar` with a flag.* A shape that cannot be
  mistaken for a bar beats one that must be remembered not to be.
- *The alignment check is per row, not positional.* The quote row is always last in practice,
  but a vendor that ever inserted one mid-payload would otherwise smuggle it into the series.
  There is a test for the misplaced case.
- *The session bar is returned as `interval="1d-live"`, never as a `DailyBar`,* and the API
  gives it a separate `SessionBarOut` schema carrying `complete: false`. Nothing that consumes
  settled bars can accept it by accident, and a client renders the in-progress affordance from
  the payload rather than from knowing which endpoint it called.
- *An unknown interval raises* rather than guessing a divisor — guessing would silently
  misclassify every row as aligned or unaligned.
- *The exchange timezone is still resolved* even though bucket identity is an instant and needs
  no zone, because resolving it keeps the "unrecognized timezone means the payload is wrong"
  check the daily path relies on. `^VIX` arrives as `America/Chicago`.
- *No trading-day guard on the job,* only a window: on a holiday the vendor publishes no
  buckets and the upsert is a no-op, which is cheaper and less to get wrong than a second
  calendar check. Unlike an option capture there is no risk of storing a misleading row.
- *The window is 09:30–16:05,* wider than T18's 09:45–16:15, because bars are not delayed: the
  opening bucket is useful immediately, and 16:05 gives the vendor a few minutes to publish the
  bucket that closes the session.

**Verified live, 2026-09-11 15:20 ET.** First poll of all six symbols: SPX/SPY/QQQ/GLD/DIA 70
buckets each, `^VIX` 142 (longer session), inserted=70/142 with updated=0, zero unaligned rows
stored, and a live quote extracted for every symbol. An immediate second poll of SPY returned
inserted=0, updated=70 — the convergence property, demonstrated rather than asserted.
`GET /api/bars/SPY/intraday` then returned 70 buckets plus a session bar reading
O 764.72 / H 766.38 / L 763.60 / C 765.01 on 30,395,591 shares, `complete: false`,
`bucket_count: 70`, `last_bucket_ts: 2026-09-11T19:15:00Z`.

**Not done, and deliberately.** The frontend still reads the settled daily series. Wiring
`session_bar` into the symbol views and charting the 5-minute series is UI work that was not
part of getting the data right, and it is the obvious next step.
