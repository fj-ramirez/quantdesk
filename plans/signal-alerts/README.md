# Signal alerts — every opportunity to a phone, for demo forward-testing (2026-10-06)

Three tasks, `T134`–`T136`. Approved in session 2026-10-06.

## Why

The desk is too young to have a confirmed edge (see `gex_track_record`, the noise ceiling and
the paper watchlist). The user's plan has two halves:

- **Backtest variants in NinjaTrader.** This is done outside this repo: `meta-trader-strategies`,
  `scripts/generate_edgelab_variants.py` generates one class per parameter combination (120
  z-score variants plus two continuation proxies). NinjaTrader is **backtest-only**.
- **Forward-test on demo accounts.** Every opportunity reaches Telegram, and **quantdesk owns
  every alert**, including the price-based ones. NinjaTrader sends nothing.

## The rules

1. **One channel, one sender.** All alerts go through `app.core.notify.send` (T104), which logs
   whether or not Telegram is configured and never raises. Nothing here may take a scheduler
   down.
2. **Changes only, one message per run.** A run with nothing new sends nothing. Variants that
   fire together are grouped into a single digest, never one message each.
3. **The same rule as the backtest.** The price signals call `research.strategies` (the code
   EdgeLab scores) and the decision engine's constants. Never a second implementation, or the
   forward test would test something nobody backtested.
4. **Every alert is recorded append-only**, so the forward test can be scored later the way the
   paper watchlist is. A Telegram message is not evidence; a row is.
5. **Caveats travel with the message.** Yahoo's continuous futures carry roll gaps that
   NinjaTrader's back-adjusted series does not, so a signal near a quarterly roll can disagree
   with the backtest. The digest says so.

## Tasks

### T134 · Opus · T104 — desk opportunities to Telegram
After the 17:45 ET decisions job records, send one message listing each **newly inserted**
`active` or `watch` opportunity: symbol, key, side, grade, entry / stop / target, and the
status. Rejected rows and re-runs that insert nothing send nothing. It is wired into
`record_decisions_job`, fenced like its other steps. Compose passes `TELEGRAM_BOT_TOKEN` /
`TELEGRAM_CHAT_ID` to `gex-capture` (today only `capture-watch` gets them).
Acceptance: tests for the message (new rows only, rejected excluded, empty run silent, notify
failure does not fail the job).

### T135 · Opus · T134 — price-signal engine and its record
A pure module that evaluates, on the latest closed bar:
- the 120 z-score variants (grid in `meta-trader-strategies/scripts/generate_edgelab_variants.py`)
  on ES=F, NQ=F, YM=F and RTY=F 1h, through `research.strategies` `positions()`;
- the continuation proxy (5-day return sign, 1×ATR stop, 2×ATR target, 10-bar hold) on daily
  bars for a configured universe, default HYG, XLU, XLB, XLE, XOP and EEM.

Each transition (ENTER / EXIT, side, price, bar time, params) is written once to an append-only
`research.signal_events`, keyed so that a re-run can never duplicate a row.

### T136 · Sonnet · T135 — price-signal alerts on a schedule
`research-search` gains two jobs. **Hourly at :05** while futures trade: refresh the four 1h
series, evaluate, record, and send one digest grouped per instrument. **Daily after the 17:30
bars job**: the continuation proxy. (`research-search` already receives `TELEGRAM_*`; see T134's
result.)

## Result

**T134 (2026-10-06).** Landed as specified. Compose needed no change, contrary to the spec:
all backend services share one environment block, so `gex-capture` and `research-search`
already receive `TELEGRAM_*`. Prod had neither variable set, so delivery is logged-only until
the bot token and chat id are in the server's `.env` and the containers are recreated. Details
are in the archive's addendum.
