# Hypothesis backlog

Documented effects queued for the research loop ([search-log.md](search-log.md)). Tests run
**top to bottom**, one per iteration. Each row names its source, so the parameters come from the
source and not from the data. Mark a row `tested #<ledger id>` when it has run. Append new ideas
at the bottom. Never reorder rows after seeing a result.

Instruments: `S&P.fs`, `NAS100.fs`, `DJ30.fs` (futures-based, swap 0, roll steps removed) and
`XAUUSD` (spot, swap charged when the hold crosses 17:00 New York). Data: `broker.bars` M1 from
2019-07, plus `gex.daily_bars` (Yahoo daily, from 2021-09: VIX family, SPY, sectors, TLT, UUP,
GLD and more) for conditioning only.

| # | Hypothesis | Rule (fixed from the source) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 1 | Intraday momentum | The return from the prior 16:00 close to 10:00 predicts 15:30→16:00. Trade 15:30→15:59 in the direction of that first-half-hour return. | 3 index CFDs | Gao, Han, Li, Zhou (2018), *JFE* | tested #307–309: fail |
| 2 | Short-term reversal after a large down day | After a session close-to-close return ≤ −2× its 20-day stdev, buy at the close and hold 1 session. | 3 index CFDs | Connors/Alvarez (2008), short-term reversal literature | tested #310–312: fail |
| 3 | Pre-FOMC drift | Long from 14:00 New York the day before an FOMC announcement to 14:00 on announcement day (5 minutes before the release). | 3 index CFDs | Lucca and Moench (2015), *JF* | blocked: `fomc_calendar.json` starts 2021-01, so ≤ 47 scheduled meetings and the ≥ 100-trade bar cannot be met (not run, not counted) |
| 4 | Opex-week drift | Long Monday open → Friday close in the week of the monthly third-Friday expiration. | 3 index CFDs | Stivers and Sun (2013), opex-week effect | blocked: one trade a month, ≤ 87 since 2019-07, so the ≥ 100-trade bar cannot be met (not run, not counted) |
| 5 | Pre-holiday effect | Long the close two sessions before a US market holiday to the close of the session before it. | 3 index CFDs | Ariel (1990); Kim and Park (1994) | blocked: about 9 holidays a year, ≤ 65 since 2019-07, so the ≥ 100-trade bar cannot be met (not run, not counted) |
| 6 | Gold around the London fixes | Short XAUUSD from 10:00 to 10:30 London (into the AM fix) and from 14:30 to 15:00 London (into the PM fix). | XAUUSD | Caminschi and Heaney (2014), fix-window drift | tested #313–314: fail |
| 7 | VIX term-structure filter | Long index CFDs close-to-close only while VIX < VIX3M (contango) at the prior close. Compare with unconditional. | 3 index CFDs | Simon and Campasano (2014); Fassas and Hourvouliades (2019) | tested #315–317: fail |
| 8 | Gap fade | When the 09:30 open gaps ≥ 0.5% from the prior 16:00 close, trade toward the close until 09:30 + 60 minutes or the gap fills. | 3 index CFDs | Gap-fill literature, e.g. Plastun et al. (2019) | tested #318–320: fail |
| 9 | Weekend effect in gold | Long XAUUSD from Friday 16:00 to Monday 03:00 New York, swap charged (triple swap on the configured weekday). | XAUUSD | Weekday-seasonality literature on gold, e.g. Blose and Gondhalekar (2013) | tested #321: fail |
| 10 | Dollar-conditioned gold Asia | gold-asia-drift only on sessions where UUP closed down the prior day. A **variant** of #301, so it is a new candidate if it passes. | XAUUSD | Gold/dollar inverse relation | tested #322: fail |
| 11 | NAS100 trend filter, rechecked | The lead from #298–300 with a 10-month SMA on month-end closes (the source's exact rule), monthly rebalance. | NAS100.fs | Faber (2007) | blocked: monthly rebalance, ≤ 87 trades, so ≥ 100 cannot be met (not run, not counted) |
| 12 | Last-half-hour reversal on high-volume days | Opposite of #1 when the first half hour range is > 2× its 20-day median. | 3 index CFDs | Baltussen, Da, Lammers, Martens (2021), *JFE*, hedging-pressure reversal | tested #323–325: fail |

## Pass 2: multi-day position rules (added 2026-10-09, after pass 1 closed, before any of these ran)

Scored with the *Position rules* section of [search-log.md](search-log.md). Closes are the 16:00
New York price. Instruments: 3 index CFDs plus XAUUSD (4 tests per row unless stated).

| # | Hypothesis | Rule (fixed from the source) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 13 | Time-series momentum | Long while the close is above the close 252 sessions earlier, otherwise flat. | 3 indices + XAUUSD | Moskowitz, Ooi, Pedersen (2012), *JFE* | tested #326–329: fail |
| 14 | Golden cross | Long while the 50-session SMA of closes is above the 200-session SMA, otherwise flat. | 3 indices + XAUUSD | Brock, Lakonishok, LeBaron (1992), *JF* | tested #330–333: fail |
| 15 | RSI(2) pullback in an uptrend | Enter long at the close when RSI(2) < 10 and the close > its 200-session SMA. Exit at the first close above the 5-session SMA. | 3 indices | Connors and Alvarez (2009), *Short Term Trading Strategies That Work* | tested #334–336: **lead** (S&P, NAS100) |
| 16 | Donchian breakout (long and short) | Long on a close above the prior 20-session high, short on a close below the prior 20-session low. Exit a long on a close below the prior 10-session low, and a short on a close above the prior 10-session high. | 3 indices + XAUUSD | Turtle rules, Faith (2003) | tested #337–340: fail |
| 17 | Low-volatility regime long | Long while the 20-session realized volatility is below its trailing 252-session median, otherwise flat. | 3 indices | Moreira and Muir (2017), *JF*, volatility-managed portfolios | tested #341–343: fail |

## Pass 3: index sessions and the RSI(2) lead (added 2026-10-09, before any of these ran)

Asked for: strategies on the index CFDs, "at least in the current regime". Regime-conditioned
rules are allowed (edge-research skill) if the condition is stated and monitored. Each result
is reported both on the full history and on **≥ 2024 only (the current regime)**. A rule that
passes the bar only on ≥ 2024 is a **regime lead**: it may go to demo with a regime kill rule,
never straight to size.

| # | Hypothesis | Rule (fixed before running) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 18 | Index session decomposition | Long each window separately, every session date d with a next session: Asia 18:00 d−1 → 03:00 d, London 03:00 → 09:30, NY morning 09:30 → 12:00, NY afternoon 12:00 → 16:00. 4 windows × 3 indices = 12 tests. | S&P.fs, NAS100.fs, DJ30.fs | The gold finding (#301), and the overnight-drift literature (Cliff et al. 2008, Lou et al. 2019) | tested #344–355: fail |
| 19 | RSI(2) pullback on an independent history | Rule #15 unchanged, on Yahoo daily ^GSPC and ^NDX, 2000-01-01 → 2019-06-30 (before the broker history starts). Cost: the CFD's median spread in % of price, doubled. 2 tests. | ^GSPC, ^NDX | #334–336 lead | tested #356–357: replicated lead |
| 20 | Index weekday effect | Long 16:00 → next 16:00 on each weekday separately (Mon … Fri). 5 × 3 = 15 tests. | 3 index CFDs | French (1980); Gibbons and Hess (1981) | tested #358–372: fail (Friday holds strong ≥ 2024 only) |
| 21 | Weekend hold on an independent history | Long the Friday close → the next session's close (Yahoo daily ^GSPC, ^NDX, 2000-01 → 2019-06), cost as #19. Tests whether #358–372's Friday cell is an effect or a 2024–26 accident. 2 tests. | ^GSPC, ^NDX | The #370–372 observation | tested #373–374: fail (does not replicate) |

## Pass 4: documented short-swing and cross-asset rules (added 2026-10-09, before any ran; unattended overnight loop)

Daily bars for the index CFDs are built from M1: the RTH open is the 09:30 price, the close the
16:00 price, and high/low come from 09:30–16:00. Cross-asset inputs are from `gex.daily_bars`
(Yahoo/Cboe, from 2021-09), so those rows have about 4 years of data.

| # | Hypothesis | Rule (fixed from the source) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 22 | Turnaround Tuesday | If Monday's 16:00 close < Friday's 16:00 close, long Monday 16:00 → Tuesday 16:00. | 3 index CFDs | Connors and Alvarez; widely documented | tested #375–377: fail |
| 23 | IBS mean reversion | IBS = (close − low) / (high − low) of the RTH day. If IBS < 0.2, long 16:00 → next 16:00. | 3 index CFDs | Pagonidis (2013), *The IBS effect* | tested #378–380: lead (NAS100) |
| 24 | NR7 breakout | After the narrowest RTH range of the last 7 days, the next day: long on a trade above that day's high, short below its low (first touch), exit at 15:55. Stop at the opposite side. | 3 index CFDs | Crabel (1990), *Day Trading with Short Term Price Patterns* | tested #381–383: fail |
| 25 | VIX stretch | If the Cboe VIX close > 1.10 × its 10-day SMA, long the index 16:00 → 16:00 five sessions later. | 3 index CFDs | Connors and Alvarez (2009), VIX stretches | tested #384–386: fail |
| 26 | Utilities beta rotation | Each Friday close: if XLU's 4-week return < SPY's, long the index for the next week, else flat. | 3 index CFDs | Gayed and Bilello (2014), Dow Award paper | tested #387–389: fail (overlay value noted) |
| 27 | Gold-asia-drift in a gold uptrend only | #301's rule, taken only when the XAUUSD 16:00 close > its 200-day SMA. A **variant**: a new candidate if it passes, and it never replaces #301. | XAUUSD | Faber (2007) trend overlay | tested #390: lead, filter adds nothing |

## Pass 5: intraday structure and credit (added 2026-10-09 ~05:00 New York, before any ran; unattended)

| # | Hypothesis | Rule (fixed from the source) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 28 | Overnight/intraday tug of war | If the 16:00 → 09:30 return is > 0, short 09:30 → 16:00. If it is < 0, long 09:30 → 16:00. | 3 index CFDs | Lou, Polk and Skouras (2019), *JFE*; Berkman et al. (2012) | tested #391–393: fail |
| 29 | Intraday periodicity | Trade 15:30 → 16:00 in the direction of the previous session's 15:30 → 16:00 return. | 3 index CFDs | Heston, Korajczyk and Sadka (2010), *JF* | tested #394–396: fail |
| 30 | Credit leads equities | At each Friday close, if HYG's 20-session return > IEF's, long the index to the next Friday close, else flat. Position rule; data from 2021-09. | 3 index CFDs | Gilchrist and Zakrajšek (2012), *AER*; the practitioner HYG/IEF ratio | tested #397–399: fail |
| 31 | 5-minute opening-range breakout | The 09:30–09:35 candle's direction sets the side (skip a doji). Enter at 09:35, stop at the candle's opposite extreme, target 10R, exit at 15:55. | 3 index CFDs | Zarattini and Aziz (2023), *Can Day Trading Really Be Profitable?* (SSRN) | tested #400–402: fail |

## Pass 6: relative value between the index CFDs (added 2026-10-09 ~05:05 New York, before any ran; unattended)

Relative trades hold one CFD long and another short, so market direction largely cancels.
`.fs` swap is 0 on both legs, and each leg pays its own doubled spread.

| # | Hypothesis | Rule (fixed from the source) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 32 | Index pairs mean reversion | Spread = log(A) − log(B) of 16:00 closes. z = (spread − its 60-day mean) / its 60-day stdev. z < −2 → long A / short B. z > 2 → the reverse. Exit when z crosses 0, or after 20 sessions. Equal notional. | NAS/S&P, NAS/DJ, S&P/DJ | Gatev, Goetzmann and Rouwenhorst (2006), *RFS* | tested #403–405: fail |
| 33 | Relative-momentum rotation | At each month-end close, hold the one index CFD with the highest 63-session return until the next month-end. Always invested. Compared with an equal-weight hold. | 3 index CFDs | Antonacci (2014), *Dual Momentum*; Jegadeesh and Titman (1993) | tested #406: fail |

## Pass 7: the gold mechanism on nights #301 never traded (added 2026-10-09 ~05:10 New York, before it ran; unattended, last pass)

#301's entries are Monday–Thursday 18:00, so the **Sunday-evening Asia session** (the weekly
open → Monday 03:00) was never in its sample. If the Asia drift is a real flow, it should be
there too. This is the same hypothesis on unseen nights, so it is an out-of-sample check rather
than a new rule.

| # | Hypothesis | Rule (fixed before running) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 34 | Gold Asia drift, Sunday session | Long XAUUSD at the first M1 bar at or after Sunday 18:00 New York (the weekly open), sell at Monday 03:00. No swap: the hold crosses no 17:00 rollover. Weekend-gap risk at entry is included as filled. | XAUUSD | #301 mechanism | tested #407: fail (no drift on Sunday nights) |
