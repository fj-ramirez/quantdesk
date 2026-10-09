# index-hold-sma200: hold NAS100.fs / S&P.fs long while above the 200-day average

**Not an edge. A risk premium with a regime switch.** Written 2026-10-09 because the pass-3
search found no index timing rule that beats holding, and holding is what made money. Read this
before treating it like the gold candidate.

**Stage:** no stage-1 gate applies. The return is the equity risk premium, documented over a
century, not something this search found. It has no t-stat bar to pass and no backtest edge to
decay. The question is only size against drawdown. **Next step:** demo at 0.01 lot alongside
gold-asia-drift, to measure the real rollover adjustments on the `.fs` CFDs.

## Rule

| Field | Value |
|---|---|
| Instruments | NAS100.fs (first choice) and S&P.fs. DJ30.fs is not worth it: $2–14 a month per 0.01 lot. |
| Position | Long while the 16:00 New York close is above its 200-session simple average. Flat otherwise. Checked once a day at the close. |
| Why the filter | It cut the worst drawdown by about 40% (NAS100.fs −$1,200 → −$731 per 0.01 lot since 2019) at the cost of about 10–20% of the return. It is the overlay from #298–300 / #330–333, used for risk, not claimed as an edge. |
| Costs | Swap on the `.fs` CFDs is 0 (`broker.symbol_specs`). The cost of carry is in the quarterly roll adjustment, which the numbers below already include. A spread is paid only on each switch (a few a year). |
| Rolls | Hold through them. The broker adjusts at the roll. Verify the first one on demo (the next is 2026-12-14). |

## Evidence (broker M1 → 16:00 closes, roll steps removed, per 0.01 lot)

| | $/month 2019+ | $/month 2024+ | Months negative | Max drawdown |
|---|---|---|---|---|
| NAS100.fs hold | 44 | 68 | 39% | −$1,200 |
| **NAS100.fs > SMA200** | **40** | **56** | 30–38% | **−$731** |
| S&P.fs hold | 22 | 34 | 32–36% | −$610 |
| S&P.fs > SMA200 | 17 | 27 | 28% | −$504 |

## $100/month arithmetic

```
NAS100.fs > SMA200, haircut to the 2019+ rate: ~$40/month per 0.01 lot
lots for $100/month           = 0.025 → round to 0.03 lot (≈ $120/month on 2019+ rates)
historical max DD at 0.03 lot ≈ 3 × $731 = $2,193, and a 2022-style year without the filter ≈ $3,600
account so that DD ≤ 20%      ≈ $11k
```

Combined with gold-asia-drift (about $30 a month per 0.01 lot after the haircut, uncorrelated
by session), a $10–15k account at 0.02–0.03 lot each covers the desk's cost **on history**. On a
smaller account it does not, and more leverage does not change that.

## Kill / regime rules

1. **Regime exit:** below SMA200 → flat. This is the rule working, not a failure.
2. **Drawdown stop:** a fall of −$1,000 per 0.01 lot from the peak (beyond the historical
   filtered maximum of −$731) → flat until the next cross above SMA200 *and* a review.
3. **Carry drift:** a demo roll adjustment that costs more than 1.5% a quarter (the measured
   steps were 0.5–1.2%) → re-run these numbers before going live.

## Stage log

| Date | Event |
|---|---|
| 2026-10-09 | Written after pass 3 (#344–374) found no index timing edge. Holding is the profit source, and the SMA200 filter is a drawdown overlay. Next: 0.01 lot demo, check the December roll adjustment. |
