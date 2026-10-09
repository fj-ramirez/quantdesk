# gold-asia-drift: long XAUUSD through the Asia session

**Stage:** 1 passed on the broker's own history (2026-10-09). **Next gate:** stage 2, a demo
forward test of ≥ 30 trades. Its main job is **measuring the real spread at 18:00 New York**.
**Source:** the documented-effects test of 2026-10-09 (`scripts/edges/2026-10-09-documented-effects.py`,
hypothesis H5a), run after the T147 price-action study failed (see
[plans/charter-mt5 *Result*](../../plans/charter-mt5/README.md)).

## Spec (frozen)

| Field | Value |
|---|---|
| Hypothesis | Gold's return accrues during the Asia session. Holding long from 18:00 to 03:00 New York earns the drift, and London and New York add nothing on average. |
| Mechanism | Asian physical and central-bank demand buys in local hours, and Western sessions carry the macro selling (rates, dollar). This is documented in market commentary since the 2010s (*reasoning, not tested here*). The sessions table below is the evidence. |
| Instruments | **XAUUSD** (Axi spot, no rolls). There is no desk symbol: the rule is price-only. |
| Entry | **Buy at 18:00 New York**, the first XAUUSD M1 bar at or after it, every session that has a next one. No judgment and no skipped days. |
| Exit | **Sell at 03:00 New York** the next morning. There is no stop and no target. |
| Regime condition | Always on. |
| Costs assumed | One spread per round trip, **doubled** in the test. The broker's recorded spread at the 18:00 entry has a median of $0.21, a mean of $0.32 and a 90th percentile of $0.58. Swap is **none**, because the hold starts after the 17:00 rollover and ends before the next one. **Verify both on demo:** entry is right after the daily break, when spreads are widest. |
| Search count | **17 hypotheses** in the documented-effects test, fixed before running: overnight drift, intraday drift, turn of month and a 200-day trend filter on three index CFDs, plus four gold rules. **Plus the 288 T147 trials** run earlier the same day on the same data. Only this one passed. |
| Not searched | The 18:00 and 03:00 times come from the published effect and were not tuned. A shifted window (e.g. 19:00 to avoid the spread spike) is a **new candidate**. |

## Stage-1 evidence (broker.bars M1, 2019-07 → 2026-10-08)

Returns by session, before costs, per day:

| Session (New York) | Mean per day | t | Days up |
|---|---|---|---|
| **Asia 18:00–03:00** | **+0.082%** | **+5.89** | 57% |
| London 03:00–08:20 | +0.009% | +0.82 | 54% |
| New York 08:20–16:00 | −0.009% | −0.45 | 51% |

It is not explained by the bull market. In 2021 gold lost 2.6% for the year while the Asia session
gained 13.9%. In 2022 Asia gained +10.2% and the year +6.8%.

The rule after **doubled** recorded spread:

| | n | Mean per trade | SD | t |
|---|---|---|---|---|
| All | 1,434 | +0.0525% | 0.532% | **+3.73** |
| IS (< 2024) | 884 | +0.0243% | | +1.82 |
| OOS (≥ 2024) | 550 | +0.0977% | 0.695% | +3.30 |

Simple % per year after doubled cost: 2019 −1.4, 2020 +0.9, 2021 +7.5, 2022 +2.8,
2023 +11.7, 2024 +14.0, 2025 +16.4, 2026 YTD +23.3. **7 of 8 years positive.**

The stage-1 gate: ≥ 100 trades ✔, mean > 0 after doubled costs ✔, > 2 SE ✔ (one trade per
session, so clustering changes nothing), walk-forward 7/8 years ✔, and ≥ 2 regimes ✔ (the 2022
hiking cycle, the 2021 down year, the 2024–26 bull). Not run through EdgeLab, so it has no
registry noise ceiling. The 17 + 288 search count above is the honest denominator.

**What could still make it fail:**

- The recorded spread is a lower bound (see the T147 note), and the entry sits on the widest
  part of the day.
- The effect grew in % terms after 2023. Part of the OOS strength may be the gold bull rather
  than the session.
- There is no stop. The worst single night was −$207 per 0.01 lot.

## Sizing (0.01 lot = 1 oz, $1 per $1 move)

The 2024+ average after doubled cost was $59 per month per 0.01 lot. The haircut (half) is
**about $30 per month per 0.01 lot**.

```
monthly target      = $100
lots needed         = 100 / 30 ≈ 0.03 lot
bootstrap DD, 250 trades (≈ 1 year), haircut mean, p95: −14.2% of price ≈ −$595 per 0.01 lot
at 0.03 lot         ≈ −$1,790 p95 one-year drawdown
```

At about 1% risk per trade, covering $100 per month needs an account of roughly **$10–15k**. On
a smaller account this strategy cannot carry the desk's cost alone, and more leverage does not
change that. Over one year at haircut expectancy, the chance of a 20%-of-price drawdown is
0.5%.

**Demo sizing:** 0.01 lot until stage 3 passes.

## Kill rules (from the first demo trade)

These come from a 20-trade block bootstrap of OOS trades at **half** the OOS mean. Units are % of
the entry price per 1 oz, so they do not drift with the gold price.

1. **Drawdown:** pause if the strategy's drawdown exceeds **6.4%** within its first 30 trades
   or **8.1%** within 60 (the bootstrap 95th percentile). After that, use 14.2% over any rolling
   250.
2. **Losing streak:** pause after **9** consecutive losing nights (the 99th percentile is 8 at
   n = 60).
3. **Expectancy decay:** after 60 trades, pause if the mean over the last 60 + 1 SE < 0.
4. **Cost drift:** pause if the rolling 20-trade mean spread at fill is > **$0.48** (1.5× the
   $0.32 recorded mean). In that case, re-run the stage-1 numbers at the measured spread before
   resuming.

A pause means back to demo with a fresh sample and the rule unchanged. Three pauses kill it.

**Power warning.** At haircut expectancy, a 30-trade sample still ends negative 38% of the time,
and a 60-trade one 33% of the time. So the demo **cannot confirm the edge in 6 weeks**. It can
confirm the costs and catch a broken rule. A losing first month is not by itself evidence
against the edge. Only a kill rule is.

## Stage log

| Date | Event |
|---|---|
| 2026-10-09 | Spec frozen. Stage 1 passed on broker M1 history: t = 3.73 after doubled recorded spread, 7/8 years positive. Search count 17 (+288). Next: demo, 0.01 lot, journal every night including skipped ones. |
| 2026-10-09 | **Automated (T151).** The executor trades this spec as `GoldAsiaDrift`, adding a −6% disaster stop that never triggers on the 1,434-night history (worst −5.07%). The journal is now `broker.order_intents`, which replaces the CSV for executed nights. It records every leg, including skipped and expired ones, with the quote at each fill. The kill rules above are enforced in code and pause the strategy automatically. Still stage 2: the demo account, until 30 trades pass the gate. |
| 2026-10-09 | Overnight checks. **Holds in gold downtrends too** (#390: filtered-out downtrend nights t = 2.4, 5/5 years), so it is not a bull-market artifact. **Absent on Sunday nights** (#407: weekly open → Monday 03:00, gross +0.02% vs +0.08%). The rule never traded Sundays, so the spec is unchanged, but the mechanism is narrower: weekday Asian sessions. |
