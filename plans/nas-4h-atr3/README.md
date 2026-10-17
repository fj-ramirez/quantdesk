# nas-4h-atr3: trade the 4h desk strategy's NAS100.fs lead through the executor

Filed 2026-10-16 on the user's decision to demo NAS100.fs with the ATR3 trail
([4h-desk-trend](../../docs/edges/4h-desk-trend.md), #562), and to **amend invariant 11** so a
stage-1 *lead* the user admits by name may enter the executor. The task is T180.

The rule is #562's, frozen: on NAS100.fs 4h candles anchored at 17:00 New York, long at a close
with close > EMA20 > EMA50 and MACD(8,17) > 0; initial stop one spread below the signal candle's
low; a chandelier trail (highest 4h high since entry − 3 × ATR(14), raised at each 4h close,
never lowered); exit at a 4h close < EMA20. 0.01 lot.

## What has to change, and why

| # | Change | Why |
|---|---|---|
| 1 | **Invariant 11** (CLAUDE.md, `context/decisions.md`, the edge-research skill, the strategies module docstring): `strategies.py` holds a candidate that passed stage 1, **or a stage-1 lead the user admitted by name**, recorded with the date in `docs/edges/README.md`. The order path gains `modify_sl`. | The user's decision, 2026-10-16. #562 is a lead (t 2.25 vs 2.40). |
| 2 | Candidate file `docs/edges/nas-4h-atr3.md`: frozen spec, kill rules from a session-block bootstrap of #562's 560 trades (drawdown, losing streak, expectancy decay, cost drift), and the forward gate: **≥ 50 trades over ≥ 2 months** (the longer gate, since stage 1 was not passed). | Every strategy needs written kill rules before its first trade. |
| 3 | Bridge `op_modify_sl(ticket, sl)`: TRADE_ACTION_SLTP on one position. **Raise-only**: refuses a stop that is lower than the current one for a buy (higher for a sell), or not below the bid (above the ask). Added to `ORDER_PATH` in `test_broker_bridge.py`. | The trail moves the stop; the bridge today can only open and close. Raise-only means a bug can tighten a stop, never remove protection. |
| 4 | Bridge `op_deals(position)`: read-only `history_deals_get` for one position ticket. | When the broker's stop closes the position, the executor needs the real exit price for P&L and kill rules. |
| 5 | Client: `modify_sl`, `deals`. | |
| 6 | Strategy class `Nas4hAtr3` (`nas_4h_atr3`, magic 260903): a `decide` leg at the end of every 4h bin (none on Saturday); `signal(...)` returns open (with the signal candle's low), trail (a new stop), close, or nothing. 4h bars built exactly as `scripts/edges/_desk4h.py`. | |
| 7 | Executor: decide a 4h bin when the first M1 bar after it is in `broker.bars` (that open is the backtest's fill), and expire if more than 10 minutes late; the initial stop = candle low − (ask − bid), and no trade if that is not below the bid (the backtest's gap rule); a **`modify` leg written to `broker.order_intents` before it is sent**; a position that is gone is journalled as a stop-out with the exit price from `deals`. | Every leg is journalled before it is sent (invariant 11). |
| 8 | **Parity test**: replay the class decision by decision over the M1 export and require the same trades as `_desk4h.simulate(..., "atr3")` on NAS100.fs. | The class must match the frozen spec character for character. |
| 9 | Settings: `nas_4h_atr3` is available in `EXECUTOR_STRATEGIES` but **not enabled by default**. It trades only with `EXECUTOR_ENABLED=true`, `MT5_ALLOW_TRADING=1` and its name in the list. | Turning it on, and deploying to the homeserver, are the user's steps. |

Unchanged: the hard volume cap, one position per strategy, the stop on every order, kill rules
that pause until a person resumes.

## Findings to carry

- **It trades whatever account MT5 is logged into.** "Demo" is the account, not a code lock.
- **Correlated exposure:** `nas_mr` also trades NAS100.fs long. When both are in, it is one
  NAS100 position of 0.02 lot for risk purposes; the kill rules count each strategy alone.
- **Signal timing:** the decision waits for `broker-ingest` to land the next M1 bar, so fills
  come a minute or two after the backtest's. The demo's measured cost includes that.

## Result

Code written 2026-10-16 on `feat/t180-nas-4h-atr3`, all nine steps plus a Telegram entry alert
(the user's request during the build). Parity with #562 is exact on entry and exit times (560
trades). Not yet deployed or enabled; see T180 for the homeserver steps.

Decided during the build:
- **Kill-rule decay test is a CUSUM, not "last 20 + 1 SE < 0".** The simple rule fires within
  200 trades on 99% of bootstrap paths for a 24%-win rule; the CUSUM (k = half the mean, h = 15)
  false-alarms about once per 200 trades and detects a dead edge in a median ~82 trades.
- **A bin is decided at the first M1 bar after it**, within 10 minutes, so the fill is the
  backtest's (Sunday's open for Friday's last bin). The decide leg waits up to 72 hours for it.
- **A pending `modify` row found after a crash is expired, never sent**: the next decision
  recomputes the trail from the bars.
- **The dry-run entry alert fires only on a fresh signal** (the previous bin off), since nothing
  is held in a dry run.
