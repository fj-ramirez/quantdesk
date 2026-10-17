# Edge search log

The ledger for every hypothesis tested against the desk's history, **failures included**. It
keeps every family's denominator honest (a rule that clears 2 SE after 300 variants of itself is
about what chance produces) and gives the programme-level count of expected false passes. The research loop (see *Loop protocol*) appends here.

**Cumulative tests: 565** (288 T147 + 18 effect tests on 2026-10-09, the session decomposition
included, + the loop's rows below, + 84 T156 COT trials on 2026-10-13, + 11 on 2026-10-14, + 1 on 2026-10-15, + 9 on 2026-10-16, + 3 in pass 13, + 18 in pass 14). Update this line with every entry.

## Pass bar

A hypothesis becomes a candidate (`docs/edges/<slug>.md`, stage 1 passed) only if **all** hold:

1. ≥ 100 trades over ≥ 30 sessions, after doubled recorded spread (and swap, if it is held across
   the 17:00 New York rollover).
2. **t ≥ max(2, sqrt(2 ln k))**, where k is the size of the set this result was *picked from*
   (see *Selection count* below). For a single pre-declared cell, k = 1 and the bar is t ≥ 2.
   This is the expected maximum of k noise t-statistics, the same idea as EdgeLab's noise
   ceiling expressed on t instead of Sharpe.
3. Mean > 0 both in-sample (< 2024-01-01) and out-of-sample (≥ 2024-01-01).
4. Positive in more than half of the calendar years.
5. Not explained by buy-and-hold drift. It must beat the same exposure held at random times, or
   the complement session must be flat or negative.

A pass that clears 1, 3, 4 and 5 but not 2 is logged as a **lead**. It is not a candidate.

**Which bar applies is decided by the holding period, not the data depth.** A rule that holds
for days is scored with the *Position rules* bar below, even if it was written as a per-trade
rule. When a short-hold event rule cannot reach 100 trades on the broker span, run it on an
independent history (Yahoo daily before 2019-07, as #356–357 and #427–428 did) rather than
marking it blocked. Backlog rows 3, 4, 5 and 11 sat blocked from 2026-10-09 to 2026-10-14 for
this reason.

### Selection count (replaces the cumulative N, 2026-10-16)

The bar used to be t ≥ max(2, sqrt(2 ln N)) with N the cumulative count of every test in this
log. That charged a test for *when* it ran, not for what it was chosen from: the same rule faced
2.0 as test #1 and 3.55 as test #544, and a COT test paid for 80 unrelated ORB filters. The tests
here have no order. They are declared from separate mechanisms, so the cumulative count was a
penalty on the ledger's length. The #427–428 precedent and [rty-zscore-dip](rty-zscore-dip.md)
already ran on the right rule; this section makes it the rule.

A multiple-testing correction protects against **picking the best of k results**, so k is the
size of the set the result was selected from:

| Situation | k |
|---|---|
| A declared test with several cells (instruments, horizons, parameter values) where any one passing would be taken | the number of cells |
| A cell that must pass *together* with the others (e.g. "all three index CFDs") | 1 |
| A variant, filter, re-tune or "fix" of an earlier rule | its **family's** cells so far, this one included |
| A rule lifted from an EdgeLab search or any other scan | that scan's trial count (EdgeLab's leaderboard ceiling, invariant 9) |

**Family** = tests of the same mechanism with the same core rule. It is written into the backlog
row **when the row is declared**, before any data is seen, as `Family: <name>, k = <n>`, and a
family's k never goes down. A new mechanism starts a new family. Deciding the family after seeing
the result is the one thing that makes this bar dishonest. When in doubt, the row joins the
existing family.

Rows before #545 carry no family label, and their verdicts stand as recorded. A past lead that
would pass under k is not promoted retroactively: it becomes a candidate only through a new,
declared run, as #427–428 did.

### Programme-level check (reported, not a gate)

The cumulative count still matters, but for the programme, not a single test. Under pure noise,
each test clears t ≥ 2 one-sided with probability about 0.023, so across N tests about
**0.023 × N false passes** are expected from chance on the t gate alone (the IS/OOS, years and
drift gates reduce that, so it is an upper bound). Report it beside the candidate list at the end
of every pass: *N tests, about E false passes expected, P passes recorded.* When P is not well
above E, the forward stages (demo, micro-live) are what separate the edges from the luck, and
none is sized up before they do.

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
2. t on the in-market daily P&L ≥ max(2, sqrt(2 ln k)), k as in *Selection count*.
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
3. Increment N, the family's k, and record n, mean, t, the bar used (k), IS/OOS, the yearly signs
   and the verdict (`fail` / `lead` / `pass`).
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
| — | 2026-10-09 | Backlog 3–5 (pre-FOMC, opex week, pre-holiday) | 3 index CFDs | ≤ 87 possible | | | | | blocked: the ≥ 100-trade bar cannot be met. Not run, not counted. **Unblocked 2026-10-14**, see #524–531 | — |
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
| 408–409 | 2026-10-09 | Backlog 35, day-2 continuation: previous 16:00 → 16:00 ≤ −1% and 09:45 < 09:30 open → short 09:45 → 15:55, stop at the 09:30–09:45 high. | NAS100.fs, S&P.fs | 174 / 132 | **−0.204 / −0.253%** (gross −0.17 / −0.21) | −2.85 / −3.54 | − / − | **0/8, 1/8** | **fail, and the wrong way round: the continuation short loses almost every year.** After a ≥1% down day with a weak first 15 minutes, the index tends to recover into the close. That agrees with the RSI(2) and IBS leads (mean reversion after down days) and with the user's 'the opening range usually fades'. The mirror (a day-2 reversal long) is **post-hoc** here and can only become a candidate through its own pre-declared test on data it was not seen on (e.g. DJ30.fs, or a future forward sample) | `scripts/edges/2026-10-09-35-day2-continuation.py` |
| 410–417 | 2026-10-09 | Backlog 36–39: #400's 5-minute ORB, taken only when (36) the opening tick volume is ≥ 2 × its 14-session mean, (37) the previous day was NR7, (38) the candle direction matches the previous day, or (39) the open gaps ≥ 0.3% in the candle's direction. | NAS100.fs, S&P.fs | 0–921 | see the right-hand column | best +1.31 (39, NAS) | — | ≤ 6/8 | **fail, all 8.**<br>(36) High relative volume: NAS100.fs never fired, because broker tick volume on its opening candle never reached 2× its 14-session mean. S&P.fs: n = 45, **−0.22% per trade, t = −4.3, 0/6 years**. On these CFDs, high-volume opening breakouts reverse, the opposite of the stock-level paper.<br>(37) NR7 prior day: +0.02 / +0.01%, negative OOS.<br>(38) Aligned with the previous day: ≈ 0 / −0.03%.<br>(39) Gap ≥ 0.3% the same way: NAS +0.04%, t = 1.3, 6/8 years (the best, and weak); S&P ≈ 0.<br>No condition known at 09:35 turns the ORB into an edge. The two strongest effects in passes 8–9 (#408–409, #410's S&P cell) both point to **openings reverting**. That is post-hoc and becomes a test only if declared first | `scripts/edges/2026-10-09-36-orb-filters.py` |
| 418–426 | 2026-10-09 | Pass 10: (40) intraday → overnight reversal ×3, (41) Double 7s ×2, (42) small-gap fill ×3, (43) gold Monday NY-session short ×1. Rules as declared in the backlog. | see backlog | 340–1,793 | 40: −0.036 / −0.026 / −0.029% (gross ≈ 0); 41: +0.076 / +0.103% a day; 42: −0.016 / −0.030 / +0.001%; 43: −0.001% | best 1.80 (41, NAS) | — | ≤ 6/8 | **fail, all 9.** (40) Intraday → overnight: gross ≈ 0, the spread is the loss. (41) Double 7s: positive on both (t 1.8, 6/8 years), but no better than buy-and-hold Sharpe; **a third member of the RSI(2)/IBS family pointing the same way**, still under the bar. (42) Small gaps do not fill reliably enough to pay the spread. (43) Gold's Monday weakness is not present | `scripts/edges/2026-10-09-40-pass10.py` |
| 427–428 | 2026-10-09 | Backlog 44, mean-reversion composite on 2000–19 (independent): close > SMA200 and (RSI2 < 10 or IBS < 0.2 or 7-day low) → long, exit at the first close > SMA5. | ^GSPC, ^NDX | 399 / 409 entries | +0.052 / +0.076% a day | 2.37 / **2.72** | + / + (2000–09 and 2010–19) | 15/20, 12/20 | **^NDX passes stage 1 on independent history** (pre-declared, run once: > 2 SE is that gate; √(2 ln N) is the gate for a *search*). → candidate [nas-mean-reversion](nas-mean-reversion.md). ^GSPC: positive, but 2010–19 t = 1.05, so it stays a lead | `scripts/edges/2026-10-09-44-mr-composite.py` |
| 429–430 | 2026-10-11 | [eurusd-burst-fade](eurusd-burst-fade.md) frozen spec, stage 1, run once. z of the M5 close vs the last 144 M5 closes; (A) sell z ≥ 1.5 / buy z ≤ −1.5, (B) sell only. Stop 25 pips, 3-pip trail once +5, 24 h time stop; shorts exit at ask; one extra entry spread + swap. 2019-01 → 2026-04-26 and 2026-07-11 → 10-05 (burst periods excluded). | EURUSD | 16,317 / 8,146 trades, 2,259 / 1,871 days | **−2.01 / −2.05 pips** (1× cost −1.01 / −1.05) | −20.4 / −14.2 (clustered by day) | − / − (2026-07+ −1.3 / −1.6) | **0/8, 0/8** | **fail, both.** Gross before any spread ≈ 0: a 12-hour z-stretch on EURUSD carries no reversion worth a spread. 76% wins at +4.4 against −22.5 losses. Diagnostic only (not a test): the frozen exit applied to the provider's actual 66 sell entries gives +4.0 pips, vs +0.57 for random sells in the same weeks. Whatever selects their entries is not this z rule | `scripts/edges/2026-10-11-eurusd-burst-fade.py` |
| 431 | 2026-10-12 | [eurusd-burst-fade](eurusd-burst-fade.md) loop round 3: the provider's grid structure, frozen and run once. First entry as #429 (z ≥ ±1.5 vs 144 M5 closes); add a unit every 6 pips against, up to 5; close all 5 pips beyond the average; stop 25 pips against the first fill; 24 h time stop; one extra spread per unit. (Rounds 1–2 screened 29 indicator families plus price levels and the clock in-sample: none reached the screen, so none were tested.) | EURUSD | 21,362 baskets | **−2.86 pips × units** (in-sample burst weeks −2.19) | −16.5 | − / − | **0/8** | **fail.** 87% of baskets win and it still loses every year (worst basket −169). Averaging in turns no edge into a high win rate with a fat left tail, not into profit | `scripts/edges/2026-10-12-burst-grid.py` |
| 432–435 | 2026-10-12 | [eurusd-burst-fade](eurusd-burst-fade.md) observe-and-test loop, development set 2019–22 only (holdout 2023–26 untouched). Each iteration frozen from the provider's indicator readings ([observations](eurusd-burst-observations.md)): (1) D1 bearish EMA stack + M15 %B ≥ 0.85 + M5 stochastic ≥ 75, exit M5 stochastic < 20; (2) H1 and M15 %B ≥ 0.8, M5 %B < 0.9, 5/3 trail; (3) H1 and M15 %B ≥ 0.5, trail; (4) as 3, sells only, 8 h hold with a 25-pip stop. Mirrors for buys in 1–3. | EURUSD | 2,725 / 2,180 / 5,428 / 1,355 | **−1.62 / −1.97 / −1.95 / −2.66** pips | −5.8 / −7.2 / −11.9 / −3.9 | — | 0/4, 0/4, 0/4, 1/4 | **fail, all four.** Observation, not a test: iteration-3 sells drift +1.38 ± 0.55 pips at 4 h on development (+0.85 ± 0.48 over 2019–26 ex bursts) with no exit logic, and the buys don't. A real but sub-cost edge: it needs an all-in round trip ≤ ~0.5 pips to be worth a pre-declared test | `scripts/edges/2026-10-12-pullback-loop.py` |
| 436–437 | 2026-10-12 | [eurusd-burst-fade](eurusd-burst-fade.md): two cells from a development-set breakdown (≈55 cells, 2019–22) of the D1-downtrend pullback-sell setup, frozen for the holdout: (5) plus live M5 %B ≥ 1.0; (6) plus price ≤ 0.6 D1 ATR below the D1 EMA50. Sell only, 4 h hold, 40-pip stop. Checked first on development as trades. | EURUSD | 1,457 / 441 | **−1.85 / −2.27** (gross −0.81 / −1.27) | −3.19 / −2.29 | — | 0/4, 0/4 | **fail on the data they were found on; holdout not spent.** The cells' +2 to +3 pip forward drifts came from sampling at M15 closes ≥ 4 h apart and vanish at tradable entry timing. This also retracts the "+1.0 to +1.4 pip drift" noted with #432–435 | `scripts/edges/2026-10-12-pullback-loop.py`, `scripts/edges/2026-10-12-pullback-dose.py` |
| 438 | 2026-10-12 | [eurusd-burst-fade](eurusd-burst-fade.md) entry replicator, exit fixed by the user at $0.50 per 0.01 lot (TP 5 pips; SL 25; 24 h). L2 logistic regression on 15 M5–D1 readings, trained on the provider's 59 sells vs every 07–21 UTC M5 close of the burst weeks; threshold = the provider's rate (≈ 1.3 episodes a day); mirrored for buys. Development 2019–22 only (holdout not loaded). | EURUSD | 10,224 | **−2.03** (in-sample −1.34; the provider's own entries +2.49) | −16.7 | — | 0/4 | **fail.** In-sample AUC 0.83 and recall 0.50: it finds the provider's *kind* of moment (M5 overbought, H1 upper band, D1 downtrend) but its other signals win ~80% against an 87% break-even. The provider's 94% is not in these readings | `scripts/edges/2026-10-12-burst-replicator.py` |
| 439 | 2026-10-12 | [eurusd-burst-fade](eurusd-burst-fade.md) entry replicator #438 unchanged, exit TP 10 pips ($1.00 per 0.01 lot) instead of 5; SL 25; 24 h. Development 2019–22 only (holdout not loaded). | EURUSD | 6,751 | **−1.97** (gross −0.95; in-sample −2.17) | −10.3 | — | 0/4 | **fail.** Wins 68% against a ~74% break-even. Diagnostic: the provider's own 69 entries under TP 10 give +3.44 net at 81% wins (TP 5: +2.13 at 93%), so a wider target suits *their* entries, not the model's | `scripts/edges/2026-10-12-burst-replicator.py --tp 10` |
| 440–523 | 2026-10-13 | T156 COT positioning extremes ([plans/cot/study.md](../../plans/cot/study.md), declared and committed before the run): 3y percentile of a group's net futures position ≥ 90 / ≤ 10; contrarian or follow; hold 5 / 10 / 20 days; enter at the close of the first day after the release; 2 × ATR(20) stop. 14 series: Legacy non-commercial, TFF leveraged funds and asset managers, Disaggregated managed money. Proxies: ^GSPC/^NDX/^DJI (with futures carry and roll spread), GLD, EURUSD=X; 2006/2009 → 2026-10 | 5 CFDs | 74–251 per trial, 29–93 episodes | best +0.29R (EURUSD, Legacy, follow, 20 d) | best 2.56 (same; episode-clustered) | that one + / − | ≤ 15/26 | **fail, all 84; no lead.** Gate counts: count 64, mean > 0 39, t > 2 **1**, years 30, IS and OOS both + 9, above the noise ceiling (1.66; 1.00 for the family alone) **0**. Costs are 0.002–0.02R at these holds, so they do not decide it: the gross signal is ≈ 0. Gold's 'follow' rows (+0.03 to +0.23R) are **below always-long gold** over the same span (+0.07/+0.22/+0.35R) — trend, not positioning. The one t > 2 (EURUSD Legacy follow 20 d) is in-sample only (2009–17 +0.41R, 2018+ −0.04R). Contrarian-on-speculators, the textbook COT reading, is negative on gold and EURUSD at every hold | `app/modules/research/cot_study.py` |
| 524–525 | 2026-10-14 | Backlog 3, pre-FOMC drift (Lucca and Moench 2015), **daily version on independent history**: long from the close of the session before a scheduled FOMC announcement to the announcement day's close (so it also carries the 14:15 reaction). Scheduled meetings from the Fed's historical pages; unscheduled ones excluded. ^GSPC / ^NDX 1994-02 → 2019-06, the CFD's median spread, doubled. | ^GSPC, ^NDX | 199 / 199 | +0.25 / +0.36% | 3.11 / 3.06 | halves + / + | 19/26, 20/26 | **lead** on the bar (3.54), but **decayed after publication**: 1994–2011, the paper's own sample, carries it (t 2.94 / 2.83); 2012–19 is +0.10 / +0.14% (t 0.9 / 1.1), and 2020–26 +0.08 / +0.31% (t 0.4 / 1.3), negative in the later half of each. Broker span S&P.fs / NAS100.fs +0.05 / +0.28% (t 0.3 / 1.2), n = 56. **Not demo-tested under T152:** the t ≥ 2 comes from the sample the effect was published on | `scripts/edges/2026-10-14-03-pre-fomc.py`, split in `…-03b-pre-fomc-post-paper.py` (context, not counted) |
| 526–528 | 2026-10-14 | Backlog 4, opex-week drift (Stivers and Sun 2013), as a position rule: long from the close before the opex week to the close of the third Friday. | 3 index CFDs | 503 / 503 / 374 days, 86 / 86 / 64 entries | −0.051 / −0.018 / −0.031% a day | −0.97 / −0.28 / −0.69 | − / + on all three | 4/8, 5/8, 3/6 | fail: negative, Sharpe far below buy-and-hold | `scripts/edges/2026-10-14-04-opex-week.py` |
| 529–530 | 2026-10-14 | Backlog 5, pre-holiday (Ariel 1990), on independent history: long the close two sessions before a market holiday to the close of the session before it. Holidays are weekdays with no session, minus unscheduled closures (9/11, Sandy, state funerals). ^GSPC / ^NDX 1990-01 → 2019-06. | ^GSPC, ^NDX | 253 / 253 | +0.028 / +0.009% | 0.47 / 0.10 | halves − / + | 15/30, 14/30 | fail: the doubled spread eats a gross +0.07 / +0.04% | `scripts/edges/2026-10-14-05-pre-holiday.py` |
| 531 | 2026-10-14 | Backlog 11, Faber (2007): at each month-end 16:00 close, long the next month if the close > the 10-month SMA of month-end closes, else flat. Position rule. | NAS100.fs | 1,293 days, **4 entries** | +0.088% a day | 2.55 | + / + | 6/7 | fail: 4 entries is four bets, under the 10-entry floor. Sharpe 1.01 vs buy-and-hold 0.84, total +113% vs +122%: the same drawdown-overlay story as #298–300 | `scripts/edges/2026-10-14-11-faber.py` |
| 532 | 2026-10-14 | T149, the frozen [xau-range-rejection](xau-range-rejection.md) rule, run once (a candidate's stage 1, gated at 2 SE). | GLD → XAUUSD | 83 | +0.10R ± 0.10 | 0.98 | + / − | 5/8 | **fail → candidate killed**: < 100 trades, < 2 SE, OOS (≥ 2024) −0.14R. Regime off: −0.07R on 331 | `scripts/edges/2026-10-14-t149-xau-range-rejection.py` |
| 533–534 | 2026-10-14 | The drawdown overlay for [index-hold-sma200](index-hold-sma200.md): SMA200 vs the utilities rotation (#387–389), 2022-02 → 2026-10 (the span XLU/SPY allows). A choice between overlays, so it counts. | NAS100.fs, S&P.fs | 870 vs 651 days; 897 vs 651 | | NAS 1.89 vs 1.65; S&P 1.13 vs 1.81 | | | **comparison, no edge claimed.** NAS100.fs: SMA200 Sharpe 0.88, max DD −14.7%, total +65%; utilities 0.77, −14.5%, +52%; hold 0.63, −32.6%, +69%. S&P.fs: SMA200 0.53, −18.0%, +28%; utilities **0.84, −11.4%, +41%**; hold 0.57, −24.2%, +46%. SMA200 stays on NAS100.fs; on S&P.fs utilities was the better overlay on this one span, which is mostly 2022 | `scripts/edges/2026-10-14-overlay-compare.py` |
| 535 | 2026-10-15 | The frozen [rty-zscore-dip](rty-zscore-dip.md) rule, run once on history EdgeLab never searched (a candidate's stage 1, gated at 2 clustered SE). EdgeLab `zscore_meanrev` at the grid centre (n 40, entry 2.0, exit 0.5, long only) on hourly bars, −4% disaster stop, swap −7%/yr. US2000 M1 2019-07 → 2024-04. | RTY=F → US2000 | 339 over 328 sessions | −0.091% (gross +0.012, cost 0.080, swap −0.023) | −1.18 | − / − (halves) | 3/6 | **fail → candidate killed**: gross edge ≈ 0, the spread is the whole result; low-vol entries worst (−0.19%). Context, not gated: the full grid (895 variants) is 62% positive at zero cost, 20% at EdgeLab's 3 bps/side, 9% at the broker's 4.6 bps. EdgeLab's 78%-positive RTY family read came from its 2024-04+ span only | `scripts/edges/2026-10-15-rty-zscore-dip.py` |
| 536–538 | 2026-10-16 | Backlog 45, rest-of-day momentum (Baltussen, Da, Lammers and Martens 2021). ROD = previous 16:00 → 15:30; if \|ROD\| ≥ 1.0% (floor declared before the run), trade 15:30 → 16:00 in ROD's direction. | 3 index CFDs | 484 / 732 / 281 | −0.047 / −0.027 / −0.042% (gross −0.008 / +0.003 / −0.021) | −1.92 / −1.39 / −2.48 | − / − on all three | 0/8, 2/8, 0/6 | **fail.** No gross effect on large-move days either: the last half hour does not continue the day on these CFDs (2019–26), long or short, and the spread is the whole loss, as in #307–309 | `scripts/edges/2026-10-16-45-rod-momentum.py` |
| 539–540 | 2026-10-16 | Backlog 46, turn of the month on independent history: #295–297's rule unchanged (close of the month's last trading day → close of the 3rd trading day), ^GSPC / ^NDX 1990-01 → 2019-06, the CFD's median spread doubled. | ^GSPC, ^NDX | 353 / 353 | +0.251 / +0.451% (gross +0.288 / +0.478) | 2.54 / 2.71 | halves + / + (1990–2004 t 2.28 / 2.04; 2004–19 t 1.26 / 1.96) | 21/30, 22/30 | **lead (both), below the bar of 3.55.** Criterion 5: beats every 3-session hold by +0.186 / +0.297%, but that difference is only t 1.85 / 1.75. The effect weakens in the second half. With #295–297 (+, t < 1.2 on 2019–26) the sign holds across 36 years, the size does not clear the bar | `scripts/edges/2026-10-16-46-turn-of-month-independent.py` |
| 541–542 | 2026-10-16 | Backlog 47, month-end rebalancing, the Calendar signal of Harvey, Mazzoleni and Melone (2025), equity leg only: 60/40 ^GSPC / IEF reset at each month end; last 5 trading days hold −sign(drift), first day of the month hold sign(drift on the 4th-last day). Position rule. | S&P.fs, NAS100.fs | 593 days, 183 entries | +0.011 / +0.000% a day | 0.26 / 0.00 | ≈ 0 / + | 5/8, 4/8 | **fail.** Sharpe 0.10 / 0.00 vs buy-and-hold 0.66 / 0.79. Inside the paper's sample (2019-07 → 2023-03) −0.010 / −0.030% a day; after it +0.043 / +0.036% (t 0.84 / 0.49). The equity leg alone carries nothing on the CFDs; the paper's spread against 10-year futures is not tradable here | `scripts/edges/2026-10-16-47-month-end-rebalancing.py` |
| 543–544 | 2026-10-16 | Backlog 48, even weeks of the FOMC cycle (Cieslak, Morse and Vissing-Jorgensen 2019): long the returns of days −1..3, 9..13, 19..23, 29..33 after each scheduled announcement, flat otherwise and in days −6..−2. Position rule, 55 meetings, after the paper's sample. | S&P.fs, NAS100.fs | 965 days, 170 entries | −0.009 / +0.017% a day | −0.23 / +0.37 | ≈ 0 / ≈ 0 | 5/8, 6/8 | **fail. The FOMC-cycle premium is not present after publication** (2019–26), the same decay as pre-FOMC drift (#524–525). Sharpe −0.09 / 0.14 vs buy-and-hold 0.66 / 0.79 | `scripts/edges/2026-10-16-48-fomc-cycle.py` |
| 545–546 | 2026-10-16 | Backlog 50, rest-of-day momentum only when dealers are short gamma (Baltussen et al. 2021, Table 7): on sessions where SqueezeMetrics `gex` for t−1 < 0, trade 15:30 → 16:00 in the direction of previous 16:00 → 15:30, no floor. | S&P.fs, NAS100.fs | 199 / 199 | +0.049 / +0.048% (gross +0.092 / +0.082) | 0.91 / 0.79 | + / + | 3/8, 5/8 | **fail.** The sign matches the paper: gross +0.09% in short gamma against −0.003% on the 1,594 long-gamma sessions (context, not counted; net −0.039%, t −6.2, the spread). But 2020 carries the whole total (+12.0 / +10.1 of +10.0 / +9.5%); without it the sum is −2.0 / −0.6%. The effect is real in a crash and too rare and too small after costs to trade | `scripts/edges/2026-10-16-50-rod-short-gamma.py` |
| 547 | 2026-10-16 | Backlog 52, the #428 mean-reversion composite with entries only when SqueezeMetrics `gex` for t−1 > 0. ^NDX 2011-05 → 2026-10, position rule. | ^NDX | 1,612 days, 425 entries | +0.097% a day | 3.47 | + / + | 13/16 | **fail, the wrong way round.** The filter removes 3 of 428 entries and lowers the mean (unfiltered +0.098%, t 3.49). The excluded short-gamma entries do *better*: +0.31% a day over 29 entries (t 2.09). Long gamma is not the regime for buying dips; if anything, short-gamma dips rebound harder. That reading is post-hoc on 29 entries and is not a rule | `scripts/edges/2026-10-16-52-mr-positive-gamma.py` |
| 548–549 | 2026-10-16 | Backlog 53, the user's 4h desk strategy (family `4h-ema-macd-trend`, k = 2, bar 2.0): 4h candles from 17:00 NY; long at a close > EMA20 with EMA20 > EMA50 and MACD(8,17) > 0; initial stop one spread below the signal candle's low; recovered pullbacks lift the stop; exit on the stop or a 4h close < EMA20. Position rule on daily P&L, per-trade R beside it. Cell A = three indices together, cell B = XAUUSD | 3 index CFDs; XAUUSD | 506–735 trades, 650–1,014 days | R −0.05 / **+0.25** / −0.20; gold −0.02 (corrected in #554–559) | daily −0.09 / 2.07 / −0.64; gold 0.99 | S&P − / +, NAS + / +, DJ − / −, gold − / + | 6/8, 7/8, 2/6, 5/8 | **fail, both cells.** 78–82% of trades stop out within ~½ day (median −1R): the signal-candle-low stop sits inside one candle's range. NAS100.fs clears t but loses to buy-and-hold on Sharpe (0.78 vs 0.82) with less than half its return, the daily trend rules' pattern again. [4h-desk-trend](4h-desk-trend.md) | `scripts/edges/2026-10-16-53-4h-desk-trend.py` |
| 550–553 | 2026-10-16 | Backlog 54, #548–549 with a wider initial stop (family `4h-ema-macd-trend`, k = 6, bar 2.0): (a) entry − 2 × ATR(14) of 4h; (b) lowest low of the last 5 candles − one spread. Everything else unchanged | 3 index CFDs; XAUUSD | 362–583 trades | (a) R −0.01 / +0.07 / −0.03, gold +0.07; (b) −0.01 / **+0.28** / −0.12, gold +0.08 (corrected in #554–559) | daily (a) 0.34 / 1.65 / −0.30, gold 0.74; (b) 0.22 / 1.89 / −0.09, gold 0.99 | NAS + / + both; S&P and gold IS −; DJ OOS − | (a) 6/8, 6/8, 3/6, 4/8; (b) 6/8, 6/8, 3/6, 4/8 | **fail, all four.** Stop-outs fall from ~80% to 47–62% and the hold doubles, but daily P&L does not improve: NAS100.fs daily t drops from 2.07 to 1.65 / 1.89, and every cell stays below buy-and-hold Sharpe (NAS 0.62 / 0.71 vs 0.82). The tight stop was not what held the rule back; the gross edge is the index drift. [4h-desk-trend](4h-desk-trend.md) | `scripts/edges/2026-10-16-53-4h-desk-trend.py <data> atr2\|swing5` |
| 554–559 | 2026-10-16 | Backlog 55, #548–553 plus a take-profit at entry + 0.5 × initial risk (stop first if one M1 bar touches both), for the three stops (family `4h-ema-macd-trend`, k = 12, **bar 2.23**) | 3 index CFDs; XAUUSD | 758–2,728 trades | candle −0.22 / −0.12 / −0.16, gold −0.10; atr2 −0.03 / −0.01 / −0.04, gold −0.01; swing5 −0.09 / −0.02 / −0.07, gold −0.02 | daily candle −5.05 / −2.24 / −3.21, gold −0.68; atr2 −0.82 / 0.10 / −1.13, gold −0.71; swing5 −1.58 / 0.14 / −0.88, gold 0.18 | − in IS for all but NAS (atr2, swing5) | 0–5 of 6–8 | **fail, all six, and worse than no target.** Win rates 54–65% against the ≈ 67% a 0.5:1 payoff needs before costs; gross per trade +0.006 to +0.041% against 0.017–0.036% of doubled spread. The target cuts off the rare long trend that carried the no-target NAS100 runs (mean MFE 2.7R → 0.4R). Also corrects #548–553's per-trade R: gap fills (entry at or below the stop) had a negative initial risk, which flipped their R positive; R is now undefined for them. NAS100 +0.26 → +0.25R (candle), +0.29 → +0.28R (swing5); verdicts unchanged (they rest on daily P&L). [4h-desk-trend](4h-desk-trend.md) | `scripts/edges/2026-10-16-53-4h-desk-trend.py <data> <stop> 0.5` |
| 560–565 | 2026-10-16 | Backlog 56, #548 (candle stop, EMA20 exit) with exits that keep open profit: (a) be1 breakeven at +1R; (b) atr3 chandelier, highest 4h high − 3 × ATR(14); (c) be1atr3. Random-entry control: 200 draws per cell, same stop and exits. Family `4h-ema-macd-trend`, k = 18, **bar 2.40** | 3 index CFDs; XAUUSD | 436–930 trades | NAS100 %/trade +0.060 / **+0.124** / +0.085 (random p95 +0.049 / +0.090 / +0.060); S&P −0.009 / +0.007 / +0.003; DJ −0.024 / −0.030 / −0.029; gold +0.017 / +0.059 / +0.024 | daily NAS 1.87 / **2.25** / 2.05; S&P −0.37 / 0.17 / 0.08; DJ −1.05 / −0.82 / −1.06; gold 0.53 / 1.24 / 0.71 | NAS + / + all; gold IS − all | NAS 7/8 all | **fail, all six cells; NAS100.fs atr3 is a lead** (IS/OOS +, 7/8 years, Sharpe 0.85 > B&H 0.82, signal at the 99th random percentile; only t 2.25 < 2.40, and cell A needs all three indices). **The NAS100 entry beats random entries with every exit, row 53's included (p99–p100)**; S&P.fs (p64–83), DJ30.fs (p26–32) and gold (p60–80) do not. Breakeven hurts everywhere: stop-outs at entry, re-entries, more spread. Exits still keep only 4–13% of MFE. [4h-desk-trend](4h-desk-trend.md) | `scripts/edges/2026-10-16-56-4h-desk-exits.py`, `_desk4h.py` |
