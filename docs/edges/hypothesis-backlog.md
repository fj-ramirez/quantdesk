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
| 3 | Pre-FOMC drift | Long from 14:00 New York the day before an FOMC announcement to 14:00 on announcement day (5 minutes before the release). | 3 index CFDs | Lucca and Moench (2015), *JF* | tested #524–525 on ^GSPC/^NDX 1994–2019: lead, but decayed after publication (2012+ t ≈ 1) |
| 4 | Opex-week drift | Long Monday open → Friday close in the week of the monthly third-Friday expiration. | 3 index CFDs | Stivers and Sun (2013), opex-week effect | tested #526–528 as a position rule: fail |
| 5 | Pre-holiday effect | Long the close two sessions before a US market holiday to the close of the session before it. | 3 index CFDs | Ariel (1990); Kim and Park (1994) | tested #529–530 on ^GSPC/^NDX 1990–2019: fail |
| 6 | Gold around the London fixes | Short XAUUSD from 10:00 to 10:30 London (into the AM fix) and from 14:30 to 15:00 London (into the PM fix). | XAUUSD | Caminschi and Heaney (2014), fix-window drift | tested #313–314: fail |
| 7 | VIX term-structure filter | Long index CFDs close-to-close only while VIX < VIX3M (contango) at the prior close. Compare with unconditional. | 3 index CFDs | Simon and Campasano (2014); Fassas and Hourvouliades (2019) | tested #315–317: fail |
| 8 | Gap fade | When the 09:30 open gaps ≥ 0.5% from the prior 16:00 close, trade toward the close until 09:30 + 60 minutes or the gap fills. | 3 index CFDs | Gap-fill literature, e.g. Plastun et al. (2019) | tested #318–320: fail |
| 9 | Weekend effect in gold | Long XAUUSD from Friday 16:00 to Monday 03:00 New York, swap charged (triple swap on the configured weekday). | XAUUSD | Weekday-seasonality literature on gold, e.g. Blose and Gondhalekar (2013) | tested #321: fail |
| 10 | Dollar-conditioned gold Asia | gold-asia-drift only on sessions where UUP closed down the prior day. A **variant** of #301, so it is a new candidate if it passes. | XAUUSD | Gold/dollar inverse relation | tested #322: fail |
| 11 | NAS100 trend filter, rechecked | The lead from #298–300 with a 10-month SMA on month-end closes (the source's exact rule), monthly rebalance. | NAS100.fs | Faber (2007) | tested #531 as a position rule: fail (4 entries) |
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

## Pass 8: day-2 continuation (added 2026-10-09 ~09:55 New York, at the user's request, before it ran)

| # | Hypothesis | Rule (fixed before running) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 35 | Day-2 continuation after a down day | If the CFD's previous 16:00 → 16:00 return was ≤ −1% (roll sessions skipped), and the 09:45 price is below the 09:30 open: short at 09:45, stop at the 09:30–09:45 high, exit 15:55. Stop checked first; gaps fill at the open. The GEX-flip filter cannot be backtested (about 20 sessions of history) and stays a demo-only overlay. 2 tests. | NAS100.fs, S&P.fs | Crabel (1990), day-2 trend continuation; Gao et al. (2018), conditioned on the prior day | tested #408–409: fail (shorts lose; it reverts) |

## Pass 9: when does the 5-minute ORB work? (added 2026-10-09 ~10:10 New York, at the user's request, before it ran)

The base trade is #400–402 unchanged: the 09:30–09:35 candle sets the side, entry at 09:35,
stop at the candle's opposite extreme, target 10R, exit 15:55. Each row takes it **only** on
days that meet a condition known by 09:35. 4 filters × 2 instruments = 8 tests.

| # | Filter | Rule (fixed before running) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 36 | High relative volume | The opening candle's tick volume ≥ 2 × the mean of the same 5 minutes over the previous 14 sessions. | NAS100.fs, S&P.fs | Zarattini, Barbon and Aziz (2024), "stocks in play" | tested #410–417: fail |
| 37 | Narrow prior day (NR7) | The previous RTH day had the narrowest range of the last 7. | NAS100.fs, S&P.fs | Crabel (1990) | tested #410–417: fail |
| 38 | Trend alignment | The opening candle's direction matches the previous day's 16:00 → 16:00 direction. | NAS100.fs, S&P.fs | Day-2 trend literature (Crabel 1990) | tested #410–417: fail |
| 39 | Gap and go | The 09:30 open gaps ≥ 0.3% from the previous 16:00, in the direction of the opening candle. | NAS100.fs, S&P.fs | Gap-continuation practitioner rule; Plastun et al. (2019) | tested #410–417: fail |

## Pass 10: more documented patterns (added 2026-10-09 ~10:30 New York, at the user's request, before any ran)

| # | Hypothesis | Rule (fixed from the source) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 40 | Intraday → overnight reversal | If 09:30 → 16:00 was up, short 16:00 → the next 09:30. If it was down, long. | 3 index CFDs | Branch and Ma (2012), negative intraday/overnight autocorrelation | tested #418–426: fail |
| 41 | Double 7s | Long at the close when the close is the lowest of 7 sessions and above SMA200. Exit at the first close that is the highest of 7. Position rule. **The same mean-reversion family as RSI(2)/IBS**: a family replication, not independent evidence. | S&P.fs, NAS100.fs | Connors and Alvarez (2009), *Short Term Trading Strategies That Work* | tested #418–426: fail |
| 42 | Small-gap fill | If the 09:30 open gaps 0.1–0.3% from the previous 16:00, trade toward the previous 16:00. Exit at that price if touched (not on the entry minute), otherwise at 12:00. No stop. | 3 index CFDs | Plastun et al. (2019): small gaps fill more often than large ones | tested #418–426: fail |
| 43 | Gold Monday weakness | Short XAUUSD Monday 08:20 → 16:00 New York (no swap). | XAUUSD | Ball, Torous and Tschoegl (1982), weekday effects in gold | tested #418–426: fail |

## Pass 11: the buy-the-dip family on independent history (added 2026-10-09 ~10:45 New York, before it ran)

| # | Hypothesis | Rule (fixed before running) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 44 | Mean-reversion composite | Long at the close when the close > SMA200 **and** any of: RSI(2) (Wilder) < 10, IBS < 0.2, close = the lowest of 7 sessions. Exit at the first close > SMA5. Yahoo daily ^GSPC / ^NDX, 2000-01-01 → 2019-06-30 (before the broker history). Cost: the matching CFD's median spread as a % of price per position change (doubled per round trip). Position-rule bar, with IS/OOS read as 2000–09 vs 2010–19. 2 tests. | ^GSPC, ^NDX | Connors and Alvarez (2009); Pagonidis (2013); leads #334–336, #378–380, #419–420 | tested #427–428: **^NDX passes → candidate** |

## Pass 12: directional signals from flows (added 2026-10-16, at the user's request, before any ran)

The question: GEX levels may mark *where* the market reacts, but not *which way*. These are
signals with a flow mechanism that says which way, each from a published source. Rows 45–48 are
backtested now. Row 49 needs the T166 intraday GEX history, which starts loading 2026-10-16
17:00 ET, so it is declared here **before that data exists** and is run inside T167. Specs:
[rod-momentum](rod-momentum.md), [turn-of-month](turn-of-month.md),
[month-end-rebalancing](month-end-rebalancing.md), [fomc-cycle](fomc-cycle.md),
[gamma-conditioned-walls](gamma-conditioned-walls.md).

| # | Hypothesis | Rule (fixed from the source) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 45 | Rest-of-day momentum into the close (hedging demand: option market makers short gamma, leveraged-ETF rebalancing) | ROD = the return from the previous 16:00 to 15:30. If \|ROD\| ≥ 1.0%, trade 15:30 → 16:00 in ROD's direction; otherwise no trade. The paper's predictor is ROD on all days; the 1% floor is **this desk's choice, declared now**: the hedging flow scales with the day's move, and #307–309 showed that the spread eats a 30-minute trade on an ordinary day. 3 tests. | 3 index CFDs | Baltussen, Da, Lammers and Martens (2021), *JFE* 142; Cheng and Madhavan (2009) for the LETF flow | tested #536–538: fail |
| 46 | Turn of the month, on independent history | #295–297's rule unchanged: long from the close of the month's last trading day to the close of the 3rd trading day of the next month. Yahoo ^GSPC / ^NDX 1990-01 → 2019-06 (before the broker span), the CFD's median spread doubled. Criterion 5: the mean must beat the mean of every 3-session close-to-close hold over the same span. 2 tests. | ^GSPC, ^NDX | Lakonishok and Smidt (1988); Ariel (1987); McConnell and Xu (2008) | tested #539–540: **lead** (t 2.5 / 2.7, bar 3.55) |
| 47 | Month-end pension rebalancing (the Calendar signal, equity leg only) | A simulated 60/40 portfolio of ^GSPC and IEF (adjusted close, the 10-year-note proxy), reset to 60% equity at each month's last trading-day close and drifting with daily returns. Cal_t = equity weight − 0.60 at the close of t. When t is one of the month's **last 5 trading days**, hold −sign(Cal_t) from close t to close t+1. On the **first trading day** of a month, hold sign(Cal on the 4th-last trading day of the previous month) from close t to close t+1. Otherwise flat. Position rule, 2019-07 → 2026-10. The post-paper slice (≥ 2023-03-18) is reported separately. The paper trades S&P against 10-year futures; the desk has no bond CFD, so only the equity leg is tested. 2 tests. | S&P.fs, NAS100.fs | Harvey, Mazzoleni and Melone (2025), *The Unintended Consequences of Rebalancing*, NBER w33554, §4 | tested #541–542: fail |
| 48 | Even weeks of the FOMC cycle | Trading-day count from each scheduled FOMC announcement (day 0). Long the close-to-close return of day d when d falls in week 0 (days −1 to 3), 2 (9–13), 4 (19–23) or 6 (29–33); the 5 days before the next meeting (−6 to −2) are odd week −1, so flat. Otherwise flat. The schedule is public, so the position is known in advance. Position rule, 2019-07 → 2026-10, which is after the paper's sample (1994–2016). Scheduled meetings only (2019–20 from the Fed's historical pages, 2021+ from `fomccalendars.htm`). 2 tests. | S&P.fs, NAS100.fs | Cieslak, Morse and Vissing-Jorgensen (2019), *JF* 74(5) | tested #543–544: fail (gone after publication) |
| 49 | Gamma sign decides bounce vs break | Wall bounces (put and call, `ALL` and `_0DTE`) are taken only when the same capture's net GEX > 0. Wall breaks only when it is < 0. Scored exactly as T167 scores the unconditional keys (CFD M1, doubled spread, SE clustered by session, IS/OOS), and against the unconditional key and the complement (the same key with the opposite sign). **Run in T167**, not here. Forward: read off `gex.level_events.net_gex`, which is already logged. 4 tests (2 kinds × `ALL`/`_0DTE`). | SPX/SPY, QQQ, DIA, GLD, IWM → their CFDs | Barbon and Buraschi (2021), *Gamma Fragility*; Ni, Pearson, Poteshman and White (2021), *RFS* | declared, waits for T166 |

## Pass 13: what the desk can do with dealer gamma (added 2026-10-16 evening, at the user's request, before any ran)

The question: how to profit from GEX positioning, and whether ThetaData *Options Pro* data
gives the desk anything it cannot build on Standard. The survey, the tier comparison and the
rows not declared are in [gex-positioning-2026-10-16.md](gex-positioning-2026-10-16.md).
Daily dealer gamma for rows 50 and 52 is **SqueezeMetrics' free `DIX.csv` `gex` column**
(S&P 500 index options, +calls / −puts, the same convention as the desk's engine and the one
Baltussen et al. used to extend their sample). It starts 2011-05-02 and is read as one current
vintage, so revisions are unknown. A session uses the value for the **previous** session, as
the paper does, which is known before the next open. DJ30.fs is left out: the gamma is S&P
500's, and NAS100.fs is the one correlated index the desk already trades.

| # | Hypothesis | Rule (fixed from the source) | Instruments | Source | Status |
|---|---|---|---|---|---|
| 50 | Rest-of-day momentum, **only when dealers are short gamma** | On session t with SqueezeMetrics `gex` for t−1 < 0: ROD = previous 16:00 → 15:30; trade 15:30 → 16:00 in ROD's direction. **No \|ROD\| floor** (the paper's Table 7 conditions on the sign of NGE<sub>t−1</sub> alone). Otherwise flat. Scored with `_common.trades` conventions (open of the first M1 bar at or after the instant, doubled spread at entry, roll steps removed), 2019-07 → 2026-10-15. Context, not counted: the same trade on `gex` ≥ 0 days. 2 tests. Not a rescue of #536–538: it is the paper's own condition, which #45 left out. | S&P.fs, NAS100.fs | Baltussen, Da, Lammers and Martens (2021), *JFE* 142, Table 7: β<sub>ROD</sub> 6.63 (t 4.78) when NGE<sub>t−1</sub> < 0, 0.82 (t 1.03) otherwise, 1996–2020 | declared |
| 51 | Signed 0DTE option flow at the open predicts the rest of the day | SPX options expiring today, trades 09:30:00–09:39:59 from ThetaData `trade_quote` (Standard; history from 2016). Sign by the quote rule against the NBBO at the trade: above the mid = buy, below = sell, at the mid = dropped. Net signed delta = Σ sign × delta × size × 100, delta from the 09:30 `greeks/first_order` snapshot (puts negative). Net > 0 → long S&P.fs 09:40 → 15:55, < 0 → short. Sessions with daily SPX expiries (2022-05-11 →). Holdout 2026-09-21 →, as T167. 1 test. **Runs after T166**: it shares the terminal's 4 request slots. Before it runs, a one-session probe records the request count and time for one session's 10-minute trade_quote pull. | SPX → S&P.fs | Kang, Kang and Lee (2022), *J. Futures Markets* 42(3), 338–364 (first-10-minute option order imbalance predicts the index's rest of day, KOSPI 200, profitable after costs); Ni, Pearson and Poteshman (2005) for signed option volume | declared, waits for T166 |
| 52 | Positive dealer gamma is the regime for buy-the-dip | The [nas-mean-reversion](nas-mean-reversion.md) composite unchanged (close > SMA200 and RSI(2) < 10 or IBS < 0.2 or a 7-day low; exit at the first close > SMA5), but an **entry** at close t is taken only when SqueezeMetrics `gex` for t−1 > 0. A held position exits by the rule regardless of gamma. Yahoo daily ^NDX 2011-05-03 → 2026-10-15, cost the NAS100.fs median spread per position change. Scored by the position-rule bar. **Adopted only if** the filtered rule clears the bar **and** its mean per day in the market beats the unfiltered rule's, with the excluded entries (gex ≤ 0) worse than the filtered ones. 1 test. A pass is a new candidate; it does not change the frozen executor class. | ^NDX → NAS100.fs | Adams et al., *Do S&P500 Options Increase Market Volatility? Evidence from 0DTEs* (working paper): higher dealer net gamma predicts stronger order-flow reversals and lower volatility; Baltussen et al. (2021); SqueezeMetrics (2017), *Gamma Exposure* white paper | declared |
