# rty-zscore-dip: buy hourly z-score dips on US2000

**Stage: 1 failed (2026-10-15, ledger #535). Killed.** On 339 trades the rule loses −0.091% per
trade after costs, t −1.18 clustered by session. Its gross edge is +0.012%, about zero, so the
doubled spread is the whole result. Every gate fails except the trade count. See *Stage-1 result*.

**Spec frozen** 2026-10-15, before the stage-1 run, on independent history (US2000 M1,
2019-07-22 → 2024-04-26). **Source:** the EdgeLab family read of
2026-10-15. Of the four index futures, only RTY=F 1h `zscore_meanrev` is positive as a
*family*: 323 variants, mean OOS Sharpe +0.93, 78% positive. ES/NQ/YM 1h are centred on zero.
No row clears its noise ceiling (5.6–5.8 at 176,591 trials), and the family's IS mean is −0.16.

## Why this test, and the relaxed gate

The user asked to relax the noise ceiling (2026-10-15). It is not lowered. Instead the test
changes: **one rule, declared here, run once on history EdgeLab never saw.** The gate is
then 2 standard errors, clustered by session. That follows the #427–428 precedent (`search-log.md`):
√(2 ln N) is the bar for a *search*, and a pre-declared single run is not one. The `search-log.md`
bar t ≥ √(2 ln 535) = 3.54 is reported alongside, for comparison only.

**The rule is the grid's centre, not its best row.** Each parameter takes the middle value of
EdgeLab's `ZScoreMeanRev.param_space`. It was chosen without looking at this rule's own EdgeLab
result, which is recorded only after the run, in the stage log.

## Spec (frozen)

| Field | Value |
|---|---|
| Hypothesis | On US small caps, an hourly close 2 standard deviations below its 40-hour mean is followed by a rebound toward the mean. |
| Mechanism | Liquidity provision. Small caps are the least liquid of the four US index futures, so short-term selling pressure overshoots further and reverts more. Dealers who are long gamma also buy dips as they hedge. *Reasoning, not tested here.* |
| Instruments | Desk research symbol RTY=F (EdgeLab, Yahoo 1h) → **US2000** (Axi **cash** index CFD, `ROW_CASH`, so no rolls and no roll gap). Same index, so the mapping is valid. The CFD trades nearly 24h, as the future does. |
| Bars | US2000 M1 (bid), resampled to 1-hour bars on the UTC clock (`[h, h+1)`), close = the last M1 close. Hours with no M1 bar don't exist. |
| Entry | `FAMILIES["zscore_meanrev"].positions` from `app/modules/research/strategies.py`, the code EdgeLab scores, with **n = 40, entry_z = 2.0, exit_z = 0.5, long_short = False, regime = any**. z = (close − SMA40) / std40 on hourly closes. Go long when z < −2.0 at an hourly close. Fill at the open of the first M1 bar at or after that close (gaps fill at the next open, however late). |
| Exit | When z > −0.5 at an hourly close, fill at the next M1 open, same convention. **Disaster stop:** −4% from the entry fill, checked on each M1 low and filled at the stop, or at the open if the bar opens below it. After a stop, no new entry until the rule's own exit condition has fired. A trade still open at the window's end is dropped. |
| Regime condition | Always on. |
| Costs assumed | **Twice the recorded M1 spread at entry** (one spread per round trip, doubled). **Swap:** long at today's −7% a year on the open price (`swap_mode` 6, interest on open price), /360 per night. A night is each weekday 17:00 New York crossing, Friday counting three (`swap_rollover3days` = 5). Today's rate is applied to 2019–24, when rates were lower, which makes the cost conservative. |
| Search count | EdgeLab: **176,591 trials**, of which **323** are this family on RTY=F 1h. Desk ledger: this run is **#535**. The family grid on the same window is run as **context only**: not gated, and nothing is picked from it. |
| Kill rules | See below. Written now, replaced with bootstrap values after stage 1 if it passes. |

## Stage-1 gate (all must hold)

1. ≥ 100 trades over ≥ 30 sessions (a session is the New York date of entry + 6 h, so the
   18:00 reopen counts as the next day).
2. Mean net % per trade > 0 after doubled spread and swap.
3. Mean > 2 standard errors, **clustered by session**.
4. Walk-forward: net positive in more than half of the calendar years (2019 H2 … 2024 to April,
   6 slots), and positive in both halves (2019-07 → 2021-12 and 2022-01 → 2024-04). No re-tuning.
5. Not drift: the mean after subtracting the window's average hourly return × hours held is
   > 0. A long-only rule must beat simply being long for the same time.

Reported, not gated: the high-vol and low-vol split (EdgeLab's `regime_mask`), the stop count,
the exposure, and $ per month at 0.1 lot (the US2000 minimum: $0.10 per point).

## Kill rules (provisional, for stage 2 if reached)

1. **Disaster stop** −4% on every order (the executor requires one).
2. **Drawdown:** pause beyond the stage-1 bootstrap's 95th-percentile drawdown for the same
   number of trades.
3. **Losing streak:** pause beyond the bootstrap's 99th-percentile streak.
4. **Expectancy decay:** after 30 trades, pause if the mean over the last 30 + 1 SE < 0.
5. **Cost drift:** pause if rolling live cost per trade > 1.5× the stage-1 median spread.

## Stage-1 result (run once, 2026-10-15)

US2000 M1 2019-07-22 → 2024-04-26, 28,098 hourly bars.

| | Value |
|---|---|
| Trades / sessions | 339 / 328. Median hold 20 h, in the market 22% of the time, 20 disaster stops |
| Net mean per trade | **−0.091%**, clustered SE 0.077, **t −1.18** |
| Gross / cost / swap | +0.012% / 0.080% / −0.023% |
| Drift-adjusted | −0.108%, t −1.39 |
| Halves | 2019-07 → 2021-12: −0.112% (n 176). 2022-01 → 2024-04: −0.068% (n 163) |
| Years (sum %) | 2019 −6.5 · 2020 −13.5 · 2021 +0.2 · 2022 −17.8 · 2023 +5.5 · 2024 +1.3 |
| Vol regime at entry | high_vol −0.031% (n 213), low_vol −0.192% (n 126) |
| $ / month at 0.1 lot | −1.00 |

Gates: 1 pass; 2, 3, 4 and 5 **fail**.

**Context, not gated:** the full grid, 895 variants with ≥ 20 fills, run with EdgeLab's own
engine on the same hours, without swap:

| Cost per side | Mean Sharpe | Share positive |
|---|---|---|
| 0 | +0.09 | 62% |
| 3 bps (EdgeLab's futures cost) | −0.37 | 20% |
| 4.6 bps (US2000 median recorded spread) | −0.62 | 9% |

**Read:**
- Hourly z-score dips on small caps have a gross effect of about zero. It is too small to survive
  even EdgeLab's cheaper cost assumption, and nowhere near the broker's spread.
- EdgeLab's 78%-positive family read came from one 9-month OOS stretch, 2026-01 onward. On 2019–24
  the same family is negative.
- This rule (`regime: any`) was never sampled by EdgeLab. Its three regime siblings scored OOS
  Sharpe +0.68 (high_vol), +1.78 (trend_up) and +1.28 (low_vol) on the 2024-04+ span.

## Stage log

| Date | Event |
|---|---|
| 2026-10-15 | Spec frozen. Stage-1 run next: `scripts/edges/2026-10-15-rty-zscore-dip.py`, ledger #535. |
| 2026-10-15 | **Stage 1 failed** (run once): −0.091% per trade, t −1.18 on 339. **Killed.** A regime-filtered or cheaper-cost variant would be a new candidate, and the context grid gives no reason to declare one. |
