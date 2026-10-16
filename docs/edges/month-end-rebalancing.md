# month-end-rebalancing: front-run pension rebalancing at month end

**Stage 1 failed (2026-10-16, ledger #541–542). Killed.** The equity leg of the Calendar
signal earns +0.011% (S&P.fs) and +0.000% (NAS100.fs) a day in the market. Its Sharpe is 0.10 /
0.00, against buy-and-hold at 0.66 / 0.79.

## Spec (frozen 2026-10-16, before the run)

| Field | Value |
|---|---|
| Hypothesis | When stocks have beaten bonds since the last month end, balanced funds sell stocks in the last week of the month (and buy after bonds won), then the pressure reverses on the first day of the next month. |
| Mechanism | Calendar rebalancing of 60/40 portfolios back to target at month end. |
| Instruments | S&P.fs, NAS100.fs. The paper trades S&P futures against 10-year note futures. The desk has no bond CFD, so only the equity leg is tested. |
| Signal | A simulated 60/40 portfolio of ^GSPC and IEF (adjusted close, the 10-year proxy), reset to 60% at each month's last close. Cal_t = equity weight − 0.60. |
| Entry / exit | When t is in the month's last 5 trading days, hold −sign(Cal_t) from close t to close t+1. On the first trading day of a month, hold sign(Cal on the previous month's 4th-last trading day). Otherwise flat. |
| Regime condition | Always. |
| Costs assumed | One recorded spread per position change (the doubled round trip), `.fs` roll steps removed. |
| Search count | 2 tests (#541–542); N = 542 after them. |
| Source | Harvey, Mazzoleni and Melone (2025), *The Unintended Consequences of Rebalancing*, NBER w33554, §4 and Appendix B. |

## Result (position rule, 2019-07 → 2026-10)

| | S&P.fs | NAS100.fs |
|---|---|---|
| Days / entries | 593 / 183 | 593 / 183 |
| Mean per day, t | +0.011%, 0.26 | +0.000%, 0.00 |
| Inside the paper's sample (→ 2023-03-17) | −0.010%/day | −0.030%/day |
| After it | +0.043%/day, t 0.84 | +0.036%/day, t 0.49 |
| Sharpe vs buy-and-hold | 0.10 vs 0.66 | 0.00 vs 0.79 |

The paper's result is a **spread** between equities and bonds. The equity leg alone carries
nothing here, even inside the paper's own sample period. Re-testing as a spread would need a
bond instrument, and the desk's instrument set has none today. That is a new candidate, not this one.
