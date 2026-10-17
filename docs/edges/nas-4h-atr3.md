# nas-4h-atr3: the 4h desk strategy on NAS100.fs with an ATR3 chandelier trail

**Stage 1 lead, admitted to the executor by the user (2026-10-16, invariant 11 as amended).**
Backtest #562 in [4h-desk-trend](4h-desk-trend.md). It cleared every gate except t: 2.25
against the family's 2.40 bar (k = 18). On NAS100.fs the entry beats random entries with the
same exits at the 99th percentile. Executor class `nas_4h_atr3` (magic 260903), T180, written
2026-10-16, **not enabled**. Because stage 1 was not passed, the forward test runs on the
longer gate.

## Spec (frozen 2026-10-16)

| Field | Value |
|---|---|
| Hypothesis | On NAS100.fs 4h candles, a close above EMA20 with EMA20 above EMA50 and MACD(8,17) above 0 marks a trend that pays a wide chandelier trail. |
| Instrument | NAS100.fs (Axi, the NAS100 futures-based CFD). Traded directly. Raw quotes, roll jumps included, as in the backtest. |
| Bars | 4h bins of broker M1, anchored at 17:00 New York (17/21/01/05/09/13 NY). A bin's close is its last M1 close. EMA20, EMA50, MACD line = EMA8 − EMA17 of the 4h closes; ATR(14) Wilder. |
| Entry | Long, while flat, at a 4h close with close > EMA20, EMA20 > EMA50 and MACD line > 0. Filled at the first M1 open after the bin (Sunday's open for Friday's last bin). Not later than 10 minutes after that bar. |
| Initial stop | The signal candle's low minus one spread (ask − bid at the fill). If that is not below the bid, there is no trade (the backtest's gap rule). |
| Trail | At each later 4h close: stop = max(stop, highest 4h high since the entry candle − 3 × ATR(14)). It never moves down. Sent through the bridge's raise-only `modify_sl`. |
| Exit | The broker's stop, intrabar; or a 4h close < EMA20, sold at the next M1 open. |
| Regime condition | Always. |
| Size | 0.01 lot ($0.20 per index point). |
| Costs assumed | The recorded spread at entry, doubled (median 2.0 points). No swap on `.fs`. |
| Search count | Family `4h-ema-macd-trend`, k = 18, #548–565. Admitted as a lead, not a pass. |
| Source | The user's rule, 2026-10-16; exit and control from backlog row 56. |

## Evidence (#562, broker M1 2019-09 → 2026-10)

| | Value |
|---|---|
| Trades | 560 (about 79 a year), 24% winners |
| Net per trade | +0.124% (random entries with the same exits: p50 +0.026%, p95 +0.090%) |
| Mean R ± clustered SE | +0.155 ± 0.143 |
| Daily t | 2.25 (bar 2.40) |
| IS < 2024 / OOS per day | +0.085% / +0.048% |
| Years positive | 7/8 |
| Sharpe vs buy-and-hold | 0.85 vs 0.82 |
| Total, 0.01 lot | +$1,954 (about $23 a month) |
| Median spread at entry | 2.0 points (p90 2.5) |
| Worst trade | −3.11% |

Parity: `tests/test_broker_nas_4h_parity.py` replays the executor class bin by bin over the M1
export and reproduces #562's 560 trades, with the same entry and exit times. Exit prices agree
within 0.01, because the class rounds the stop to the broker's two decimals.

## Kill rules (from a bootstrap of #562's trades, resampled by entry day; % of the entry price)

| Rule | Limit | How it was set |
|---|---|---|
| Drawdown, first 30 trades | > 9.6% | bootstrap 95th percentile at 30 trades |
| Drawdown, first 60 trades | > 13.7% | 95th percentile at 60 |
| Drawdown, last 100 trades (after 60) | > 17.5% | 95th percentile at 100 |
| Losing streak | ≥ 28 in a row | 99th percentile over 100 trades (a 24%-win rule runs long streaks) |
| Expectancy decay | one-sided CUSUM, k = 0.062% (half the mean), alarm at h = 15 | about one false alarm per 200 trades in control |
| Cost drift | mean spread over the last 20 entries > 3.0 points | 1.5 × the backtest's median |

A kill rule pauses the strategy and only a person resumes it, on a fresh sample, with the rule
unchanged.

**Finding:** the CUSUM is slow on this rule. If the edge vanished (mean 0), it would take a
median of about 82 trades, roughly a year, to alarm. The simple "last 20 trades + 1 SE < 0" rule
was rejected: on #562's own distribution it fires within 200 trades 99% of the time. The
drawdown and streak limits are the faster stops.

## Forward gate (stage 2, the longer gate)

- ≥ 50 trades over ≥ 2 calendar months. At the backtest's rate, about 7–8 months.
- Mean net per trade > 0 with the 2-SE test, SE clustered by entry day.
- Measured spread at the fill ≤ 1.5 × assumed (≤ 3.0 points on average).
- No kill rule tripped.

**It trades whatever account the `mt5` terminal is logged into.** "Demo" means the terminal is on
the demo account; there is no demo-only lock.

**Correlated exposure:** `nas_mr` also trades NAS100.fs long. When both hold, the account has
one 0.02-lot NAS100 position for risk purposes. Each strategy's kill rules count it alone.

## Telegram

Every entry signal goes to Telegram with the bin, the reference price, the stop, the risk in
points and $ at 0.01 lot, and the indicator values. In a dry run (`EXECUTOR_ENABLED=false`) only a
fresh signal is sent, since nothing is held and the condition stays on bin after bin. Fills,
exits, stop-outs, a rejected trail and a bin decided too late are alerted too.

## Journal

`broker.order_intents` (strategy `nas_4h_atr3`) is the trade journal: one `decide` row per 4h
bin with the values that decided it, and the `open`, `modify`, `close` and `:stopped` rows with
fills, stops and P&L.

## Stage log

| Date | Stage | Note |
|---|---|---|
| 2026-10-16 | 1 lead | #562: t 2.25 vs bar 2.40; entry beats random at p99 |
| 2026-10-16 | admitted | The user admitted it to the executor (invariant 11 amended). Class `nas_4h_atr3` written with exact parity (T180). **Not enabled**: needs `nas_4h_atr3` in `EXECUTOR_STRATEGIES` on the homeserver, with `EXECUTOR_ENABLED=true` and `MT5_ALLOW_TRADING=1` |
