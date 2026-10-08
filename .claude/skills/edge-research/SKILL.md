---
name: edge-research
description: >
  Takes a desk finding through validation, demo and micro-live forward tests, sizing and
  kill rules on leveraged CFDs, or kills it. Use when the user asks whether something
  is an edge, to backtest or validate a candidate, to find history for one, to plan a forward
  test, to size a position, or whether a live strategy has stopped working. Not for "what to
  trade today": that is market-research.
---

# Edge research mode

## The goal, and the boundary

**The desk must pay for itself.** It costs about **$100/month** (as of 2026-10-08: ThetaData
Options Standard at $80 plus hosting and the rest). Profit comes from trading CFDs (index and
gold) with leverage. Every recommendation here is judged by whether it moves the desk toward
covering that cost after trading costs, without risking the account to get there.

**Regime-dependent strategies are fine. Unmonitored ones are not.** A strategy that only works
in short gamma, or only when rates are rising, is acceptable if it states that condition and
we can see when the condition is gone or the edge has decayed **before the account is badly
damaged**. Every strategy needs a written kill rule before its first live trade.

**The desk never routes orders.** That is a repo invariant. This skill designs, tests, sizes
and monitors. The user places every trade by hand, on demo or live. Do not write broker
integration, MT5/EA code or anything else that sends an order, even when asked to make
forward-testing easier. Say it is out of scope and suggest a journal instead.

## The problem this file exists for

The desk has **less than a month of continuous capture**. Call `desk_status` for the actual
first session. Do not quote a date from memory. Anything found in that window was found:

- in **one regime** (a few weeks of one rates/vol backdrop),
- on data that was **already searched** (every variant tried counts against the result), and
- with **correlated events** (SPX, SPY and DIA cross the same level together, so it is one event,
  not three).

So a finding from the desk's own history is a **candidate, never an edge**, however good the
numbers look. `docs/read-review-2026-10-08.md` §4 is the reference case. A plausible rule was
proposed, backtested over 12 sessions, lost money, and left behind a leaning that clears no
significance bar.

There are two ways to test a candidate properly, and the second one is always needed:

1. **Historical test on data the candidate was not found on**, from a free or already-paid
   source. Fast, and it covers several regimes. Try this first whenever the signal can be
   rebuilt from data that goes back years.
2. **Forward test**: demo account first, then **0.01 lot** on the real account. Slow, but it
   is the only way to measure *this broker's* spread, slippage and swap, and it is the only
   option when the signal depends on data only the desk has (intraday GEX).

## The pipeline: stages and gates

A candidate moves forward only by **passing its gate**, never by argument. The thresholds below
are defaults. The user can change them for a given candidate, but only **before** the stage
starts, and the change goes in the candidate file.

### Stage 0: Candidate spec, frozen

Write `docs/edges/<slug>.md` before testing anything. It must contain:

| Field | What goes in it |
|---|---|
| Hypothesis | One sentence, plus the mechanism: why dealers, flows or macro would cause this |
| Instruments | Desk symbol (e.g. SPY) **and** the CFD it is traded through (e.g. US500), and why the mapping is valid |
| Entry | Exact, mechanical, no judgment. Timeframe, the data used, and the time it is known (no lookahead) |
| Exit | Fixed horizon, or stop and target, defined in advance |
| Regime condition | What must be true for the strategy to be *on*, e.g. "net GEX < 0 and spot below flip". Can be "always" |
| Costs assumed | Spread, commission and swap per trade, in R or bps |
| Search count | How many variants, horizons and filters were tried to find it. Report this with every result |
| Kill rules | See *Detecting that it stopped working*. Numeric, written now |
| Source finding | Link to the review or query where it came from |

**Once frozen, the rule does not change.** A tweak makes a new candidate with its own slug and
start date. Re-fitting a rule after seeing forward data destroys the only clean evidence we
have.

### Stage 1: Historical test (when the data exists)

Run it on history that **does not overlap** the window the candidate was found in.

Gate (all of them):

- **≥ 100 trades and ≥ 30 independent sessions.** Count clustered events once.
- **Mean R > 0 after doubled costs.** This is EdgeLab's doubled-cost gate, applied to the CFD's
  real cost, not the underlying's.
- **Mean > 2 standard errors, clustered by session.** A per-event SE overstates certainty.
- **Walk-forward:** split the history into consecutive windows. The edge has the same sign in
  most of them, with no re-tuning between windows.
- **Regimes:** it works in ≥ 2 distinct regimes, or the regime condition correctly isolates the
  periods where it works and it stays flat outside them.
- If it went through EdgeLab, it is **above its noise ceiling**, and the OOS-reuse caveat
  applies.

Fail → write the result in the candidate file and stop. A clean "no" is a useful output.
No history obtainable → go to stage 2 with the longer gate below.

### Stage 2: Demo forward test

Use the frozen rule on the broker's demo account, on the same CFD that will be traded live.
It has two jobs: produce out-of-sample trades, and **measure real costs**. Record the spread at
signal time, not the advertised minimum.

Gate:

- **≥ 30 trades** if stage 1 passed. **≥ 50 trades over ≥ 2 calendar months** if stage 1 was
  impossible, because then this is the only evidence.
- Mean R within the stage-1 confidence interval (or > 0 with the same 2-SE test if there was no
  stage 1).
- Measured cost per trade ≤ 1.5× the cost assumed in stage 0. If it is higher, re-run the
  stage-1 numbers with the real cost before going further.
- No kill rule tripped.

### Stage 3: Micro live, 0.01 lot

Real money at minimum size. Demo fills are often better than real ones, and this stage
measures that gap. It also tests the user's ability to execute the rule on time.

Gate to scale: **≥ 30 live trades**, live cost per trade within 1.25× of demo, live mean R not
significantly below demo, and no kill rule tripped.

### Stage 4: Sized

Size by the formula below. The kill rules stay on permanently.

## Where to get history for free

Prefer data the desk already pays for or already ingests. **A proxy is a proxy.** Write down
how it differs from the traded CFD.

| Need | Source | Cost | Caveats |
|---|---|---|---|
| EOD option chains, OI and greeks, so daily GEX levels going back years | ThetaData, already paid. T26 history loader → T25 / T127 | $0 marginal | **EOD only**, so it supports daily-horizon strategies. Check T26 status in `TASKS.md` and what is actually loaded (`query_sql` on `gex.snapshots` where `source='thetadata'`) before relying on it. Verify what the plan includes before assuming intraday option history exists |
| Intraday GEX (flip, walls within the session) | **None.** Only the desk's own capture | — | Signals that need it skip stage 1 and go to stage 2 with the longer gate. A forward log like T142 *is* the history |
| The CFD's own price | Broker's MT5/platform history export | Free | The exact traded instrument, including its overnight session, but depth is limited and the data is broker-specific |
| Index CFD / XAUUSD intraday, years deep | Dukascopy historical data (tick / 1-min) | Free | Dukascopy's CFD feed, not your broker's. Spreads and session hours differ |
| Daily OHLCV for indices and ETFs | Yahoo via `yfinance` (EdgeLab already uses it), Stooq | Free | Unofficial and occasionally revised. Use adjusted vs unadjusted deliberately |
| Macro, rates, credit | FRED (already used, needs `XA_FRED_API_KEY`); ALFRED for vintages | Free | Use vintages for anything point-in-time. Latest-revised data leaks the future |
| VIX family history | Cboe (already ingested by the terminal) | Free | See the VIX/SKEW lag note in market-research |
| Economic calendar history | The terminal only since T139 | — | No deep free history with consensus. Treat calendar-conditioned rules as stage-2 only |

Before writing a new loader, check whether the terminal or EdgeLab already has the series.
Fetched data goes under `DATA_DIR`, not the repo. Any new ingestion is a task in `TASKS.md`,
not something this skill builds inline.

## CFD economics: model these or the backtest lies

- **Contract spec.** Value per point of 0.01 lot, minimum stop distance, trading hours. Read it
  from the broker's spec sheet. **Never assume it.** It differs between brokers and between
  US500/NAS100/US30/XAUUSD.
- **Spread** at the time the signal fires. It is wider at the open, around news and overnight.
- **Commission**, if the account type charges one.
- **Overnight financing (swap)**, per night held, with a triple charge on one weekday. It
  dominates any multi-day hold on a leveraged position.
- **Basis.** An index CFD usually tracks the future or a fair-value-adjusted cash price, not the
  cash index the desk measures. Levels from SPX/SPY must be **translated** to the CFD's price
  (offset at signal time), not copied.
- **Gaps.** Weekend and overnight gaps jump past stops. Size for the gap, not the stop.
- **Mapping.** SPX/SPY → US500, QQQ → NAS100 (an NDX proxy, not QQQ itself), DIA → US30,
  GLD → XAUUSD (GLD has US hours and an expense ratio; spot gold trades nearly 24h).

## Sizing: the $100/month arithmetic, shown every time

Leverage changes **position size**, not **edge**. A strategy with no edge loses faster with more
leverage. Before recommending a size, show this calculation with the candidate's own numbers:

```
monthly R          = trades_per_month × mean_R_live_estimate
risk_per_trade_$   = monthly_target_$ / monthly R          (target ≥ 100 to cover costs)
account_needed_$   = risk_per_trade_$ / risk_fraction       (risk_fraction ≤ 0.01 by default)
```

- Use a **haircut** mean R: half the stage-1 estimate, or the stage-2/3 live mean if that is
  lower. Backtested edges shrink in live trading.
- Worked example (illustrative only): 20 trades/month × 0.15R = 3R/month → $33 risk per trade →
  $3,300 account at 1% risk.
- If covering $100/month needs **risk_fraction > 2%**, say plainly that the strategy can't carry
  the desk's costs at this account size. Don't solve it with more leverage. Options are a bigger
  account, more independent strategies, or accepting that it doesn't cover costs yet.
- **Risk of ruin:** bootstrap the trade distribution (stage-1 or forward trades, resampled in
  session blocks) over 12 months at the proposed size. Report the chance of a 20% and a 50%
  drawdown. Don't scale while the 50% probability is above 1%.
- Several strategies on the same index aren't diversified when they fire on the same events.
  Count total risk on correlated positions as one position.

## Detecting that it stopped working

This part makes regime-dependent strategies acceptable. Write all of it in stage 0, with numbers,
and check it after **every** trade from stage 2 on.

1. **Regime switch, not a loss signal.** Record the regime condition's value at each signal.
   When the condition is off, the strategy is **flat**. That is expected behaviour and doesn't
   count against it. Report "off: regime" separately from "paused: decay".
2. **Drawdown limit.** Strategy drawdown greater than the bootstrap's 95th-percentile drawdown for
   the same number of trades → **pause**.
3. **Expectancy decay.** One-sided CUSUM on trade R, with reference value k = half the expected
   mean R and threshold h set from the bootstrap so false alarms are rare (≈ 1 per 200 trades).
   Alarm → pause. A simpler fallback is to pause if the mean R over the last 20 trades + 1 SE < 0.
4. **Losing streak.** A streak longer than the bootstrap's 99th-percentile streak → pause.
5. **Cost drift.** Rolling live cost per trade > 1.5× assumed → pause and re-run the economics.
   The edge may be intact while the broker eats it.

**Paused means back to demo, not deleted.** A paused strategy can come back only on a **fresh**
forward sample that passes its stage-2 gate again, with the rule unchanged. It is never
re-fitted to bring it back. Three pauses → kill and write up why.

## Record keeping

Each candidate has `docs/edges/<slug>.md` (spec, stage log with dates, gate results, pauses)
and a trade journal `docs/edges/<slug>-trades.csv`. One row per signal, **including signals not
taken**:

`signal_ts_utc, key, desk_symbol, cfd_symbol, direction, regime_on, <desk inputs: spot, flip,
net_gex…>, intended_entry, fill_price, spread_at_fill, exit_ts_utc, exit_price, swap, r,
account (demo|live), taken (y/n), note`. `docs/edges/flip-cross-trades.csv` is the template.

Timestamps are UTC (invariant 4). A missing value is empty, never 0 (null ≠ zero). Signals not
taken matter: if the user skips the losers by intuition, the journal measures the user, not the
rule. Keep a list of active candidates and their stages in `docs/edges/README.md`.

## Honesty rules (inherited, and the reason this skill exists)

- **Noise ceiling, doubled costs, walk-forward, roll-gap caveat** (EdgeLab, invariant 9).
- **Report the search count** with every result. Ten variants tried and one passed at 2 SE is
  roughly what chance produces.
- **Cluster by session.** Correlated instruments firing together are one event.
- **Point-in-time.** Use only what was known at signal time. Pass `as_of` to terminal tools for
  past moments.
- **A null is never a zero.**
- **No numbers baked into this file.** Results live in the candidate files and the database.
  Numbers copied into prompts rot without anyone noticing (the T106 lesson).
- **"Not significant yet" is not "works".** Small samples are leanings. Quote `n` every time.
- When forward data contradicts an earlier conclusion, **say so first**, in the candidate file
  and in the answer.

## Output

Lead with **the candidate's stage, the next gate, and what is missing to pass it** (trades,
months, data). Then the evidence: n, mean R ± clustered SE, search count, costs used. If a
size is recommended, show the arithmetic. End with the kill rules in force. Keep a
recommendation to scale separate from the evidence, and never base it on a single good week.
