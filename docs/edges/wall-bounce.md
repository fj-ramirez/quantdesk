# wall-bounce: a touch of a gamma wall that closes back and moves away

**Stage:** 0, spec frozen 2026-10-12. Like flip-cross it needs the walls **within the session**,
so no history exists for a stage 1 (see flip-cross's *Why there is no stage 1*). It goes to
stage 2 (demo) with the longer gate.
**Next gate:** stage 2: ≥ 50 trades over ≥ 2 calendar months. The exit is frozen (2026-10-14).
What is missing for demo is an executor class (T163).
**Source:** the user's request of 2026-10-12 for instant alerts of wall bounces. The rule was
chosen from three proposed definitions **without looking at any data**. Logging and alerts are
**T153**.

## Spec (frozen)

| Field | Value |
|---|---|
| Hypothesis | When price touches the put wall from above and closes back above it, then keeps rising for two closes, it continues up. The call wall is the mirror image. |
| Mechanism | At a large positive-gamma strike, dealers' hedging buys dips into it and sells rallies into it, so the wall acts as support or resistance. A close back inside plus two closes away shows that the hedging held. *Reasoning, not tested.* Note: the desk names walls by gamma sign (T99), so a put wall is a negative-gamma strike. The hedging story is weakest there. |
| Instruments | SPY/SPX → **US500** (S&P.fs), QQQ → **NAS100** (an NDX proxy), DIA → **US30**, GLD → **XAUUSD**. The signal is read on the desk symbol and **traded on the CFD**. The level is translated with the pair's basis method (`broker.basis`) at signal time. |
| Data and timing | The wall is `gex.gex_levels.put_wall` / `call_wall`, filter `ALL`, from the **latest in-session capture with that wall and `captured_at` ≤ the touching bar's close**. Price is the 5-minute bars in `gex.intraday_bars` (high/low for the touch, closes for the rest). |
| Entry | **Put wall W:** the previous close is above W. The touching bar's low is ≤ W + 0.05 % × W (through W counts). It closes above W. Then the next two closes are each higher than the one before. Enter at the second of those closes, **long** (`PUT_WALL_BOUNCE`). **Call wall:** mirror image, **short** (`CALL_WALL_BOUNCE`). After an event, the next touch of the same key must come after the confirming bar. |
| Exit | **Frozen 2026-10-14** (see *Exit*). Stop beyond the wall by half an ATR and never closer than one ATR to entry, target at 1R, flat at 15:55 ET. ATR = ATR(14) of the CFD's 5-minute bars at the entry close. |
| Clustering | Same as flip-cross: one alert per index family and key within 60 minutes. DIA is alerted only when SPX/SPY did not make the same move. |
| Session filter | 09:45–15:00 ET entries. |
| Regime condition | Always on. Net GEX at the touch is recorded, not used as a filter. |
| Costs assumed | **Unknown.** Measure on demo, as for flip-cross. |
| Search count | **1 rule**, chosen from 3 proposed definitions by the user, with **no data looked at**. The 0.05 % tolerance and "two closes" are judgment, not fitted. |

## Exit (frozen 2026-10-14)

Set by the user: an ATR-based stop, so the stop sits outside the CFD's normal noise, and a 1:1
target. The rule:

- **ATR** = ATR(14) of the traded CFD's 5-minute bars, built from its M1 bars, at the entry
  bar's close. W is the wall translated to the CFD (`broker.basis`, as for entry).
- **Long (`PUT_WALL_BOUNCE`):** stop distance D = max(entry − W + 0.5 × ATR, 1.0 × ATR). Stop
  = entry − D, target = entry + D.
- **Short (`CALL_WALL_BOUNCE`):** mirror image. D = max(W − entry + 0.5 × ATR, 1.0 × ATR).
- **Time exit:** flat at 15:55 ET if neither is hit. Nothing is held overnight.
- On an M1 bar that touches both, the stop counts first. **R = D.**
- The same exit applies unchanged to the `_0DTE` keys and to IWM → US2000.

Why it is built this way: the hypothesis is that the wall holds. A stop just beyond the wall
(half an ATR) exits when that is proven wrong. The one-ATR floor keeps the stop out of the noise
when the entry sits close to the wall.

Search count: the exit's form (ATR, 1:1) is the user's. 0.5 and 1.0 ATR, ATR(14) and 15:55 are
judgment. Nothing was fitted. The only data seen before freezing was the 3-session aggregate from
`gex_level_events` on 2026-10-14 (median adverse and favourable excursion in swing units), not
any event's path at these distances.

## Evidence so far

None. The forward record starts the day T153 is deployed. Read it with the MCP tool
`gex_level_events(key="PUT_WALL_BOUNCE")` and `key="CALL_WALL_BOUNCE"`.

## 0DTE variant (separate keys, frozen 2026-10-12, T154)

The same rule, unchanged, on the **0DTE** levels: `gex_levels` filter `ZERO_DTE` instead of `ALL`.
It is logged under its own keys (`PUT_WALL_BOUNCE_0DTE`, `CALL_WALL_BOUNCE_0DTE`), so its record never mixes with this one. It is a
**new candidate at stage 0** with no evidence, added at the user's request because the
all-expiries levels often sit far from price (on 2026-10-12, SPX's ALL put/call walls were
7500/8000 with spot at 7804; the 0DTE ones were 7810/7820). 0DTE levels sit close to price and move
within the session, so expect many more events. Read it with
`gex_level_events(key="PUT_WALL_BOUNCE_0DTE")`.

**IWM → US2000 added 2026-10-12 (T154).** US2000 is Axi's *cash* Russell 2000 CFD: no rolls, but a
**0.1-lot minimum** at 1 USD per point, ten times the `.fs` indices' minimum size. IWM events are
recorded per symbol, so they can be read apart from the original five.

## Kill rules (from the first demo trade)

The same fixed rules as flip-cross until 30 demo trades exist. At trade 30, replace them with
bootstrap values and record the change here with its date.

1. **Expectancy decay:** pause if mean R over the last 20 trades + 1 SE < 0.
2. **Losing streak:** pause after 8 consecutive losses (each key counted separately).
3. **Drawdown:** pause at −10R cumulative from the strategy's peak.
4. **Cost drift:** pause if the rolling 20-trade average cost > 1.5× the cost measured in the first 20.
5. **Per-key:** `PUT_WALL_BOUNCE` and `CALL_WALL_BOUNCE` are paused independently.

A pause means back to demo with a fresh sample, with the rule unchanged. Three pauses kill it.

## Stage log

| Date | Event |
|---|---|
| 2026-10-12 | Spec frozen from the user's choice. No data examined. Logged forward and alerted by T153 (`gex.level_events`). Exit still to be fixed before any demo trade. |
| 2026-10-14 | **Exit frozen** at the user's request: stop max(beyond the wall by 0.5 ATR, 1 ATR from entry), target 1R, flat 15:55 ET, ATR(14) on the CFD's 5-minute bars. Kill rules now have an R. Demo needs an executor class (T163). |
