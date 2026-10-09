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
