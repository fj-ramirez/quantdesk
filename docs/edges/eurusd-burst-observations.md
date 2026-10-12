# eurusd-burst-fade: indicator observations at the provider's entries and exits

**What this is:** a description, not a rule search. For each of the provider's 66 sell bursts
(2026-04-27 → 07-10, copy-account statement, see [eurusd-burst-fade](eurusd-burst-fade.md)), it
records what standard indicators read on M1, M5, M15, H1, H4 and D1 at the entry and at the basket
close. Each reading is set against the same indicator over every minute of those weeks. Nothing here was tuned, and nothing here is a test.

**How it was read:**
- **Data:** Axi EURUSD M1 (`broker.bars`). Higher timeframes were built from it, and D1 is the
  broker day (17:00 New York).
- **Timing:** each timeframe's indicators come from its last *completed* bar before the
  instant. %B and the EMA distances also have a *live* version: the fill price against the last
  completed bands.
- **Settings:** RSI 14, stochastic 14/3/3, Bollinger 20/2, EMA 20/50/200, MACD 12/26/9 scaled
  by ATR 14, ADX 14.
- **Script and outputs:** `scripts/edges/2026-10-12-burst-observe.py`. The per-burst table is
  `backend/data/broker-export/burst-snapshots.csv` (gitignored).

AUC: 0.5 means the reading is no different from a random minute. 0.75 means a provider moment
outranks a random minute 75% of the time.

## 1. The daily chart: they sell in a daily downtrend

| D1 reading | Sell entries | Random minute |
|---|---|---|
| EMA20 < EMA50 < EMA200 (bearish stack) | **89%** | 62% |
| Price below the D1 EMA50 | **94%** | 76% |
| D1 RSI | median 41 | 43 |

- **This corrects an earlier note.** In [eurusd-burst-fade](eurusd-burst-fade.md) I said "the
  20-day trend filter does not separate them". That compared one SMA against a base where 82%
  of minutes were below it. On the full EMA stack the difference is clear.
- **From 2026-05-27 on, every sell (58 of 58) came with a bearish D1 stack.** The first 8, in
  April and May, did not (stack 0 or bullish).
- **The 3 buys were also in a bearish D1 stack.** They are rare and small (−3.7, −0.9, +4.6), so
  they look like occasional counter-trend scalps, not a mirrored setup.

## 2. The intraday chart: they sell the rally into the upper band

| Reading at the sell | Sell entries | Random minute | AUC |
|---|---|---|---|
| H1 live %B > 0.8 | **53%** | 19% | 0.74 |
| M15 live %B > 0.8 | **55%** | 19% | 0.73 |
| M15 above the upper band (%B > 1) | 30% | 7% | |
| Price above the M15 EMA50 | **80%** | 45% | 0.75 (median +2.0 ATR) |
| Price above the H1 EMA20 | 76% | 46% | 0.74 |
| M15 RSI | median 61 (IQR 52–67) | 49 | 0.74 |
| M5 stochastic > 80 | 38% | 18% | 0.66 (median 73) |
| H4 / D1 RSI, stochastic or %B | about the same as random | | 0.5 |

- **The overbought reading lives on M5–H1.** On H4 and D1 the entries look like any other
  minute, apart from the downtrend.
- **They don't wait for the classic extreme.** RSI > 70 occurs on only 17–21% of entries on
  M5/M15 and on 2% on H1. The typical sell is "upper half of the M15/H1 band, RSI around 60".
- **They don't wait for momentum to turn.** At entry the H1 MACD histogram is still *rising*
  70% of the time (random: 50%). They sell into the rally, not after it rolls over.

## 3. Winners vs losers: the losers were not sold at the top

| Median at entry | Winners (58) | Losers (8) |
|---|---|---|
| M15 live %B | **0.85** | 0.63 |
| M5 stochastic | **75** | 39 |
| M15 RSI | 61 | 52 |
| Price vs M15 EMA50 (ATR) | +2.0 | +1.1 |

Four of the losers (#34, #35, #64, #65) were sold near or below the **lower** M15 band
(%B −0.11 … 0.31) with M5 stochastic 11–28. That is selling weakness, not fading a rally, and a
different kind of trade from the rest. The other sells taken low in the band (#21–23 and #54)
made only +1 to +8. **The sells taken at the upper band are where the provider's results come
from.**

## 4. Exits: they take profit when M5 is oversold

| At the basket close | Winners | Losers | Random minute |
|---|---|---|---|
| M5 live %B (median) | **0.22** | 0.80 | 0.47 |
| M5 stochastic (median) | **17** | 27 | 48 |
| M5 stochastic < 20 (all exits) | 55% | | 21% |
| M5 %B < 0.5 (all exits) | 77% | | 53% |
| M5 MACD histogram | negative (median −0.19 ATR) | | 0 |

**Winners close when M5 reaches its lower band and the M5 stochastic is oversold:** the
short-term swing has run its course. H1 shows nothing at the exit. Losers close near the M5
*upper* band, i.e. they were stopped out into a continuing rally.

## The picture these observations draw

Read together, without any fitting:

> **In a daily downtrend** (D1 EMA20 < 50 < 200, price below the D1 EMA50), **sell when an
> intraday rally pushes price into the upper part of the M15/H1 Bollinger band** (%B ≳ 0.8,
> ~2 ATR above the M15 EMA50, M5 stochastic > 70). **Take profit when M5 becomes oversold**
> (stochastic < 20, price at the M5 lower band). The grid adds (see the burst-fade file) are the
> same idea repeated as the rally extends.

That is a **trend-pullback** strategy: sell the rallies in a downtrend. It is not a fade of every
stretch. **That distinction is what the failed tests were missing:**
- #429 (both directions) and #430 (short-only) had **no daily trend filter**, so they also sold
  rallies in uptrends;
- #431 (the grid) used the same unfiltered entry;
- the exits there were a fixed trail, not "M5 oversold".

**It has not been tested.** It is the first hypothesis that comes out of the provider's own
readings rather than out of a guess. To make it a candidate:
1. Freeze it with exact numbers (the medians above, not values tuned to the bursts).
2. Mirror it for uptrends: buy pullbacks to the lower band when the D1 stack is bullish.
3. Run it once on 2019–2026, minus the burst weeks. That span covers both 2021–22 and 2024
   downtrends, and the 2020 and 2025 uptrends where the mirror applies.

**Caveats:**
- n = 66 sells in one 10-week downtrend, so all of the above describes that regime.
- The AUCs were read across 78 indicator × timeframe cells, so a few of the weaker ones (around
  0.6) can be noise.
- The strong, consistent ones are: D1 trend, M15/H1 band position, M15 RSI, and the M5 exit.

## 5. New trades from the user's MT5 History screenshots (2026-10-12)

The screenshots show the History → Positions list. Its timestamp is the **close** time: the
2026-10-08 buys entered at 19:00 server and show 20:15:38. Readings are as in §1–4.

| Trade | When (server) | M5 | M15 | H1 |
|---|---|---|---|---|
| Sep 29 sell, 3 × 0.01, 1.13486 → 1.13362, +12.4 pips: **exit** | 18:06 | %B −0.15, stoch 17 | %B 0.02, stoch 22 | %B 0.06, stoch 23 |
| Oct 8 buy, ≥ 8 × 0.01, 1.11915 → 1.12076, +16.1: **entry** | 19:00 | %B 0.18, **stoch 5** | %B 0.46 | %B 0.41 |
| Oct 8 buy: **exit** (75 min later) | 20:15:38 | **%B 1.17, stoch 85** | %B 0.78 | %B 0.86 |
| Oct 7 buy, 1.11870 → 1.11955, +8.5: entry / exit | 18:38 / 20:53:32 | %B 0.49 / 0.62 | 0.64 / 0.78 | 0.21 / 0.33 |

- **The exits fit §4.** Both clear winners closed with M5 at or beyond the *opposite* band: the
  sell at the lower band on all three timeframes, the Oct 8 buy above the M5 upper band.
- **The October buys mirror the sells.** Oct 8 was bought with M5 deeply oversold (stochastic 5),
  just as the sells come with M5 overbought.
  - **This contradicts §1**, where the 3 earlier buys looked like rare counter-trend scalps.
    Both October buys came with the D1 stack still bearish (EURUSD ~1.12, down from 1.17), so
    the provider *does* trade both sides inside a D1 downtrend.
- **USDJPY appears**, always bought (USD long, like USDCHF/GBPCHF). Three USDJPY closes land
  exactly on an M5 boundary (06:45:00, 07:20:00, 13:05:00): an EA acting at a new M5 bar.
  USDJPY is not in `broker.bars`, so it can't be checked.
- **The account is live and paying the provider.** A `PPF…TRFto872629` transfer on 2026-09-27
  matches the weekly profit-share debits in the April–July statement.

**Second batch of screenshots (expanded rows, 2026-10-12):**
- **Exact open times:**
  - Oct 6 sell **15:05:00** server, exactly an M5 bar open;
  - Oct 7 buy 18:38:29, mid-bar;
  - Oct 8 buy **19:00:07**, 7 s after an H1/M5 bar open (about the copier's delay).
- **Two of three entries sit on an M5 bar open**, as do three USDJPY closes. The April–July
  statement did not show this: 12% of first orders came within 10 s after a minute boundary,
  and minute-of-5 was uniform. Either the provider changed method, or the copier's timestamps
  differ between the two exports.
  - **Next check:** entry and exit seconds across a full History export, rather than 3 entries.
- **No S/L or T/P is set** on any position, and the comments are copy-platform IDs.
- **The Oct 6 copy was closed by the user** at 15:14:11 (−1.6 pips). The provider's own result
  (+12.3, as reported) stays in the log, and the user's close is noted beside it.

## 6. Exit timing match (2026-10-12): no standard exit reproduces the provider's close times

`scripts/edges/2026-10-12-burst-exit-match.py`. For the 61 winning bursts with a known close (58 sells,
3 buys including October), each standard exit rule was run from the real entry and its close time
compared with the provider's.

| Rule | Within 10 min | Within 30 min | Fires > 10 min early |
|---|---|---|---|
| Trail 5/3 | 28% | 44% | 56% |
| Fixed TP 5 pips | 28% | 52% | 61% |
| Fixed TP 10 pips | 23% | 43% | 34% (median error +0.8 min) |
| M5 stochastic < 20 | 22% | 36% | 59% |
| M5 lower-band touch | 21% | 34% | 60% |
| M15/H1 bands, RSI, stochastic | 0–13% | ≤ 30% | — |

- **None reproduces the timing.** The best match about a quarter of the closes.
- **The indicator rules mostly fire early.** The provider holds through the first M5 oversold
  touch, the first 5 pips and the first band touch.
- **§4's "they take profit when M5 is oversold" is a state they are in at the exit, not its
  trigger.**
- **A 10-pip target is centred on their closes but scattered.** That fits a per-trade target: a
  $ or basket amount, a level they choose, or a manual close.
- **Search count:** 28 exit rules, matched on timing only, with no P&L fitted.

## 7. Clock of opens and closes (2026-10-12)

| | 02–07 UTC | 21–02 UTC | 07–21 UTC |
|---|---|---|---|
| Opens (69) | **0** | 6 | 63 |
| Closes (69) | 4 | 11 | 54 |

- 12 of the 69 closes fall in 22–06 UTC (Asian night), e.g. baskets closed 01:00–04:00 UTC.
- Close seconds and minutes are uniform: 20% in :00–:09, 17% on an M5 mark.

**Reading:** entries are made inside a European/American trading day (a person, or a session-
limited program), while **exits run unattended, at any hour, not on bar opens**. That fits
**a take-profit (and maybe a stop) set on the provider's side, which the copy platform does not
replicate**. The copied positions show no S/L or T/P because the copier mirrors the master's
close, not its order levels. A per-trade level would also explain §6: no indicator exit matches,
and a 10-pip target is centred on the closes but scattered.

**Next test once more data exists:** in the full History export (or the provider's page), check
whether each close price sits at a round distance from its entry (e.g. a fixed multiple of ATR,
or a fixed $ per lot), which is what a level set at entry would leave behind.

**Checked the same day on the 59 Apr–Jul winners:**
- **Spread of the realised profit** (std / mean, CV):

  | Measure | CV |
  |---|---|
  | Raw pips (median 6.8) | 0.78 |
  | Pips / ATR14 on M5, M15, H1, H4, D1 | 0.88, 0.88, 0.86, 0.79, 0.77 |
  | $ per 0.01 lot (median $0.68) | 0.78 |

- **Profit does not scale with volatility:** corr(pips, ATR) is −0.15 … +0.16.
- **So the target is not a fixed pips, ATR multiple or $ amount.** If it is a level set on the
  provider's side, it is chosen per trade (structure, a prior swing, discretion). Testing that
  needs the provider's own order levels, which the copy account does not carry.
