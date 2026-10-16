# rod-momentum: the last half hour follows the day

**Stage 1 failed (2026-10-16, ledger #536–538). Killed.** On days when the move from the previous
close to 15:30 is ≥ 1%, the 15:30 → 16:00 trade in that direction loses −0.027% to −0.047% per
trade after costs on all three index CFDs. The gross effect is about zero or negative, 0–2 of
8 years are positive, and OOS is worse than IS.

## Spec (frozen 2026-10-16, before the run)

| Field | Value |
|---|---|
| Hypothesis | The return from the previous close to 15:30 (ROD) predicts the 15:30 → 16:00 return, with the same sign. |
| Mechanism | End-of-day hedging. Option dealers short gamma, and leveraged ETFs, must trade in the direction of the day's move near the close, in proportion to its size. |
| Instruments | S&P.fs, NAS100.fs, DJ30.fs. |
| Entry | 15:30 NY, when \|ROD\| ≥ 1.0%, in ROD's direction. The 1% floor is the desk's choice, declared before the run. The paper uses all days. |
| Exit | 16:00 NY. |
| Regime condition | Always (the \|ROD\| floor is the only filter). |
| Costs assumed | The recorded spread at entry, doubled. |
| Search count | 3 tests (#536–538); N = 538 after them. Related: #307–309 (first half hour as the predictor) and #323–325 (volatile mornings), both failed. |
| Source | Baltussen, Da, Lammers and Martens (2021), *JFE* 142, 377–403; Cheng and Madhavan (2009) for the LETF flow. |

## Result

| | S&P.fs | NAS100.fs | DJ30.fs |
|---|---|---|---|
| Trades | 484 | 732 | 281 |
| Net mean (gross) | −0.047% (−0.008) | −0.027% (+0.003) | −0.042% (−0.021) |
| t | −1.92 | −1.39 | −2.48 |
| IS / OOS | −0.043 / −0.055% | −0.011 / −0.057% | −0.029 / −0.056% |
| Years positive | 0/8 | 2/8 | 0/6 |

Longs and shorts both lose. On these CFDs from 2019 to 2026, the last half hour does not
continue a large day. A gamma-conditioned version (in negative gamma only) is the paper's
stronger claim. It needs daily dealer gamma history, which T166 will provide. If it is
wanted, it becomes a new, declared backlog row; it is not a rescue of this one.
