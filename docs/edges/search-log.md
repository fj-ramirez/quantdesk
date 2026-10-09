# Edge search log

The ledger for every hypothesis tested against the desk's history, **failures included**. It
is the denominator that makes a pass mean something: a strategy that clears 2 SE after 300
tries is about what chance produces. The research loop (see *Loop protocol*) appends here.

**Cumulative tests: 407** (288 T147 + 18 effect tests on 2026-10-09, the session decomposition
included, + the loop's rows below). Update this line with every entry.

## Pass bar

A hypothesis becomes a candidate (`docs/edges/<slug>.md`, stage 1 passed) only if **all** hold:

1. ≥ 100 trades over ≥ 30 sessions, after doubled recorded spread (and swap, if it is held across
   the 17:00 New York rollover).
2. **t ≥ max(2, sqrt(2 ln N))**, where N is the cumulative test count *including this one*.
   (N = 306 → 3.38.) This is the expected maximum of N noise t-statistics, the same idea as
   EdgeLab's noise ceiling expressed on t instead of Sharpe.
3. Mean > 0 both in-sample (< 2024-01-01) and out-of-sample (≥ 2024-01-01).
4. Positive in more than half of the calendar years.
5. Not explained by buy-and-hold drift. It must beat the same exposure held at random times, or
   the complement session must be flat or negative.

A pass that clears 1, 3, 4 and 5 but not 2 is logged as a **lead**. It is not a candidate.

### Position rules (added 2026-10-09, before backlog pass 2 ran)

Swing and trend rules hold a position for many days. They are scored on **daily P&L** rather
than per trade:

- The position is decided at the 16:00 New York close of day d and held to the 16:00 close of
  d+1.
- Each change of position pays **one recorded spread**, so a round trip pays two, which is the
  doubled cost.
- `.fs` roll steps are removed. XAUUSD pays swap for each 17:00 crossing, three nights on
  Wednesday.

The bar becomes:

1. ≥ 100 days in the market and ≥ 10 separate entries.
2. t on the in-market daily P&L ≥ max(2, sqrt(2 ln N)).
3. Positive in IS and OOS.
4. Positive in most years.
5. **A higher Sharpe than buy-and-hold of the same CFD over the same span.** A long-only rule
   that only collects the index drift with fewer days in the market is not an edge.

Few entries mean few independent observations. A trend rule with 15 entries is weak evidence
whatever its t, so the entry count is reported with every result.

## Loop protocol (what each iteration does)

1. Take the **first untested** row of [hypothesis-backlog.md](hypothesis-backlog.md). Copy it here
   under *Entries* with its rule written out **before** running anything. Parameters come from
   the cited source. Nothing is tuned.
2. Run it on the local M1 export, with one script per hypothesis in `scripts/edges/`. Refresh the
   export from `broker.bars` if it is more than 7 days old (read-only).
3. Increment N and record n, mean, t, IS/OOS, the yearly signs and the verdict
   (`fail` / `lead` / `pass`).
4. On a **pass**: write `docs/edges/<slug>.md` in the gold-asia-drift format (frozen spec,
   evidence, sizing, bootstrap kill rules, stage log) and add it to `README.md`.
5. A variant of a tested hypothesis is a **new backlog row**, appended at the bottom. It never
   replaces a result.
6. Never place orders and never write broker code (repo invariant).

## Backlog pass 1, 2026-10-09: summary

All 12 backlog rows are closed: 8 tested, 4 blocked by sample size. **No new candidate.** The one
standing candidate is still [gold-asia-drift](gold-asia-drift.md) (#301), and #322's context run
re-confirmed it on 2021-09+ (t = 3.97). What recurs across the failures:

- **The spread decides short holds on index CFDs.** Intraday-momentum (#307–309) and gap trades
  (#318–320) have about zero gross edge, so the doubled spread is the whole result.
- **Rare-event effects can't be tested here.** Pre-FOMC, opex week, holidays, monthly trend and
  big down days all have < 100 occurrences since 2019.
- **The dollar filter (#322) and the VIX filter (#315–317) both made things worse than no filter.**

## Backlog pass 2, 2026-10-09: summary (multi-day position rules)

5 rows and 18 tests. **No new candidate. One lead: RSI(2) pullback (#334–336)** on S&P.fs
(t = 3.13) and NAS100.fs (t = 2.47). It clears every criterion except the t bar (3.41), and beats
buy-and-hold Sharpe while in the market about 15% of days. It has two things against it: the
indices are one correlated sample, and its total return is below buy-and-hold. Next step for
it: **an independent history**, e.g. the cash index or ES/NQ futures daily from 2000–2019
(Yahoo, free), with the rule unchanged. A pass there makes it a candidate.

What else the pass found:

- **Long gold over several days pays heavily on Axi.** Today's swap of −$0.62 per oz per night
  is about 5–8% a year, so trend and momentum rules on gold lose to that cost. The intraday Asia
  window (#301) avoids it by design.
- **Trend rules on indices (TS momentum, golden cross, low-vol) mostly trade 2022 off against
  lower total return.** Too few entries to count as evidence.
- **Turtle breakouts earn nothing** on these four CFDs since 2019.

## Pass 3, 2026-10-09: summary (index CFDs, "at least in the current regime")

19 tests. **No new candidate.**

- **Index sessions (#344–355):** the index drift is spread evenly over the day, so no session
  window is worth trading alone.
- **Weekdays (#358–372):** the Friday-to-Monday hold looked strong since 2024 (t ≈ 3) but
  failed on 2000–19 (#373–374). It is not promoted.
- **RSI(2) pullback (#356–357):** positive on 19 independent years, on both indices and in
  both halves. Its t (1.5 / 2.0) is below the bar. It is the best index lead.

**The source of index profit in this data is holding, not timing.** S&P.fs and NAS100.fs
buy-and-hold returned +94% / +140% over 2019–26 after roll adjustment. Swap on the `.fs` CFDs
is 0, and none of the timing rules beat holding on total return. That is the equity risk
premium: documented over a century, not found here, and it has multi-year drawdowns (2022:
about −25% S&P, about −35% NAS). The trend filters (#298–300, #330–333) lowered the 2022
drawdown at the cost of total return. They work as a risk overlay, not as an edge.

## Pass 4, 2026-10-09 overnight: summary

16 tests (#375–390). **No new candidate.**

- **Lead: IBS < 0.2 on NAS100.fs** (#378–380, t = 2.91, 7/8 years). The same mean-reversion
  family as the RSI(2) lead, so it is not independent of it.
- **Utilities rotation (#387–389)** fails the bar, but it is a credible drawdown overlay for
  holding the index.
- **#390 re-confirms gold-asia-drift:** it holds in gold downtrends too.
- **Fail:** Turnaround Tuesday, NR7, and the VIX stretch (n = 69).

## Pass 5, 2026-10-09 overnight: summary

12 tests (#391–402). **No candidate, no lead.** Tug of war, last-half-hour periodicity, the
credit lead and the 5-minute opening-range breakout all fail. Four more intraday index rules
show the same thing as pass 1: before costs they are about zero, and the CFD spread decides.

## Pass 6, 2026-10-09 overnight: summary

4 tests (#403–406). **No candidate, no lead.** The index spreads trend rather than revert, and
monthly relative momentum lost to an equal-weight hold.

## Morning summary, 2026-10-09 (the overnight loop, passes 4–7, ~04:40–05:15 New York)

**Stopped after pass 7, as planned.** Cumulative tests: **407**. Current bar: **t ≥ 3.47**.

| | Status | Evidence |
|---|---|---|
| **[gold-asia-drift](gold-asia-drift.md)** | **the only candidate**: stage 1 passed, the executor is built (PR #18) | t = 3.73 (2019+), 4.12 (2021+); positive in gold downtrends too (#390); absent on Sunday nights (#407) |
| [index-hold-sma200](index-hold-sma200.md) | risk premium, not an edge | NAS100.fs ≈ $40–56 a month per 0.01 lot, max DD −$731 |
| RSI(2) pullback, S&P/NAS | lead, replicated sign | t 3.13 (broker), 1.5 / 2.0 (2000–19) |
| IBS < 0.2, NAS100.fs | lead | t 2.91, 7/8 years; the same family as RSI(2), so not independent |
| Utilities rotation (XLU vs SPY) | overlay idea | Sharpe above buy-and-hold on all 3 indices, positive in 2022 |

**Overnight, 33 tests (#375–407): no new candidate.** Intraday index rules keep failing on the
CFD spread (pass 5). Relative-value index trades do not revert (pass 6). The two mean-reversion
leads (RSI(2), IBS) are the most promising index work. A combined test on 2000–19 data
(declared first) is the natural next step, not more searching.

## Entries

| # | Date | Hypothesis | Instrument | n | Mean (net, 2× cost) | t | IS / OOS sign | Years + | Verdict | Script |
|---|---|---|---|---|---|---|---|---|---|---|
| 1–288 | 2026-10-09 | T147 intraday price action (6 patterns × 2 × M5/M15/H1 × 2 exits) | 4 CFDs | 288 trials | best +0.076R | best 1.5 | — | — | fail (all) | `app/modules/research/pa_study.py` |
| 289–291 | 2026-10-09 | Overnight drift 16:00→09:30 | S&P / NAS100 / DJ30 .fs | ~1.8k each | −0.008 / +0.016 / −0.020% | ≤ 1.4 | mixed | ≤ 5/8 | fail | `scripts/edges/2026-10-09-documented-effects.py` |
| 292–294 | 2026-10-09 | Intraday drift 09:30→16:00 | 3 index CFDs | ~1.8k | ≈ 0 | < 0.5 | mixed | — | fail | same |
| 295–297 | 2026-10-09 | Turn of month (last day → 3rd day) | 3 index CFDs | 64–87 | +0.1–0.3% | < 1.2 | + / + | ≤ 4/8 | fail (n < 100) | same |
| 298–300 | 2026-10-09 | Long while > 200-day SMA | 3 index CFDs | 0.9–1.3k | NAS100 +0.06% | NAS100 1.5 / 1.1 | + / + (NAS) | 6/7 (NAS) | **lead** (NAS100 only) | same |
| 301 | 2026-10-09 | Gold Asia long 18:00→03:00 | XAUUSD | 1,434 | +0.0525% | **3.73** | + / + | 7/8 | **pass** → [gold-asia-drift](gold-asia-drift.md) | same |
| 302 | 2026-10-09 | Gold NY short 08:20→16:00 | XAUUSD | 1,795 | −0.011% | < 0.8 | − / + | — | fail | same |
| 303 | 2026-10-09 | Gold overnight 16:00→09:30 | XAUUSD | 1,794 | +0.053% (no swap charged) | 0.96 / 2.54 | + / + | 6/8 | fail: swap not charged, crosses rollover | same |
| 304 | 2026-10-09 | Gold > 200-day SMA | XAUUSD | 1,171 | +0.05% (no swap charged) | −0.46 / 2.15 | − / + | 4/7 | fail | same |
| 305–306 | 2026-10-09 | Gold session decomposition (London, NY legs) | XAUUSD | ~1.8k | ≈ 0 | < 0.9 | — | — | context for #301 | inline |
| 307–309 | 2026-10-09 | Backlog 1, intraday momentum (Gao et al. 2018). Sign of the return from the prior 16:00 to 10:00; trade that direction 15:30 → 16:00. One test per index. | 3 index CFDs | 1.3–1.8k | −0.040 / −0.033 / −0.032% (gross ≈ 0) | −4.8 / −3.5 / −5.3 | − / − | 0–2 of 8 | fail: no gross effect; the spread is the loss | `scripts/edges/2026-10-09-01-intraday-momentum.py` |
| 310–312 | 2026-10-09 | Backlog 2, reversal after a large down day (Connors/Alvarez 2008). 16:00→16:00 return ≤ −2 × its trailing 20-session stdev (excluding today) → long at that 16:00, exit the next 16:00. One test per index. | 3 index CFDs | 47–68 | +0.27 / +0.39 / −0.02% | 1.1 / 1.5 / −0.1 | + / − (OOS ≈ 0) | 3–4 of 8 | fail: n < 100; the whole gain is March 2020 | `scripts/edges/2026-10-09-02-down-day-reversal.py` |
| — | 2026-10-09 | Backlog 3–5 (pre-FOMC, opex week, pre-holiday) | 3 index CFDs | ≤ 87 possible | | | | | blocked: the ≥ 100-trade bar cannot be met. Not run, not counted | — |
| 313–314 | 2026-10-09 | Backlog 6, gold into the London fixes (Caminschi and Heaney 2014). Short XAUUSD 10:00→10:30 London (AM fix) and, separately, 14:30→15:00 London (PM fix), every London weekday with bars. | XAUUSD | 1,862–1,864 | −0.010 / −0.009% (gross +0.007) | −3.3 / −1.3 | − / ≈ 0 | 2 of 8 | fail: a tiny gross drift (0.007%) that is less than half the spread | `scripts/edges/2026-10-09-06-gold-fix.py` |
| 315–317 | 2026-10-09 | Backlog 7, VIX term-structure filter (Simon and Campasano 2014). Long 16:00 → next 16:00 only when the Cboe VIX close < the VIX3M close on day d. Unconditional long close-to-close is shown as the buy-and-hold baseline (criterion 5) and not counted. Data 2021-09 → 2026-09-22. | 3 index CFDs | 1,183 | −0.007 / +0.013 / −0.002% | −0.2 / +0.3 / −0.1 | − / + | 3–5 of 6 | fail: worse than always-long. Contango holds on 95% of days and the 5% of backwardation days were the better ones (n = 64, not significant) | `scripts/edges/2026-10-09-07-vix-term.py` |
| 318–320 | 2026-10-09 | Backlog 8, gap fade (Plastun et al. 2019). If the 09:30 open is ≥ 0.5% from the prior 16:00, enter at 09:30 toward the prior 16:00 price. Exit at that price if touched (not on the entry minute), otherwise at 10:30. No stop. | 3 index CFDs | 369–808 | −0.063 / −0.005 / −0.045% | −2.8 / −0.2 / −1.8 | − / mixed | 2–4 of 8 | fail: before costs, gaps continue as often as they fill | `scripts/edges/2026-10-09-08-gap-fade.py` |
| 321 | 2026-10-09 | Backlog 9, gold weekend (Blose and Gondhalekar 2013). Long XAUUSD Friday 16:00 → Monday 03:00 New York, with the Friday rollover swap charged. It overlaps #301's Sunday-evening Asia session, so it is not independent of it. | XAUUSD | 303 | +0.002% (gross +0.048, swap −0.029, cost 0.017) | +0.03 | + / − | 3 of 8 | fail: the Friday swap and the spread take the whole gross gain | `scripts/edges/2026-10-09-09-gold-weekend.py` |
| 322 | 2026-10-09 | Backlog 10, gold-asia-drift conditioned on the dollar. Take #301's 18:00 → 03:00 long only when UUP closed below its previous close on day d. A **variant** of #301: a pass makes a new candidate and does not replace #301. UUP data from 2021-09. | XAUUSD | 440 | +0.031% | 1.11 | ≈ 0 / + | 5 of 6 | fail: the dollar filter **hurts**. Over the same 2021-09+ span, unconditional #301 gives t = 3.97 (n = 1,008, a consistency check, not a new test). The dollar-*up* sessions were the stronger half (t = 4.4, n = 568), but that is a post-hoc reading. A rule built on it would be data-snooped and is **not** promoted | `scripts/edges/2026-10-09-10-gold-asia-dollar.py` |
| — | 2026-10-09 | Backlog 11, NAS100 10-month SMA (Faber 2007), monthly rebalance | NAS100.fs | ≤ 87 months | | | | | blocked: one trade a month cannot reach 100. Not run, not counted | — |
| 323–325 | 2026-10-09 | Backlog 12, last-half-hour reversal on volatile mornings (Baltussen et al. 2021). On sessions whose 09:30–10:00 range is > 2 × the median of the previous 20 sessions' 09:30–10:00 ranges, trade 15:30 → 16:00 **against** the sign of the prior 16:00 → 10:00 return. | 3 index CFDs | 42–131 | −0.018 / −0.143 / +0.032% | −0.3 / −1.3 / +0.7 | mixed | 3–4 of 8 | fail: rare (42–131 days) and no consistent sign | `scripts/edges/2026-10-09-12-volatile-reversal.py` |
| 326–329 | 2026-10-09 | Backlog 13, time-series momentum (Moskowitz, Ooi, Pedersen 2012). Long while the 16:00 close > the close 252 sessions earlier, otherwise flat. Position rule. | 3 indices + XAUUSD | 860–1,289 days, 7–17 entries | +0.048 / +0.071 / +0.025 / +0.013% a day | 1.9 / 2.0 / 0.9 / 0.4 | + / + (gold − / +) | 4–6 of 8 | fail: Sharpe ≈ buy-and-hold (0.70 vs 0.66 S&P, below it on the others), lower total return, and only 7–8 entries. **Gold finding:** today's long swap (−$0.62 per oz per night) costs about 5–8% a year, so buy-and-hold XAUUSD on Axi returned +42% where gold itself rose about +200%. Historical swaps were probably smaller, which makes this a pessimistic reading. | `scripts/edges/2026-10-09-13-tsmom.py` |
| 330–333 | 2026-10-09 | Backlog 14, golden cross (Brock, Lakonishok, LeBaron 1992). Long while the 50-session SMA of 16:00 closes > the 200-session SMA, otherwise flat. Position rule. | 3 indices + XAUUSD | 885–1,284 days, **2–6 entries** | +0.056 / +0.089 / +0.024 / +0.018% a day | 2.1 / 2.4 / 0.8 / 0.5 | + / + (gold − / +) | 3–6 of 8 | fail: under 10 entries, so it is three or four bets on the 2022 bear. Sharpe beats buy-and-hold on S&P/NAS (0.79/0.91 vs 0.66/0.79) by sitting out 2022, but total return is lower and t is below the bar | `scripts/edges/2026-10-09-14-golden-cross.py` |
| 334–336 | 2026-10-09 | Backlog 15, RSI(2) pullback (Connors and Alvarez 2009). Enter long at the 16:00 close when RSI(2) (Wilder) < 10 and close > SMA200. Exit at the first close > SMA5. Position rule. | 3 indices | 190–257 days, 41–59 entries | +0.169 / +0.191 / +0.097% a day | **3.13** / 2.47 / 1.76 | + / + on all three | 5–6 of 6–8 | **lead** (S&P.fs, NAS100.fs): every criterion except t ≥ 3.41. Sharpe 1.16 / 0.92 vs buy-and-hold 0.66 / 0.79, in the market about 15% of days. Lower total return than buy-and-hold. The three indices fire on the same days, so this is one piece of evidence, not three. DJ30 fails. **Not a candidate:** it needs out-of-sample confirmation, i.e. forward data or an independent history, before a demo | `scripts/edges/2026-10-09-15-rsi2.py` |
| 337–340 | 2026-10-09 | Backlog 16, Donchian breakout (Turtle rules, Faith 2003). Long on a close above the prior 20-session high, short on a close below the prior 20-session low. Exit a long on a close below the prior 10-session low, and a short on a close above the prior 10-session high. Position rule, long and short. | 3 indices + XAUUSD | 1,071–1,464 days, 61–79 entries | ≈ 0 on all four | −0.6 … +0.2 | − OOS on 3 of 4 | 2–4 of 8 | fail: flat to negative. Single-market Turtle breakouts on these four CFDs since 2019 earn nothing after costs | `scripts/edges/2026-10-09-16-donchian.py` |
| 341–343 | 2026-10-09 | Backlog 17, low-volatility regime long (after Moreira and Muir 2017). Long while the 20-session realized volatility of 16:00-to-16:00 returns is below its trailing 252-session median, otherwise flat. Position rule. | 3 indices | 617–884 days, 30–34 entries | +0.028 / +0.066 / +0.013% a day | 1.0 / 1.7 / 0.4 | + / + | 3–6 of 8 | fail: Sharpe below buy-and-hold on all three. On these CFDs the high-volatility days carried much of the return | `scripts/edges/2026-10-09-17-low-vol.py` |
| 344–355 | 2026-10-09 | Backlog 18, index session decomposition. Long each window separately: Asia 18:00 → 03:00, London 03:00 → 09:30, NY morning 09:30 → 12:00, NY afternoon 12:00 → 16:00. .fs swap 0, roll steps removed. Each reported full-span and ≥ 2024. | 3 index CFDs | 1.3–1.8k per window | gross +0.001 … +0.040% per window, net −0.030 … +0.012% | −2.5 … +0.9 | none + / + above 1 | ≤ 5 of 8 | fail, all 12. **Unlike gold, the index drift is spread across every session** (each window +0.01–0.04% gross), so slicing it pays the spread once per slice. The index return is in holding, not in timing a session (see the pass-3 note) | `scripts/edges/2026-10-09-18-index-sessions.py` |
| 356–357 | 2026-10-09 | Backlog 19, the RSI(2) lead (#334–336) on an independent history: rule unchanged, Yahoo daily ^GSPC / ^NDX closes 2000-01-01 → 2019-06-30. Cost per position change: the CFD's median recorded spread as a % of price (S&P.fs → ^GSPC, NAS100.fs → ^NDX). The whole span is out-of-sample for this rule, so the IS/OOS criterion becomes 'positive in each decade half' (2000–09, 2010–19). | ^GSPC, ^NDX | 551 / 672 days, 124 / 139 entries | +0.066 / +0.095% a day in the market | 1.52 / 2.00 | **+ in 2000–09 and 2010–19 on both** | 11 / 20, 12 / 20 | **replicated lead**, below the bar (3.43). The sign holds on 19 years the rule never saw, on both indices and in both halves. Sharpe beats buy-and-hold over 2000–19 (0.35 / 0.45 vs 0.29 / 0.27) because it sat out 2000–02 and 2008, but loses to it in 2010–19. With the broker span that is 3 out of 3 positive periods per index. The effect is real-looking but small: about 0.1% a day while in the market, in the market about 13–15% of days | `scripts/edges/2026-10-09-19-rsi2-independent.py` |
| 358–372 | 2026-10-09 | Backlog 20, index weekday effect (French 1980). Long 16:00 → next session's 16:00, separately for each weekday of the entry (Mon … Fri; Friday holds over the weekend). .fs swap 0, roll steps removed. Full span and ≥ 2024. | 3 index CFDs | 250–373 per weekday | Fri +0.090 / +0.163 / +0.075%; the other days ≈ 0 or − | Fri 1.3 / 1.9 / 1.4 | Fri: IS ≈ 0, **OOS (≥ 2024) +0.23 / +0.32 / +0.15%, t 2.9 / 3.0 / 2.1** | Fri 4–6 of 6–8 | fail, all 15. **Regime observation:** the Friday-close → Monday-close hold (over the weekend) carried the index return since 2024 and nothing before. It was found by looking at 15 cells, so it needs its own test on data it was not found on before it is anything. Filed as backlog 21 | `scripts/edges/2026-10-09-20-weekday.py` |
| 373–374 | 2026-10-09 | Backlog 21, the weekend hold on an independent history: long the Friday close → the next session's close, Yahoo daily ^GSPC / ^NDX 2000-01 → 2019-06, cost as #356–357. | ^GSPC, ^NDX | 983 weekends each | −0.044 / −0.052% | −1.0 / −0.9 | − in 2000–09 on both | 8 / 20, 10 / 20 | **fail. The 2024–26 Friday pattern does not replicate**: over 2000–19 the weekend hold was slightly negative, the classic Monday effect. It was a recent accident, or a regime with no history behind it. Not promoted | `scripts/edges/2026-10-09-21-weekend-independent.py` |
| 375–377 | 2026-10-09 | Backlog 22, Turnaround Tuesday. If Monday 16:00 < Friday 16:00, long Monday 16:00 → Tuesday 16:00. | 3 index CFDs | 92–115 | +0.217 / +0.163 / −0.031% | 1.5 / 1.1 / −0.4 | S&P + / −, NAS + / + | 7 / 8 S&P & NAS | fail: below the bar. S&P is mostly 2020, and its OOS is negative | `scripts/edges/2026-10-09-22-turnaround-tuesday.py` |
| 378–380 | 2026-10-09 | Backlog 23, IBS mean reversion (Pagonidis 2013). RTH IBS = (close − low) / (high − low) < 0.2 → long 16:00 → next session's 16:00. | 3 index CFDs | 255–335 | +0.122 / **+0.285** / +0.096% | 1.5 / **2.91** / 1.6 | + / + on all three | 5 / **7** / 5 of 6–8 | **lead (NAS100.fs)**, below the bar of 3.45. All three indices are positive in IS and OOS. Caveat: NAS 2020 alone is +53.6% of the +95.3% total, and it is the same mean-reversion family as the RSI(2) lead (#334), so the two are not independent evidence | `scripts/edges/2026-10-09-23-ibs.py` |
| 381–383 | 2026-10-09 | Backlog 24, NR7 breakout (Crabel 1990). After the narrowest RTH range of 7 days, the next day: the first M1 touch above that day's high → long at that level (or at the open if it gaps through); below its low → short. Stop at the opposite side. Exit 15:55. Stop checked first on an ambiguous bar. Cost: doubled spread at the entry bar. | 3 index CFDs | 198–297 | +0.029 / +0.041 / +0.008% | 0.7 / 0.7 / 0.2 | mixed | 4–6 of 6–8 | fail: small positive, nowhere near the bar | `scripts/edges/2026-10-09-24-nr7.py` |
| 384–386 | 2026-10-09 | Backlog 25, VIX stretch (Connors and Alvarez 2009). Cboe VIX close > 1.10 × its 10-day SMA on day d → long index 16:00 d → 16:00 five sessions later. Signals during an open trade are ignored (no overlap). VIX data 2021-09 → 2026-09-22. | 3 index CFDs | 69 each | +0.08 / +0.30 / −0.01% | 0.3 / 0.7 / −0.0 | − / + | 4 of 6 | fail: n < 100 and IS negative. It lost heavily in the 2022 bear (buying spikes in a falling market) and was positive since 2023. Regime-dependent, and with too few trades to say more | `scripts/edges/2026-10-09-25-vix-stretch.py` |
| 387–389 | 2026-10-09 | Backlog 26, utilities beta rotation (Gayed and Bilello 2014). At each Friday close, if XLU's 20-session return < SPY's, long the index CFD from that close to the next Friday close, else flat. Position rule; data from 2021-09. | 3 index CFDs | 691 days, 30 entries | +0.053 / +0.068 / +0.036% a day | 1.6 / 1.5 / 1.2 | + / + | **6/6**, 5/6, 4/6 | fail on the bar (t < 2), but as a **risk overlay** it beats buy-and-hold Sharpe on all three (0.72 / 0.65 / 0.53 vs 0.54 / 0.55 / 0.42), and S&P was positive in 2022 (+5.3% vs buy-and-hold about −20%). It is an alternative to the SMA200 overlay in [index-hold-sma200](index-hold-sma200.md). That comparison was not made here and would be its own test | `scripts/edges/2026-10-09-26-utilities-rotation.py` |
| 390 | 2026-10-09 | Backlog 27, gold-asia-drift only when the XAUUSD 16:00 close on day d > its 200-session SMA. A **variant** of #301: a new candidate if it passes, never a replacement. | XAUUSD | 934 | +0.063% | 3.42 | + / + | 6/7 | lead (just under the bar), but **the filter adds nothing**: over the same span the unfiltered #301 gives t = 4.12, 7/7 years, and the filtered-out downtrend nights were positive too (t = 2.4, 5/5 years). **This strengthens #301:** the Asia drift is not a gold-bull artifact. No new candidate. #301 stays unfiltered | `scripts/edges/2026-10-09-27-gold-asia-trend.py` |
| 391–393 | 2026-10-09 | Backlog 28, tug of war (Lou, Polk and Skouras 2019). Overnight 16:00 → 09:30 return > 0 → short 09:30 → 16:00; < 0 → long. | 3 index CFDs | 1.3–1.8k | −0.024 / −0.035 / +0.004% | −1.1 / −1.3 / +0.2 | mixed | 2–3 of 6–8 | fail: no reversal between the overnight and intraday legs on these CFDs | `scripts/edges/2026-10-09-28-tug-of-war.py` |
| 394–396 | 2026-10-09 | Backlog 29, intraday periodicity (Heston, Korajczyk and Sadka 2010). Trade 15:30 → 16:00 in the direction of the previous session's 15:30 → 16:00 return. | 3 index CFDs | 1.3–1.8k | −0.044 / −0.042 / −0.024% | −5.4 / −4.5 / −4.0 | − / − | 0–1 of 6–8 | fail: gross ≈ 0 (slightly negative), so the doubled spread is the whole result | `scripts/edges/2026-10-09-29-periodicity.py` |
| 397–399 | 2026-10-09 | Backlog 30, credit leads equities (Gilchrist and Zakrajšek 2012). At each Friday close, if HYG's 20-session return > IEF's, long the index to the next Friday close, else flat. Position rule; data from 2021-09. | 3 index CFDs | 752 days, 31 entries | +0.032 / +0.053 / +0.022% a day | 0.9 / 1.1 / 0.7 | ≈ 0 / + | 4–5 of 6 | fail: Sharpe below buy-and-hold on all three, and it lost in 2022 (credit turned late). Worse than the utilities overlay (#387) | `scripts/edges/2026-10-09-30-credit-lead.py` |
| 400–402 | 2026-10-09 | Backlog 31, 5-minute opening-range breakout (Zarattini and Aziz 2023). The 09:30–09:35 candle's direction sets the side (skip a doji). Enter at the 09:35 open, stop at the candle's opposite extreme, target 10R, exit at 15:55. Stop checked first; the entry bar never pays; gaps fill at the open. | 3 index CFDs | 1.3–1.8k | −0.026 / −0.002 / −0.033% | −2.4 / −0.1 / −2.8 | − | 0–3 of 6–8 | fail: the paper's QQQ result (cheap commissions, no spread at the open) does not survive the CFD's spread. Gross is +0.01 / +0.03 / −0.01% | `scripts/edges/2026-10-09-31-orb5.py` |
| 403–405 | 2026-10-09 | Backlog 32, index pairs mean reversion (Gatev et al. 2006). z of log(A/B) over 60 days at the 16:00 close. Enter at \|z\| > 2 (long the cheap leg, short the rich one), exit when z crosses 0 or after 20 sessions. Equal notional, each leg's doubled spread charged on each change, roll steps removed per leg. Criterion 5 (beat buy-and-hold) does not apply to a market-neutral rule. | NAS/S&P, NAS/DJ, S&P/DJ | 547–789 days, 28–37 entries | +0.006 / +0.008 / −0.002% a day | 0.3 / 0.2 / −0.1 | mixed | 3–4 of 6–8 | fail: the index spreads trend (tech vs value) more than they revert | `scripts/edges/2026-10-09-32-index-pairs.py` |
| 406 | 2026-10-09 | Backlog 33, relative-momentum rotation (Antonacci 2014; Jegadeesh and Titman 1993). At each month-end 16:00 close, hold the index CFD with the highest 63-session return until the next month-end. Always invested. Compared with an equal-weight hold of all three over the same span (from 2021-06, DJ30's start). | 3 index CFDs | 1,267 days | +0.026% a day | 0.78 | + / + | 5 of 6 | fail: Sharpe 0.35 vs equal-weight 0.52, total +33% vs +47%. It held NAS through most of the 2022 drawdown (−24.7%) | `scripts/edges/2026-10-09-33-rotation.py` |
| 407 | 2026-10-09 | Backlog 34, gold Asia drift on Sunday nights (never in #301's sample): long XAUUSD at the first bar at or after Sunday 18:00 New York, out Monday 03:00. | XAUUSD | 373 | −0.009% (gross +0.021, cost 0.029) | −0.24 | − / + | 3 of 8 | fail. **The Asia drift is not present on Sunday nights**: gross +0.02%, against #301's weekday-night +0.08%. The weekly open differs (weekend gap, a wider spread: median $0.23, p90 $0.78). This does not contradict #301's own sample, but it narrows the mechanism to weekday Asian sessions, and it is noted in the candidate file | `scripts/edges/2026-10-09-34-gold-sunday-asia.py` |
