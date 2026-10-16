# nas-mean-reversion: buy short-term weakness in a NAS100 uptrend

**Stage:** 1 passed on **independent history** (2026-10-09). **Next gate:** stage 2, demo,
≥ 30 trades. **Source:** the mean-reversion family leads (RSI(2) #334, IBS #378, Double 7s #419,
the day-2 reversal observed in #408), combined and confirmed out-of-sample in #428.

## Spec (frozen)

| Field | Value |
|---|---|
| Hypothesis | In an uptrend, a short-term oversold close is followed by a rebound within days. |
| Mechanism | Liquidity provision: short-term sellers (stops, de-risking, rebalancing) push price below value, and dip buyers and dealers' positive-gamma hedging pull it back. The effect is documented for US index ETFs since the 1990s (Connors and Alvarez 2009; Pagonidis 2013). |
| Instrument | **NAS100.fs**. S&P.fs is a weaker lead (2010–19 t = 1.05) and is not part of this candidate. |
| Regime condition | The 16:00 New York close > its 200-session SMA. Flat otherwise ("off: regime"). |
| Entry | At the 16:00 close, when the regime is on **and** any of: RSI(2) (Wilder) < 10; IBS = (close − RTH low) / (RTH high − RTH low) < 0.2; the close is the lowest of 7 sessions. RTH = 09:30–16:00. |
| Exit | At the first 16:00 close above its 5-session SMA. No target. Disaster stop: see *Kill rules*. |
| Costs assumed | One recorded spread per position change (2.0 points), doubled per round trip. Swap 0 (futures-based CFD). Roll adjustment included (held through rolls). |
| Search count | It took 428 tests to get here. The composite was **declared before** the independent test and run once (2 tests: ^GSPC, ^NDX). |

## Evidence

| Span | Entries | Mean per day in the market | t | Years + | Sharpe vs buy-and-hold |
|---|---|---|---|---|---|
| **^NDX 2000–19 (independent)** | 409 | +0.076% | **2.72** | 12/20 | 0.62 vs 0.27 |
|  2000–09 | 166 | +0.103% | 2.32 | 5/10 | 0.73 vs −0.03 |
|  2010–19 | 242 | +0.056% | 1.56 | 7/10 | 0.51 vs 0.94 |
| NAS100.fs 2019–26 (broker, the span the leads came from) | 177 | +0.127% | 2.82 | 6/8 | 1.06 vs 0.79 |

**Read honestly:**
- It is in the market about 25% of days, so in strong bull markets (2010–19) it earns less than
  holding.
- Its value is a better return per unit of time at risk, and sitting out bear phases through
  the SMA200 regime: +70% vs −9% for holding in 2000–09.
- The 2022 bear cost −11% (broker span), with the regime on during bear-market rallies.

## Sizing

**0.01 lot** NAS100.fs ($0.20 per point, notional ≈ $6,200 at 31,000) through stages 2 and 3.
Expectancy at that size, for reference: half the 2000–19 rate is 0.038% per in-market day ×
~80 days a year ≈ 3.0% a year of notional ≈ $16 a month (the broker span, unhaircut, ≈ $60).

## Kill rules (fixed until 30 trades, then replaced with bootstrap values)

1. **Disaster stop:** −8% below entry (a stop on every order is executor policy).
2. **Drawdown:** pause at −15% of notional cumulative from the peak (2022 cost −11%).
3. **Losing streak:** pause after 6 consecutive losing trades.
4. **Expectancy decay:** after 30 trades, pause if the mean over the last 30 + 1 SE < 0.
5. **Regime:** a close below SMA200 means flat. That is the rule working, not a pause.

## Stage log

| Date | Event |
|---|---|
| 2026-10-09 | Composite declared (backlog 44) and run once on 2000–19: ^NDX t = 2.72, both halves positive. **Stage 1 passed** (> 2 SE on data it was not found on). S&P version is a lead only. Next: demo, 0.01 lot. |
| 2026-10-15 | **Executor class written** (T152): `NasMeanReversion` (`nas_mr`, magic 260902) in `app/modules/broker/strategies.py`. One `decide` leg per weekday at 16:00 New York. It reads `broker.bars` M1, and once the 16:00 bar is in (window 10 min) it journals the decision with the indicator values and sends the open or close. User's choices: **indicators on raw NAS100.fs quotes**, roll jumps included, as in the 2019–26 broker backtest; **kill rules as frozen** (the four above, no cost-drift rule). Round-trip `pnl_pct` removes roll steps confirmed in `broker.rolls`. **Parity:** replayed day by day on 2019-07 → 2026-10 broker M1 with the executor's 330-day window, it matches the backtest's `signal()` on all 1,794 sessions (480 days in the market, 177 entries, 0 mismatches). Not yet enabled on the homeserver. |
