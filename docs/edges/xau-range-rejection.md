# xau-range-rejection: short a rejection at the top of a gold range, in a downtrend

**Stage:** 0, spec frozen 2026-10-09. **Stage 1 is possible**: the rule needs only GLD daily bars
and XAUUSD M1, and both exist going back years (see *Stage 1 plan*).
**Next gate:** stage 1. Until it passes, triggers are traded on **demo only**.
**Source:** the conversation of 2026-10-08/09: GLD GEX levels + price action read, converted with
the measured `broker.basis` ratio (T145).

## Spec (frozen)

| Field | Value |
|---|---|
| Hypothesis | In a gold downtrend that has paused into a tight range, a push into the top of the range that closes back inside it fails, and price returns toward the bottom of the range within the session. |
| Mechanism | Trend sellers reload at the range top; trapped breakout buyers exit. When the top sits on positive-gamma strikes (GLD 382–385 on 10-08), dealer hedging also sells into the rally. *Reasoning, not tested.* |
| Instruments | GLD (desk) → **XAUUSD** (Axi spot, no rolls). Levels are read on GLD daily bars and translated with the newest `broker.basis` GLD ratio at or before the signal. |
| Definitions | All on GLD daily bars (`gex.daily_bars`) up to the **previous** completed session, so known before the signal. `R_hi`, `R_lo` = highest high and lowest low of the last **8** sessions. `ATR` = 14-session mean true range. Multiply each by the ratio to get XAUUSD. |
| Regime condition | Both true at the previous close: (1) GLD close < its **50-session SMA**; (2) `R_hi − R_lo ≤ 3 × ATR`. Otherwise the strategy is flat ("off: regime"). |
| Zone | `[R_hi − 0.3·ATR, R_hi]` in XAUUSD. |
| Entry | XAUUSD trades inside or above the zone, then a **15-minute bar closes below the zone's lower edge**. Short at that close. Window 07:00–19:00 UTC. No entry within 15 minutes either side of a High/Medium USD release (`terminal_calendar`). **One attempt per session.** |
| Cancel | If XAUUSD trades at or above the stop level before a trigger, no trade that session. |
| Exit | Stop `R_hi + 0.25·ATR`, target `R_lo + 0.25·ATR`, or **flat at 20:30 UTC** on the entry day, whichever comes first. Never held overnight or over a weekend. R = entry − stop distance. |
| Recorded, not filtered | Net GEX at the GLD strikes inside the zone, net GEX at the flip, and whether the zone sits above the flip. Adopting any of these as a filter later makes a new candidate. |
| Costs assumed | Spread **$0.16** (median M1 spread 10:00–16:00 ET, T144), about 0.004R at a $39 stop. No swap (intraday). No commission assumed on the Axi standard account. **Verify on demo.** |
| Search count | **1 rule**, no variants tested. But it was written **after looking at the current chart**, and the parameters (8 sessions, 0.3/0.25 ATR, 3 ATR, SMA50) were picked by judgment. Treat it as discretionary until stage 1. |

### The first instance, 2026-10-09

The basis ratio is 10.9168 (GLD close 378.6 vs XAUUSD 4,133 at 20:00 UTC 10-08). From GLD
8-session `R_hi` 385.22, `R_lo` 374.23 and ATR 5.84, with SMA50 396.9 > close 378.62 (on), and
range 10.99 ≤ 3 × 5.84 = 17.52 (on):

| | GLD | XAUUSD |
|---|---|---|
| Zone | 383.47–385.22 | 4,186–4,205 |
| Stop | 386.68 | 4,221 |
| Target | 375.69 | 4,101 |

The plan given in chat on 10-09 rounded these values (zone 4,188–4,207, stop 4,222, target
4,105) and added "flat by Friday". The rule above is the authority.

## Stage 1 plan

1. Rebuild the signal on GLD daily bars from the start of `broker.bars` XAUUSD M1 depth, and
   translate with the `1d` basis of the previous session.
2. Simulate entries and exits on XAUUSD M1 (aggregated to 15-minute bars for the trigger),
   applying the spread **as recorded** in `broker.bars.spread` on each bar, doubled for the
   cost gate.
3. Gate (skill defaults): ≥ 100 trades and ≥ 30 independent sessions; mean R > 0 after doubled
   costs; mean > 2 SE clustered by session; same sign in most walk-forward windows (yearly);
   works in ≥ 2 regimes (e.g. the 2022 hiking cycle, 2024–25 bull, 2026 bear) or is flat when off.
4. Calendar blackout: release history with timestamps exists only from T139. Before that, run
   without the blackout and report the result as such.
5. Filed as **T149** in `TASKS.md`.

## Kill rules (from the first demo trade)

Fixed rules until 30 trades exist, then they are replaced with bootstrap values (95th-pct
drawdown, 99th-pct streak, CUSUM h for about 1 false alarm per 200 trades), dated here.

1. **Expectancy decay:** pause if mean R over the last 20 trades + 1 SE < 0.
2. **Losing streak:** pause after 7 consecutive losses.
3. **Drawdown:** pause at −8R from the strategy's peak.
4. **Cost drift:** pause if the rolling 20-trade average cost > 1.5× the $0.16 assumed.
5. **Regime:** sessions with the regime condition off are reported as "off: regime", not as losses.

A pause means back to demo with a fresh sample and the rule unchanged. Three pauses kill it.

## Sizing

0.01 lot ($1 per $1 move on Axi XAUUSD, 100 oz per lot), on demo, until stage 3. No mean R
estimate exists, so the $100/month arithmetic has no input. With mean R taken as 0, no size
covers costs.

## Stage log

| Date | Event |
|---|---|
| 2026-10-09 | Spec frozen. First instance computed: zone 4,186–4,205. Price 4,176 at 03:46 UTC after an Asian-session rally from 4,143. Stage 1 filed as T149, not yet run. |
