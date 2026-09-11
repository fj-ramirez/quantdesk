# Real-time, paid (T21–T23) and the 2026-09-11 vendor re-survey

`T21`–`T23` keep their existing specs in `TASKS.md` — the real-time design doc, the Tradier
REST+WebSocket provider, and the recompute loop. Nothing about them changes. This file records
the vendor re-survey done on 2026-09-11, so the next reader does not have to redo it, and
states what the tiers below this one imply for when the paid tier is worth buying.

## Status

Blocked on a funded Tradier brokerage account. Not blocked on anything in this repo.

`PLAN.md` §1's conclusion still holds: **Tradier brokerage, $0–10/month, is the only real-time
OPRA path under the $50 budget**, and DR eligibility is confirmed. Greeks refresh hourly from
ORATS, so the app computes gamma itself from live quotes and IV — which it does anyway.

## Alpaca, evaluated 2026-09-11

The user asked. It was not in `PLAN.md` §1's original survey. It does not displace Tradier.

**What it would give.** Real-time OPRA options quotes with `impliedVolatility` and
Black-Scholes greeks, over REST snapshot/chain endpoints and a WebSocket stream — a legitimate
fast feed.

**Why it does not fit.**

1. **Open interest is absent from the market data API entirely.** Chain, snapshot and
   historical option endpoints all omit it; it is a long-standing open feature request. OI
   lives on the *trading* API's `GET /v2/options/contracts`, which does carry `open_interest`
   and `open_interest_date`, filters by `underlying_symbols`, and pages to 10,000 contracts.
   So an `AlpacaProvider` would be a two-API join, and — given invariant 3, where `None` OI
   silently excludes a contract — a partial join failure would quietly deflate GEX rather than
   fail loudly. Workable, but it is a provider that has to be written defensively.
2. **$99/month for real OPRA.** The free Basic plan gives options only the *indicative* feed
   (derived values, delayed trades, modified quotes). Real OPRA requires Algo Trader Plus.
   That is double the data budget, and it loses on merit to ThetaData Standard at $80, which
   `PLAN.md` already declined as over budget and which at least includes tick history to 2016
   and full greeks.
3. **Index-option market-data coverage is undocumented.** Index options (SPX, SPXW, VIX, DJX,
   XSP) arrived on the *trading* API in paper only; the market data docs say nothing either
   way about index underlyings. SPX is the headline symbol here, so this alone would decide
   it, and it could not be settled from the documentation.
4. **History since February 2024 only, with no OI history at all** — so it does not unlock the
   `T25` backtest harness the way ThetaData would.
5. **DR eligibility unconfirmed.** Alpaca publishes no country list and directs the question
   to support. Tradier's eligibility is already confirmed in `PLAN.md` §1.

**Where it could still earn a slot.** As a *second free delayed provider*. The Cboe endpoint
is undocumented, has no SLA, and the only mitigation today is the provider abstraction itself
(invariant 6). Alpaca's indicative feed plus the contracts-endpoint OI would be a real
fallback, and a paper account is free, instant and unfunded — unlike Tradier Sandbox, which
`PLAN.md` §1 lists as the second delayed source but which also has only hourly ORATS greeks.

**If it is ever picked up**, it is a spike, not a migration, and it answers two questions
first: does the market data API serve SPX chains on a free paper account, and is the
indicative IV close enough to Cboe's to cross-check against? Two yeses make it
`PROVIDER=alpaca` as a fallback. Anything less and it is not worth the two-API join.

## What the cheaper tiers imply for buying this one

`T18` and `T72` together give: structure at most 15 minutes old, refreshing 27 times a
session, with live distance-to-flip on top. What they do **not** give is a live *surface* — on
a fast move the walls and flip point themselves are stale by up to 15 minutes, and that is
precisely when they matter.

So the honest trigger for buying the paid tier is empirical, and the cheaper tiers are what
produce the evidence: once `T18` has run for a few weeks, compare the 15-minute-lagged flip
point against where it actually sat, using the captured series itself. If the lag moves the
flip point by less than the width of a typical stop, real-time buys nothing. If it does not,
open the Tradier account. Decide with data rather than in advance — the same discipline
`PLAN.md` applied in deferring the ThetaData decision until after Phase 4.

## Sources for the Alpaca finding (fetched 2026-09-11)

- <https://docs.alpaca.markets/us/docs/real-time-option-data>
- <https://docs.alpaca.markets/us/docs/historical-option-data>
- <https://docs.alpaca.markets/us/reference/optionchain>
- <https://docs.alpaca.markets/reference/get-options-contracts>
- <https://alpaca.markets/data>
- <https://github.com/alpacahq/Alpaca-API/issues/261> (open interest absent from market data)
- <https://alpaca.markets/blog/alpaca-introduces-index-options-paper-trading/>
- <https://alpaca.markets/support/countries-alpaca-is-available>
