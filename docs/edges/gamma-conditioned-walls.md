# gamma-conditioned-walls: let the gamma sign choose bounce or break

**Stage: 0, spec frozen 2026-10-16.** Stage 1 runs inside **T167**, on the T166 intraday
history (2023-01 → 2026-09-18). That history starts loading 2026-10-16 17:00 ET, so this spec
is written **before any of it exists**. Forward: read now, off rows the logger already writes.
Backlog row 49 ([hypothesis-backlog.md](hypothesis-backlog.md), pass 12).

## Why

The level record ([wall-bounce](wall-bounce.md), [wall-break](wall-break.md)) asks *whether*
price reacts at a wall. It does not ask *which way*. The dealer mechanism has a direction:

- **Net gamma > 0.** Dealers sell strength and buy weakness to stay hedged, so a move into a
  wall is absorbed. Expect a **bounce**.
- **Net gamma < 0.** Dealers hedge in the direction of the move, so a move through a wall is
  amplified. Expect a **break** that keeps going.

Sources: Barbon and Buraschi (2021), *Gamma Fragility*: intraday momentum under negative dealer
gamma, reversal under positive gamma. Ni, Pearson, Poteshman and White (2021), *RFS*: the same
in single stocks. Baltussen et al. (2021), *JFE*, tie end-of-day momentum to the same hedging.
**Not tested here.** Rest-of-day momentum without the gamma condition failed on the CFDs
(#536–538), so this rule depends on the condition itself.

## Spec (frozen)

| Field | Value |
|---|---|
| Hypothesis | A wall bounce follows through when the event's net GEX is > 0. A wall break follows through when it is < 0. |
| Instruments | As [wall-bounce](wall-bounce.md): SPX/SPY → S&P.fs, QQQ → NAS100.fs, DIA → DJ30.fs, GLD → XAUUSD, IWM → US2000, on the CFD path (`broker.basis`). |
| Entry | Exactly the existing keys' entries (`PUT_WALL_BOUNCE`, `CALL_WALL_BOUNCE`, `PUT_WALL_BREAK`, `CALL_WALL_BREAK`, each also `_0DTE`). The **only** addition is the filter: bounces need `net_gex > 0`, breaks need `net_gex < 0`, where `net_gex` belongs to the **same capture and filter** that produced the event (`ALL` for plain keys, `ZERO_DTE` for `_0DTE`). This is what `gex.level_events.net_gex` stores. Known at the confirming bar, so there is no lookahead. A null net GEX means unknown, and the event is not taken. |
| Exit | Unchanged from each key's spec (wall-bounce's frozen exit, 2026-10-14; wall-break's scoring). |
| Regime condition | The condition **is** the regime. A filtered-out event counts as "off: regime", not as a loss. |
| Costs assumed | T167's: the CFD's recorded M1 spread, doubled. |
| Search count | **4 tests** (bounce and break, × `ALL`/`_0DTE`), ledger numbers assigned when T167 runs them. The cumulative N is 544 before them. |
| Source finding | The 2026-10-16 directional-signals survey (this session); backlog row 49. |

## Stage-1 gate (T167)

The skill's gate, and two comparisons on top, all on the same events:

1. ≥ 100 trades over ≥ 30 sessions per conditioned key; same-session SPX/SPY/DIA events count once.
2. Mean R > 0 after doubled cost, > 2 SE clustered by session. The search-log bar
   √(2 ln N) is reported alongside.
3. Positive in IS (< 2024) and OOS (≥ 2024), and in most calendar years.
4. **Beats the unconditional key** over the same span. A filter that adds nothing is not a signal.
5. **The complement is worse:** the same key with the opposite gamma sign has a lower mean. If
   both signs do equally well, gamma is not what drives the result.

## Already visible in the forward log: the filter is confounded with the wall

Counts only, 2026-10-12 → 10-16, no returns read:

| Key | net GEX > 0 | < 0 |
|---|---|---|
| `PUT_WALL_BOUNCE` | 0 | 7 |
| `CALL_WALL_BOUNCE` | 2 | 0 |
| `PUT_WALL_BOUNCE_0DTE` | 11 | 24 |
| `CALL_WALL_BOUNCE_0DTE` | 18 | 2 |

Under the `ALL` filter, the put wall sits below the flip, so its events almost always happen in
negative gamma. The call wall sits above the flip, so its events happen in positive gamma. So,
under `ALL`, "bounce only in positive gamma" mostly means "only call-wall bounces", and
"break only in negative gamma" mostly means "only put-wall breaks". T167 must report each cell
**by wall and by sign**. The `_0DTE` keys are the ones where the two signs mix, so they are the
real test of the mechanism. This is reported here, not fixed: the rule stays as declared.

## Forward read (automated)

`gex_level_events(by_gamma=True)` (MCP) splits every key into `gamma+`, `gamma−` and `gamma?`
with the usual session-clustered returns and CFD paths. Nothing new is logged, because the
logger has recorded `net_gex` on every event since T142. Read it as a leaning until about 30
sessions per cell exist.

## Kill rules

This filter is a condition on [wall-bounce](wall-bounce.md) and [wall-break](wall-break.md),
not a separate order path. Their kill rules apply unchanged. Two of its own:

1. If T167 finds the complement (gate 5) equal to or better than the conditioned key, the
   filter is dropped, and the unconditional key stands on its own result.
2. Forward: if after ≥ 30 sessions per cell the `gamma+` vs `gamma−` difference has the
   opposite sign from stage 1, the filter is dropped.

## Stage log

| Date | Event |
|---|---|
| 2026-10-16 | Spec frozen before the T166 history exists. Forward read added to the MCP tool (`by_gamma`). |
