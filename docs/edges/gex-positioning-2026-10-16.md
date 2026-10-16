# GEX positioning, 2026-10-16: what the desk can trade, and what Options Pro adds

The question was how to profit from dealer gamma positioning, and whether ThetaData *Options
Pro* ($160/month) gives the desk something *Standard* ($80) cannot. Three tests with a published
source were declared in [hypothesis-backlog.md](hypothesis-backlog.md) pass 13 and committed
(1cde5c8) **before** anything ran. Two ran tonight on free data. The third needs ThetaData
history and waits for T166.

## Answer

- **Options Pro adds no information the desk cannot build on Standard.** Its extras are
  conveniences (vendor second-order greeks), capacity (8 requests instead of 4, the unlimited
  trade stream) and four more years of tick history (2012 → 2016). None is a signal. Upgrading
  only makes sense if a rule passes and is shown to be limited by one of those, and none is today.
- **Daily gamma is free, and on its own it does not trade.** SqueezeMetrics publishes S&P 500
  dealer gamma (`DIX.csv`, from 2011, the same sign convention as the desk). Both daily-regime
  tests failed on it. Short gamma does make the last half hour follow the day, as the paper says,
  but almost all of that came from 2020 (#545–546). And long gamma is *not* a better time to buy
  dips; short-gamma dips rebounded harder (#547).
- **What is left for GEX is intraday and needs ThetaData:** the level rules and the gamma
  filter on walls (T167 on the T166 history), and signed 0DTE flow at the open (row 51, which
  needs `trade_quote` history). The subscription decision rests on these. It is unchanged:
  keep Standard through T167 and cancel on a clear fail (the history stays on disk).

## Options Pro against Standard

From [ThetaData pricing](https://www.thetadata.net/pricing) and the
[subscriptions table](https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html),
read 2026-10-16. The docs table drops empty cells. The column mapping below follows from rows
whose Standard access the 2026-09-25 probe already confirmed: `Greeks 1st Order` has two checks,
Standard and Pro.

| | Standard ($80) | Pro ($160) | Can the desk build Pro's version on Standard? |
|---|---|---|---|
| Tick history from | 2016-01 | 2012-06 | No, but SqueezeMetrics covers *daily* gamma from 2011 for free. Only intraday rules would gain, and T166 loads 2023 onward anyway |
| Concurrent requests | 4 | 8 | No. It would shorten T166 (fetch is ~60% of its time), but the load finishes in ~10 days on Standard |
| Greeks, 1st order, and IV | yes | yes | — |
| Greeks, 2nd / 3rd order (vanna, charm, vomma, …) | no | yes | **Yes.** `gex/greeks.py` already has `vanna` and `charm`. They are closed-form in the IV Standard delivers, and the engine recomputes gamma from IV instead of using vendor greeks anyway (context/gex-engine.md) |
| Trade greeks (greeks at each trade) | no | yes | **Yes.** `trade_quote` gives each trade with its NBBO. IV and delta at the trade follow from that and the 1st-order snapshot |
| `trade` and `trade_quote` history | yes (2 checks: Standard, Pro) | yes | — **To confirm at the data level** with the row-51 probe before relying on it |
| Real-time trade stream | 15k contracts | every option trade | 15k covers SPX + SPY 0DTE and the near expiries, which is all that row 51 would need live |
| Snapshots | option chain | option root | Same data, different grouping |

## What the literature supports

| Effect | Source | What the desk did with it |
|---|---|---|
| Short dealer gamma → intraday momentum (the last 30 min follow the day); none when long gamma | Baltussen, Da, Lammers and Martens (2021), *JFE* 142, Table 7: β 6.63 (t 4.78) vs 0.82 (t 1.03), S&P 1996–2020 | #536–538 (unconditional) and **#545–546 (conditioned): fail**. The sign holds on the CFDs, but the size is not tradable after the spread outside 2020 |
| Long dealer gamma → lower volatility and stronger order-flow reversals for about an hour | Adams et al., *Do S&P500 Options Increase Market Volatility? Evidence from 0DTEs* (working paper) | **#547: fail** as a filter on the daily dip-buy. The intraday version (fade a move in long gamma) is what wall-bounce and row 49 test |
| Gamma sign decides whether a level holds or breaks | Barbon and Buraschi (2021), *Gamma Fragility*; Ni, Pearson, Poteshman and White (2021) | Row 49, inside T167. The forward read so far is 4–5 sessions per key, which is noise |
| Option order imbalance in the first 10 minutes predicts the index's rest of day, profitable after costs | Kang, Kang and Lee (2022), *J. Futures Markets* 42(3) (KOSPI 200) | **Row 51, declared**: SPX 0DTE signed delta 09:30–09:40 → S&P.fs 09:40–15:55. Task T177 |
| Expiration-day pinning toward the at-the-money strike | Golez and Jackwerth (2012), *JFE* 106 (S&P futures options) | Not declared, see below |

## Not declared, and why

| Idea | Why not |
|---|---|
| Vanna / charm flows into the close or into opex | Practitioner claims with no published rule to fix the parameters from (also noted 2026-10-16 in [directional-signals](directional-signals-2026-10-16.md)). The greeks themselves are already computable, so this is not a reason to buy Pro |
| 0DTE pinning in the last hours | The paper documents pinning in futures on serial-option expiry days, with no trading rule. The desk's own `GAMMA_PIN` decisions are −0.53R ± 0.23 over 12 live trades, so there is nothing to suggest one |
| Gamma as a size or stop-width control | Both papers support "long gamma = lower realized volatility", which is a sizing input, not a direction. It belongs in stage 4 of a rule that has passed, not in a search |
| Extending T166 back to 2016 (Standard) or 2012 (Pro) | Only worth it if T167 is borderline. Extending after a clear fail would be a search |

## What happens next

1. **T166** loads 2023-01 → 2026-09 from tonight (unchanged).
2. **T167** runs the level rules and row 49 once the load is done.
3. **T177** probes one session of SPX 0DTE `trade_quote` (requests, time, size, and that
   Standard serves it), then loads 09:30–09:40 for the daily-expiry sessions and runs row 51
   once. It runs after T166, because both share the terminal's 4 request slots.
4. **The ThetaData decision** stays at the renewal after T167. If T167 and row 51 both fail
   clearly, cancel and keep the history. If either passes, keep Standard and take it to demo.
   Pro is not on the table unless a passing rule is shown to be limited by Standard's 15k trade
   stream or its 4 request slots.

Ledger: N = 547 after #545–547.
