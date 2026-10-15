# GEX intraday backfill — years of 15-minute levels, so the level rules get a stage 1 (2026-10-14)

Three tasks, `T165`–`T167`. Approved in session 2026-10-14.

## Why

flip-cross, wall-bounce and wall-break (T164) are frozen rules on **in-session** gamma levels.
Their docs say "no stage 1 possible": the desk has 14 sessions of intraday captures, and their
only route to evidence is a forward record that needs months to reach 50 demo trades.

ThetaData Options Standard ($80/month, bought 2026-09-24, already paid) includes **10 years**
of intraday first-order greeks. That is enough to rebuild the levels at the live capture's own
cadence and run each frozen rule **once** over years of history. No new spend.

## What the subscription gives (checked 2026-10-14)

| Need | Source | Note |
|---|---|---|
| Per-contract IV + `underlying_price` at 15 min | `GET /v3/option/history/greeks/first_order`, `interval=15m`, `strike=*` | Standard tier. **One expiration per request**, multi-day ranges **≤ 1 month**, 4 concurrent requests per terminal |
| Open interest | `GET /v3/option/history/open_interest` | The value reported on D is D−1's close, as in the live capture (thetadata plan rule 3) |
| 5-minute desk-symbol bars | **No Stocks/Indices subscription.** Build them from **1-minute `underlying_price`** on one near-ATM contract per session | Close ≈ the last minute's mid. High/low = the max/min of five 1-minute mids, which **understates** true extremes (see *Known biases*) |
| CFD prices for P&L | `broker.bars` M1, since 2019 | Levels translated with `broker.basis`, as the live scores do |

The live capture runs **every 15 min, 26 captures a session**, on all six symbols (measured
2026-10-01 → 10-14). `interval=15m` matches that exactly.

## The rules

1. **Holdout.** Load **2023-01-03 → 2026-09-18** only. 2026-09-21 → 10-08 is where the
   flip-cross leaning was found, and everything after each logger went live is its forward
   record. Neither may appear in a backtest.
2. **A replay is not the live record.** Rebuilt levels and bars go to research-owned tables
   (`research.gex_intraday_levels`, `research.desk_bars_5m`), **never** `gex.snapshots`,
   `gex.gex_levels`, `gex.level_events` or `gex.decisions`. This is the thetadata plan's rule 5,
   applied to levels.
3. **Same engine, same detectors.** Levels come from `gex/engine.py` (gamma recomputed from IV,
   invariant 2; OI `None` excluded, invariant 3) under the `ALL` and `ZERO_DTE` filters. Events
   come from `scan/level_events.py` **unchanged**, fed the same `Capture` and bar shapes it gets
   live. A backfill-only detector would test a different rule.
4. **One run per frozen spec, declared first.** Each key is one row in
   `docs/edges/search-log.md` before it runs, and one EdgeLab trial. Nothing is tuned on the
   backfill. A change is a new rule, with a new row.
5. **The homeserver owns the terminal** (thetadata plan rule 1). The load runs there, outside
   09:15–16:30 ET and at weekends, and never from dev.

## Tasks

### T165 · Sonnet · T26
**Intraday history loader + a one-month SPY pilot.** `python -m app.modules.gex.gex.intraday_history
--symbols SPY --start --end`, beside T26's `gex/history.py` and reusing its client:

- for each session D and each expiry listed on D: `greeks/first_order` at `15m`, and OI
  reported on D;
- for each 15-minute stamp: build the chain, run the engine for `ALL` and `ZERO_DTE`, and
  write one row per (symbol, captured_at, filter) to `research.gex_intraday_levels` (flip, call
  wall, put wall, net GEX, `source='thetadata-history'`, `loader_version`). **Per-contract rows
  are not kept** (invariant 5 concerns live snapshots; a replay needs only the levels);
- 1-minute `underlying_price` from the nearest-expiry ATM contract → 5-minute OHLC in
  `research.desk_bars_5m`;
- resumable per (symbol, session), refuses to run inside 09:15–16:30 ET (T26's guard).

**Pilot:** SPY, 2026-09-25 → 10-14 (the sessions with live ThetaData captures, used **only** to
validate the levels, never to score events). Then one earlier month, timed.
**Acceptance:**
- the backfilled walls equal the live captures' walls on ≥ 90 % of matched 15-minute stamps;
- the median flip difference is ≤ 0.1 %;
- the 5-minute closes are within 1 bp of `gex.intraday_bars` on the overlap;
- requests, rows and wall-clock per session are recorded under *Result*, together with the
  projected time for the full load.

The thresholds are judgment, set before the pilot.

### T166 · Sonnet · T165
**Full load: 2023-01-03 → 2026-09-18**, all six symbols (IWM included), at the pilot's measured
pace, on the homeserver. Record under *Result*: sessions per symbol, sessions with no
`underlying_price`, and the first session with a 0DTE expiry per symbol. SPX has had dailies
since 2022 and SPY since late 2022; GLD and DIA have none, so their `_0DTE` keys stay empty.

### T167 · Opus · T166
**Stage 1 for the level rules.** One declared run each for `FLIP_CROSS_DOWN`, `FLIP_CROSS_UP`,
`PUT_WALL_BOUNCE`, `CALL_WALL_BOUNCE`, `PUT_WALL_BREAK` and `CALL_WALL_BREAK`, plus the `_0DTE` keys where data exists.

- Each key's frozen entry, exit and session window, applied character for character. The P&L
  is on the CFD's M1 bars, with the doubled recorded spread charged.
- The standard error is clustered by session (SPX, SPY and DIA fire together).
- IS 2023–24 against OOS 2025–26-09; years positive; the noise ceiling.
- Each run is recorded in `research.trials` and in the search log.
- The result moves each doc's *Stage* line: a stage-1 pass goes to demo with its gate
  unchanged, and a fail is written down and the rule stays where its forward record puts it.

## Known biases (state them with every result)

- **Touch under-detection.** A high/low built from 1-minute mids misses wicks between samples
  and the bid/ask excursion, so wall-bounce touches are found **less** often than live. Closes
  are unaffected, so flip-cross and wall-break are not.
- **No-dividend IV** (thetadata plan rule 4): an SPY net-GEX gap of about 9 % against Cboe at
  EOD. Walls identical, flip within 0.1 %. The T165 acceptance re-checks this intraday.
- **Listings with no OI yet** are excluded (invariant 3), exactly as live.
- **CFD basis on old dates** comes from `broker.basis`'s pairing at the time. Roll weeks carry
  the roll-gap caveat (invariant 9).

## Size (an estimate; T165 replaces it with a measurement)

SPX lists 50–60 expiries. At one request per expiry-month, 3¾ years × 6 symbols is roughly
10–15k requests at 4 concurrent, which means tens of hours spread over nights and weekends.
If that is too slow, cap expiries at ≤ 120 DTE and check the effect on walls in the pilot
before using it.

## Also unblocks

T25 (a GEX-level backtest, now over intraday levels) and T127 (the decision replay), both of
which read history this load provides.

## Result

### T165 pilot (2026-10-14, homeserver, after hours)

The loader is `app/modules/gex/gex/intraday_history.py`, and the check is
`scripts/intraday_pilot_validate.py`. Both were run inside the prod backend container from
`/tmp`, with nothing deployed. Output went to Parquet under `/data/research/intraday_pilot/`.
**No database tables yet:** the pilot needed no migration, and the tables come with T166.

**The terminal, probed live:**
- `greeks/first_order` history works at `interval=15m`: 27 stamps (09:30 → 16:00), columns as
  the live endpoint, back to at least 2023-01-03.
- `underlying_price` is filled for all six symbols. SPX is zero only at 09:30, before the index
  prints, and that stamp is skipped.
- One expiry per request; ranges up to a month, never past today. Today's OI is not in history
  yet (400), so a session is loadable from the next day on.
- 1-minute bars cost one request (0.1 s) per session.

**SPY, 2026-09-25 → 10-13 (13 sessions, 336 live captures matched):**

| Check | Result | Target |
|---|---|---|
| `ALL`: both walls equal live | **96.1 %** (call 98.2, put 97.9) | ≥ 90 % ✓ |
| `ALL`: flip \|diff\| | median **0.004 %**, p90 0.020 % | ≤ 0.1 % ✓ |
| `ALL`: net GEX | median ratio −0.1 % | — |
| 5-min closes vs `gex.intraday_bars` | median **0.13 bp**, p90 0.26 bp | ≤ 1 bp ✓ |
| highs / lows | understated by 0.78 bp median, 2.1–2.2 bp p90 | known bias; the touch tolerance is 5 bp |
| `ZERO_DTE`: both walls equal | **84.0 %** (call 95.0, put 88.4) | ≥ 90 % ✗ |

- **`ALL` passes.** That is the filter the frozen specs use. Its 13 misses are 0.7–2 % of spot:
  the wall alternating between two far-apart strikes of nearly equal gamma, not noise around one
  strike.
- **`ZERO_DTE` fails the 90 % bar.** Its misses are mostly one strike (median 0.13 % of spot,
  max 1.0 %). Live captures land 8–10 s after each boundary, so this is not timing. It is the
  near-equal 0DTE gamma of neighbouring strikes, ranked off slightly different IVs (snapshot
  against interval quotes). **Consequence for T167:** the `_0DTE` keys' backtest uses walls that
  are one strike off about one capture in six. Report that with any `_0DTE` result, or leave
  those keys to the forward record.
- **Bug found and fixed in the pilot:** a bar's close must be the sample stamped at its close
  (s+5), not the last one inside it. Before the fix, the closes were off by 1.6 bp median.
  `tests/test_intraday_history.py` pins it.

**Pace:** SPY **165–187 s a session** (40–48 requests, ~370k rows). The split between terminal
time and engine time was not measured. One 0DTE expiry alone took 9 s from the terminal.
