# 4h-desk-trend: the user's 4h EMA/MACD trend rule

**Stage 1 failed (2026-10-16, ledger #548–565), but not dead: NAS100.fs with the ATR3 trail is a lead (#562),
and on NAS100.fs the entry beats random entries with every exit tested.** S&P.fs, DJ30.fs and XAUUSD show no entry information. Neither cell passes.
Cell A (S&P.fs, NAS100.fs, DJ30.fs together) fails: S&P.fs and DJ30.fs are flat to negative
after costs, and NAS100.fs, the best of the three, has a lower Sharpe than holding the index
(0.78 vs 0.82) with less than half its return. Cell B (XAUUSD) fails: IS negative, t 0.99.

Trade workbooks (every S&P.fs and NAS100.fs trade, MAE/MFE in points, $ and R), local and
not in git: `backend/data/edges/4h-desk-trend-stops-and-targets.xlsx` (rows 53–55) and
`backend/data/edges/4h-desk-trend-exits.xlsx` (row 56, with the random control). Built by
`scripts/edges/workbooks/` (`uv run --with openpyxl python …`).

## Spec (frozen 2026-10-16, before the run)

| Field | Value |
|---|---|
| Hypothesis | On 4h candles, a close above EMA20, with EMA20 above EMA50 and the MACD line above 0, marks a trend that continues long enough to pay a trailed stop. |
| Mechanism | Trend persistence (time-series momentum) at the swing horizon. |
| Instruments | S&P.fs, NAS100.fs, DJ30.fs (cell A, must pass together); XAUUSD (cell B). Traded directly; no desk-symbol mapping. |
| Bars | 4h candles from broker M1, anchored at 17:00 New York (the Axi server day: 17/21/01/05/09/13 NY, as MT5 draws them). |
| Entry | Long, while flat, at a 4h close with close > EMA20, EMA20 > EMA50 and MACD line (EMA8 − EMA17) > 0. Filled at the next M1 open. The MACD signal line (9) plays no part in "MACD > 0". |
| Initial stop | One entry spread below the signal candle's low (the user's choice, 2026-10-16). |
| Trail | A pullback starts at the first candle after entry that closes below the previous close (closes still > EMA20) and runs until a candle closes above the pullback's highest high. At that close the stop moves to the pullback's lowest low minus one spread, if higher. |
| Exit | First of: the M1 low touches the stop (fill at the stop, or the M1 open if it gapped through); a 4h close < EMA20 (next M1 open). |
| Regime condition | Always. |
| Costs assumed | Twice the entry spread; `.fs` roll steps removed; XAUUSD long swap per 17:00 crossing, triple on Wednesday. |
| Search count | Family `4h-ema-macd-trend`, k = 2 (k = 6 after row 54, 12 after row 55, 18 after row 56), bar t ≥ 2.0. Related, earlier families on daily bars: 200-day SMA (#298–300), golden cross (#330–333), TS momentum, Donchian. All failed or stayed below buy-and-hold. |
| Score | *Position rules* bar on daily P&L marked at 16:00 NY; per-trade R (net move ÷ initial risk) reported beside it, SE clustered by entry session. |
| Source | The user, 2026-10-16. |

## Result

Broker M1 2019-07 → 2026-10-09 (DJ30.fs from 2021-06). Script:
`scripts/edges/2026-10-16-53-4h-desk-trend.py`.

| | S&P.fs | NAS100.fs | DJ30.fs | XAUUSD |
|---|---|---|---|---|
| Trades | 735 | 701 | 506 | 648 |
| Win rate | 24% | 25% | 21% | 21% |
| Exits: stop / EMA20 / gap | 586 / 149 / 0 | 572 / 125 / 4 | 391 / 114 / 1 | 505 / 138 / 5 |
| Median hold | 0.5 d | 0.5 d | 0.5 d | 0.4 d |
| Mean R ± clustered SE | −0.05 ± 0.14 | **+0.25 ± 0.14** | −0.20 ± 0.10 | −0.02 ± 0.14 |
| Net % per trade (gross, cost) | −0.003 (+0.034, 0.037) | +0.088 (+0.117, 0.029) | −0.019 (+0.001, 0.021) | +0.043 (+0.101, 0.019, swap −0.039) |
| Doubled cost in R | 0.22 | 0.11 | 0.14 | 0.14 |
| Daily t (in-market days) | −0.09 (1,014 d) | 2.07 (981 d) | −0.64 (650 d) | 0.99 (880 d) |
| IS / OOS mean per day | −0.014 / +0.017% | +0.072 / +0.048% | −0.011 / −0.018% | −0.024 / +0.107% |
| Years positive | 6/8 | 7/8 | 2/6 | 5/8 |
| Sharpe vs buy-and-hold | −0.03 vs 0.69 | 0.78 vs 0.82 | −0.28 vs 0.40 | 0.38 vs 0.30 |
| Total vs buy-and-hold | −2.0 vs +96.5% | +61.5 vs +141.8% | −9.8 vs +31.1% | +28.0 vs +38.2% |
| Verdict | fail | fail (criterion 5) | fail | fail (IS < 0, t < 2) |

What it shows:

- **The initial stop decides the rule.** The signal candle's low is a median 0.3–0.4% below
  entry, inside one 4h candle's normal range, so 78–82% of trades are stopped within about half
  a day, and the median trade is −1R. On NAS100.fs the 125 EMA20 exits average +1.08R and the
  572 stop exits +0.07R. (Per-trade R excludes the few gap fills, whose initial risk is ≤ 0; see #554–559.) Whatever trend the entry catches, the stop cuts most of it off.
- **NAS100.fs is the index drift, not timing.** It is in the market 56% of sessions, earns
  less than half of buy-and-hold, and still loses to it on Sharpe. Same pattern as the daily
  trend rules in passes 2–3: lower 2022 drawdown (−8% vs −36%), lower total return.
- **S&P.fs and DJ30.fs: the gross edge is about the cost.** Per trade, gross +0.034% against
  0.037% of doubled spread on S&P.fs, and +0.001% against 0.021% on DJ30.fs.
- **XAUUSD's OOS +0.107% a day is 2024–25 gold's run** (buy-and-hold +15% and +46% in those
  years). IS is negative, and long swap takes 0.04% a trade.

## Wider stops (backlog 54, #550–553, declared 2026-10-16 before the run)

Row 53 unchanged except the initial stop. Family k = 6, bar t ≥ 2.0.
(a) `atr2`: entry − 2 × ATR(14) of 4h (Wilder). (b) `swing5`: lowest low of the last 5 candles
(signal candle included) − one spread.

| | S&P.fs | NAS100.fs | DJ30.fs | XAUUSD |
|---|---|---|---|---|
| Trades (a / b) | 533 / 583 | 497 / 550 | 362 / 393 | 464 / 511 |
| Stop exits (a / b) | 48% / 62% | 51% / 66% | 43% / 59% | 42% / 57% |
| Median initial risk (a / b) | 0.83 / 0.61% | 1.14 / 0.81% | 0.82 / 0.59% | 1.02 / 0.70% |
| Mean R ± clustered SE (a) | −0.01 ± 0.04 | +0.07 ± 0.05 | −0.03 ± 0.05 | +0.07 ± 0.06 |
| Mean R ± clustered SE (b) | −0.01 ± 0.09 | +0.28 ± 0.14 | −0.12 ± 0.08 | +0.08 ± 0.10 |
| Daily t (a / b) | 0.34 / 0.22 | 1.65 / 1.89 | −0.30 / −0.09 | 0.74 / 0.99 |
| IS / OOS per day (b) | −0.005 / +0.021% | +0.072 / +0.038% | +0.008 / −0.011% | −0.020 / +0.103% |
| Sharpe (a / b) vs B&H | 0.13 / 0.08 vs 0.69 | 0.62 / 0.71 vs 0.82 | −0.13 / −0.04 vs 0.40 | 0.28 / 0.37 vs 0.30 |
| Total (a / b) vs B&H | +7.8 / +5.2 vs +96.5% | +50.5 / +57.1 vs +141.8% | −4.8 / −1.4 vs +31.1% | +21.7 / +28.3 vs +38.2% |
| Verdict | fail | fail (t, criterion 5) | fail | fail (IS < 0, years 4/8) |

**The stop was not what held the rule back.** The wider stops cut stop-outs from about 80% to
42–66% and double the median hold, but the daily P&L does not improve: NAS100.fs drops from
t 2.07 to 1.65 / 1.89, and every cell stays below buy-and-hold Sharpe. (b)'s NAS100 per-trade
+0.28R (t 2.04) is one leg of a cell that must pass on all three indices, and per-trade R is not
comparable across stops because the denominator changes. The daily % is the comparable line.
What the entry catches on the indices is the index drift, collected on about 56% of sessions,
and on gold it is 2024–25's run with IS negative.

## 0.5:1 target (backlog 55, #554–559, declared 2026-10-16 before the run)

Rows 53/54 plus a take-profit at entry + 0.5 × initial risk, filled at the target; the stop is
taken first when one M1 bar touches both. The trail and the EMA20 exit stay. Family k = 12,
**bar t ≥ 2.23**.

| Daily t / Sharpe (total %) | S&P.fs | NAS100.fs | DJ30.fs | XAUUSD |
|---|---|---|---|---|
| Candle + 0.5R | −5.05 / −1.90 (−83.7) | −2.24 / −0.84 (−46.8) | −3.21 / −1.41 (−37.5) | −0.68 / −0.26 (−12.4) |
| 2×ATR + 0.5R | −0.82 / −0.31 (−17.2) | 0.10 / 0.04 (+2.7) | −1.13 / −0.50 (−16.6) | −0.71 / −0.27 (−18.2) |
| Swing5 + 0.5R | −1.58 / −0.60 (−33.0) | 0.14 / 0.05 (+3.7) | −0.88 / −0.39 (−12.1) | 0.18 / 0.07 (+4.4) |
| Buy-and-hold Sharpe (total %) | 0.69 (+96.5) | 0.82 (+141.8) | 0.40 (+31.1) | 0.30 (+38.2) |

| Per trade | Trades | Win rate | Mean R ± clustered SE | Gross / doubled cost per trade |
|---|---|---|---|---|
| S&P.fs candle / atr2 / swing5 | 2,728 / 1,181 / 1,255 | 60 / 57 / 57% | −0.22 ± 0.02 / −0.03 ± 0.02 / −0.09 ± 0.02 | +0.006 / +0.022 / +0.010% vs 0.036% |
| NAS100.fs | 2,623 / 1,158 / 1,210 | 64 / 58 / 60% | −0.12 ± 0.02 / −0.01 ± 0.02 / −0.02 ± 0.02 | +0.011 / +0.031 / +0.032% vs 0.029% |
| DJ30.fs | 1,629 / 758 / 810 | 61 / 54 / 56% | −0.16 ± 0.02 / −0.04 ± 0.02 / −0.07 ± 0.02 | −0.002 / −0.001 / +0.006% vs 0.021% |
| XAUUSD | 2,574 / 1,126 / 1,160 | 65 / 58 / 60% | −0.10 ± 0.02 / −0.01 ± 0.02 / −0.02 ± 0.02 | +0.019 / +0.022 / +0.041% vs 0.017–0.018% (+ swap) |

**Worse than no target, on every stop.** A 0.5:1 payoff breaks even at a ≈ 67% win rate
before costs, and the target lifts the win rate only to 54–65%. The gross move per trade
falls to about the doubled spread, because the target caps the winners at 0.5R and removes
the rare long trend that carried the no-target NAS100 runs (mean MFE 2.7R → 0.4R). With the
candle stop the trade count nearly quadruples (exits come within hours and the entry
condition is usually still true), so the spread is paid four times as often.

Correction made with this run: in #548–553 a gap fill (entry at or below the stop) had a
negative initial risk, which turned its loss into a positive R. R is now undefined for gap
fills (they stay in the daily P&L at full cost). NAS100.fs moves from +0.26 to +0.25R (candle)
and from +0.29 to +0.28R (swing5). No verdict changes, since those rest on daily P&L.

## Exits that keep open profit, with a random-entry control (backlog 56, #560–565)

Declared 2026-10-16 before the run, after the user read the MFE column: on the candle stop,
trades that reached +1R to +2R closed at −0.48R on average, and 18 NAS100.fs trades reached
+300 to +1,000 pts and closed at −633 pts net. Candle stop and EMA20 exit unchanged; exits:
**be1** pullback trail + breakeven (entry + 2 spreads) once +1R is touched; **atr3** chandelier,
highest 4h high since entry − 3 × ATR(14), instead of the pullback trail; **be1atr3** both.
Control: each exit on 200 random-entry draws (any 4h close while flat, rate calibrated to the
signal's trade count, same stop and exits). Family k = 18, **bar t ≥ 2.40**; a pass also needs
the signal's mean net per trade above the random 95th percentile. Script:
`scripts/edges/2026-10-16-56-4h-desk-exits.py` (simulator `_desk4h.py`, which reproduces row 53
trade for trade).

| NAS100.fs | pullback (row 53) | be1 | **atr3** | be1atr3 |
|---|---|---|---|---|
| Trades | 701 | 880 | 560 | 708 |
| Net % per trade | +0.088 | +0.060 | **+0.124** | +0.085 |
| Random p50 / p95 | +0.020 / +0.064 | +0.006 / +0.049 | +0.026 / +0.090 | +0.014 / +0.060 |
| Signal's random percentile | 100 | 100 | 99 | 100 |
| Daily t | 2.07 | 1.87 | **2.25** | 2.05 |
| IS / OOS per day | +0.072 / +0.048% | +0.055 / +0.052% | +0.085 / +0.048% | +0.067 / +0.052% |
| Years positive | 7/8 | 7/8 | 7/8 | 7/8 |
| Sharpe vs B&H | 0.78 vs 0.82 | 0.71 | **0.85** | 0.77 |
| Total vs B&H (+141.8%) | +61.5% | +52.9% | +68.9% | +60.1% |
| $ at 0.01 lot, 2019-09 → 2026-10 | +1,803 | +1,610 | **+1,954** | +1,806 |
| Verdict | fail | fail | **lead** (t < 2.40) | fail |

| Signal's random percentile (net %/trade) | pullback | be1 | atr3 | be1atr3 |
|---|---|---|---|---|
| S&P.fs | 64 | 64 | 68 | 83 |
| DJ30.fs | 32 | 26 | 26 | 27 |
| XAUUSD | 76 | 60 | 80 | 60 |

What it shows:

- **On NAS100.fs the entry has information.** With every exit the signal's mean net per trade
  is above all or nearly all of 200 random-entry runs with the same machinery. That is the
  first evidence in this family that the EMA/MACD entry is more than the index drift.
- **On S&P.fs, DJ30.fs and XAUUSD it does not.** Their signals sit between the 26th and 83rd
  percentile of random entries. Cell A (all three indices) fails because of them.
- **ATR3 is the best exit on NAS100.fs** (+0.124% per trade, Sharpe 0.85 against holding's
  0.82). It is a lead, not a candidate: t 2.25 is under this family's 2.40 bar.
- **Breakeven makes everything worse.** Moving the stop to entry at +1R gets it hit by normal
  4h noise, the entry condition is still true, so it re-enters and pays the spread again.
- **The exits still keep only 4–13% of MFE.** The give-back the user found is real and none of
  these three exits fixes it; on NAS100.fs the entry is the asset and the exit is still open.

## Stage log

| Date | Stage | Note |
|---|---|---|
| 2026-10-16 | 0 | Spec frozen from the user's rule, backlog row 53 |
| 2026-10-16 | 1 failed | #548 (cell A) fail, #549 (cell B) fail |
| 2026-10-16 | 1 failed | Row 54, wider stops: #550–553 all fail |
| 2026-10-16 | 1 failed | Row 55, 0.5R target: #554–559 all fail; per-trade R corrected for gap fills. Family at k = 12 |
| 2026-10-16 | 1 failed, lead | Row 56, exits + random control: #560–565 fail; NAS100.fs atr3 a lead, NAS100 entry beats random at p99–100. Family at k = 18 |
| 2026-10-16 | admitted | The user admitted #562 to the executor as [nas-4h-atr3](nas-4h-atr3.md) (T180) |
