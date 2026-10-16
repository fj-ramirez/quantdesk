# fomc-cycle: hold the index only in even weeks of the FOMC cycle

**Stage 1 failed (2026-10-16, ledger #543–544). Killed.** After the paper's sample, holding
S&P.fs / NAS100.fs only in even FOMC-cycle weeks earns −0.009% / +0.017% a day (t −0.23 /
+0.37). Its Sharpe is −0.09 / 0.14, against buy-and-hold at 0.66 / 0.79. This is the same
post-publication decay as pre-FOMC drift (#524–525).

## Spec (frozen 2026-10-16, before the run)

| Field | Value |
|---|---|
| Hypothesis | The equity premium is earned in weeks 0, 2, 4 and 6 of the FOMC cycle. |
| Mechanism | The paper's: Fed news (board meetings, leaks, accommodation) arrives bi-weekly, and an unexpectedly accommodating Fed lowers the risk premium. |
| Instruments | S&P.fs, NAS100.fs. |
| Entry / exit | Trading days counted from each **scheduled** announcement (day 0). Long the close-to-close return of day d when d is in −1..3, 9..13, 19..23 or 29..33. Flat in odd weeks and in the 5 days before the next meeting (−6..−2). The schedule is public, so the position is known in advance. |
| Regime condition | Always. |
| Costs assumed | One recorded spread per position change, `.fs` roll steps removed. |
| Search count | 2 tests (#543–544); N = 544 after them. |
| Source | Cieslak, Morse and Vissing-Jorgensen (2019), *JF* 74(5), 2201–2248. The week definitions are from the paper's text. Meeting dates come from the Fed's historical pages (2019–20) and `fomccalendars.htm` (2021+). |

## Result (position rule, 2019-07 → 2026-10, 55 meetings)

| | S&P.fs | NAS100.fs |
|---|---|---|
| Days / entries | 965 / 170 | 965 / 170 |
| Mean per day, t | −0.009%, −0.23 | +0.017%, +0.37 |
| Years positive | 5/8 | 6/8 |
| Sharpe vs buy-and-hold | −0.09 vs 0.66 | 0.14 vs 0.79 |
