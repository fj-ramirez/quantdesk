# flip-cross: an intraday cross of the gamma flip, held a fixed 60 minutes

**Stage:** 0, spec frozen 2026-10-08. Goes **directly to stage 2** (demo), because no history
exists for intraday GEX (see *Why there is no stage 1*).
**Next gate:** stage 2 with the longer gate: ≥ 50 trades over ≥ 2 calendar months.
**Source:** [docs/read-review-2026-10-08.md](../read-review-2026-10-08.md) §4 · desk-side logging is **T142**.

## Spec (frozen)

| Field | Value |
|---|---|
| Hypothesis | When price crosses **down** through the gamma flip during the session, it keeps going for about an hour. When it crosses **up**, it tends to fall back. |
| Mechanism | Below the flip dealers are short gamma and hedge by selling into declines, which pushes moves further. Short gamma also leans downside (vanna). Above the flip, long-gamma hedging pins price and caps rallies. *Reasoning, not tested.* |
| Instruments | SPY/SPX → **US500**, QQQ → **NAS100** (an NDX proxy, not QQQ), DIA → **US30**, GLD → **XAUUSD**. The signal is read on the desk symbol and **traded on the CFD**. SPX itself cannot be traded. |
| Data and timing | The flip is `gex.gex_levels.flip_point`, filter `ALL`, from the **latest in-session capture with `captured_at` ≤ the bar's close**. Never use the previous EOD flip. Bars before the session's first capture don't count. Price is the 5-minute closes in `gex.intraday_bars`. |
| Entry | A cross is **two consecutive 5-minute closes on the new side of the flip, with the close two bars earlier on the old side**. Enter at the second close. Down-cross → short the CFD (`FLIP_CROSS_DOWN`). Up-cross → **short the CFD as a fade** (`FLIP_CROSS_UP`). Both are logged. They are tracked as two keys and gated separately. |
| Exit | **Fixed 60 minutes** after entry (12 bars), or the last bar of the session, whichever comes first. No flip-based stop: the review showed the re-cross stop is what made the original rule lose. Hard catastrophe stop at 3× the symbol's median 60-minute absolute move, for gap/news risk only. R = that stop distance. |
| Clustering | SPX, SPY and DIA cross together. **Trade one position per index family per event**: US500 for SPX/SPY, US30 for DIA *only if it crosses without SPX/SPY*. Score by session. |
| Session filter | 09:45–15:00 ET entries (a 60-minute hold must finish before the close). No entry in the 15 minutes either side of a scheduled release rated by the thesis as relevant (check `terminal_calendar`). |
| Regime condition | Always on. The cross *is* the regime change. Net GEX sign at the cross is recorded but **not** used as a filter: the review found that filtering on it made results worse (−4.6 vs −1.9 bps), and adopting it now would be fitting. |
| Costs assumed | **Unknown.** To be measured on demo: spread at the signal time plus commission. No swap (intraday only). The candidate is unviable if round-trip cost exceeds about half the measured edge per trade. |
| Search count | **4 rule variants** (cross/stop, plus gamma sign, short-only, long-only) **× 4 horizons** (15, 30, 60 minutes and close) = 16 looks, plus the direction split. Treat the 60-minute leaning as the best of ~16. |

## Evidence so far (the window it was found in; does not count toward any gate)

12 full sessions, 2026-09-21 → 10-07, plus 10-08 to about 14:30 ET. SPX, SPY, QQQ, GLD, DIA.
Before costs.

| | Sessions | Events | 60 min, per-session mean ± SE | Followed through |
|---|---|---|---|---|
| Down-cross | 9 | 36 | +5.2 ± 7.4 bps | 7 of 9 |
| Up-cross | 9 | 36 | −9.1 ± 5.4 bps (= +9.1 for the fade) | 2 of 9 |

Neither clears 2 SE. It's one regime (rising long yields, VIX mid-teens) and was found after ~16
looks. **This is a leaning.**

## Why there is no stage 1

The signal needs the flip **within the session**. ThetaData history (T26) gives EOD chains only,
and no free source has intraday option positioning. The desk's own capture since mid-September
is the only intraday GEX in existence here, and it is the window the leaning was found in. So the
forward record is the test, which is why stage 2 uses the longer gate.

A cheaper partial check *is* possible: an **EOD flip** version (does a daily close crossing
the EOD flip carry into the next session?) can be tested on ThetaData history. That would be a
**different candidate** (`flip-cross-eod`), not evidence for this one.

## Execution until T142 ships

The desk has no alert for this signal yet. Until T142 logs crosses forward, a demo trade needs the
user to watch 5-minute closes against the current flip by hand, which is error-prone and will
miss signals. **Recommendation:** let T142 log the signals and score them, and take demo trades
from those logs. Then the journal's "signals not taken" column comes from the desk, not from
memory. T142's scored log gives the hypothetical record. The demo journal gives the real
costs.

## 0DTE variant (separate keys, frozen 2026-10-12, T154)

The same rule, unchanged, on the **0DTE** levels: `gex_levels` filter `ZERO_DTE` instead of `ALL`.
It is logged under its own keys (`FLIP_CROSS_DOWN_0DTE`, `FLIP_CROSS_UP_0DTE`), so its record never mixes with this one. It is a
**new candidate at stage 0** with no evidence, added at the user's request because the
all-expiries levels often sit far from price (on 2026-10-12, SPX's ALL put/call walls were
7500/8000 with spot at 7804; the 0DTE ones were 7810/7820). 0DTE levels sit close to price and move
within the session, so expect many more events. Read it with
`gex_level_events(key="FLIP_CROSS_DOWN_0DTE")`.

**IWM → US2000 added 2026-10-12 (T154).** US2000 is Axi's *cash* Russell 2000 CFD: no rolls, but a
**0.1-lot minimum** at 1 USD per point, ten times the `.fs` indices' minimum size. IWM events are
recorded per symbol, so they can be read apart from the original five.

## Kill rules (from the first demo trade)

The bootstrap-based thresholds need a trade distribution, which doesn't exist yet. Until 30 demo
trades exist, use these fixed rules. At trade 30, replace them with bootstrap values (95th-pct
drawdown, 99th-pct streak, CUSUM h for about 1 false alarm per 200 trades) and record the change
here with its date.

1. **Expectancy decay:** pause if mean R over the last 20 trades + 1 SE < 0.
2. **Losing streak:** pause after 8 consecutive losses (both keys counted separately).
3. **Drawdown:** pause at −10R cumulative from the strategy's peak.
4. **Cost drift:** pause if the rolling 20-trade average cost > 1.5× the cost measured in the first 20.
5. **Per-key:** `FLIP_CROSS_DOWN` and `FLIP_CROSS_UP` are paused independently. One can die while the other lives.

A pause means back to demo with a fresh sample, with the rule unchanged. Three pauses kill it.

## Stage log

| Date | Event |
|---|---|
| 2026-10-08 | Spec frozen. Found in-sample. Routed to stage 2 (no intraday history). Waiting for T142 for signal logging. |
| 2026-10-12 | Frozen rule (09:45–15:00 window, 60-min hold, one position per family and key, DIA dropped within 60 min of a same-key SPX/SPY cross; calendar filter **not** applied) run by hand over stored data, before costs, scored on the desk symbol rather than the CFD. In-sample 09-25 → 10-08: DOWN 27 trades / 9 sessions, +3.3 ± 7.2 bps per session; UP fade 30 / 9, +8.7 ± 5.3 — the same leaning, still under 2 SE. **Out of sample (10-09 → 10-12 11:50 ET): 1 trade**, QQQ→NAS100 UP fade 10-09 10:25 ET, −7.6 bps. No crosses on SPX/SPY/DIA/GLD in that window. Nothing to conclude. |
| 2026-10-12 | Path study, same 57 in-sample trades, on the **CFD's own M1 bars** (`broker.bars`), short from the M1 close at entry to 16:00 ET, no exit rule. Unit u = 0.5 × the CFD's median RTH 5-min range (S&P.fs 2.5, NAS100.fs 13.8, DJ30.fs 18.5, XAUUSD 2.1 pts). Hold-to-close mean +2.2u (DOWN +1.2u, UP fade +3.0u), skewed by a few large days. Race-to-level grid (±2/4/8/16u, 16 cells): reaching 8u against before 2u in favour happened in 3 of 57. **This adds 16 more looks to the search count**; any stop/target picked from that grid is a new candidate, not this one. |
| 2026-10-12 | T142 built: crosses are logged forward to `gex.level_events` (shared with wall-bounce since T153) and scored after the close (fixed horizons + CFD excursion path) into `gex.level_event_scores`; read with the MCP tool `gex_level_events`. Counts from its deploy date. T153 sends fresh, tradeable crosses to Telegram. |
