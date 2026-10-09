# Edge search log

The ledger for every hypothesis tested against the desk's history, **failures included**. It
is the denominator that makes a pass mean something: a strategy that clears 2 SE after 300
tries is about what chance produces. The research loop (see *Loop protocol*) appends here.

**Cumulative tests: 343** (288 T147 + 18 effect tests on 2026-10-09, the session decomposition
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
