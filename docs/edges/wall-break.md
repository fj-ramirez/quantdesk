# wall-break: two closes through a gamma wall that stood still

**Stage:** 0, logged and alerted, not traded. Spec frozen 2026-10-14, before any data was
looked at. Like wall-bounce it needs the walls **within the session**, so no history exists for
a stage 1. It is **not** a trade candidate: there is no executor class. It is a forward record
with a Telegram alert, at the user's request (2026-10-14).
**Next gate:** a first read at **≥ 30 sessions** with events, through
`gex_level_events(key="PUT_WALL_BREAK")` / `key="CALL_WALL_BREAK"`. Until then any mean is a
leaning (the tool's session-clustered SE).
**Source:** an Instagram reel (Skydweller Trades, reeler batch 2026-10-14): "at a big gamma
level dealer hedging pushes price back; if price moves through it, hedging flips and drives
price quickly to the next level." The desk already logs the bounce half
([wall-bounce](wall-bounce.md)). This logs the other half. Filed as **T164**.

## Why log it

- It is the **failure mode of wall-bounce**. Each break is a touch that did not hold. Break
  and bounce counts at the same walls say how often the bounce premise holds at all.
- The reel's claim has a direction, so it can be measured: after a break, price keeps going
  through the wall.
- It costs no EdgeLab trial and raises no noise ceiling, because nothing is backtested.

## Spec (frozen)

| Field | Value |
|---|---|
| Hypothesis | After two 5-minute closes through a wall, price continues in the direction of the break. |
| Mechanism | The reel's: past the wall, dealer hedging stops absorbing the move and adds to it. *Reasoning, not tested.* The desk names walls by gamma sign (T99), so the put wall is a negative-gamma strike. There, hedging *adds* to a move anyway, so the story is the reel's, not the desk's. |
| Instruments | As wall-bounce: SPX, SPY, QQQ, DIA, GLD and IWM, measured on the desk symbol and on the CFD path (`broker.basis` pairs). |
| Data and timing | The wall is `gex.gex_levels.put_wall` / `call_wall`, filter `ALL` (and `ZERO_DTE` under `_0DTE` keys), from the **latest in-session capture with that wall and `captured_at` ≤ the bar's close**. Price is `gex.intraday_bars` 5-minute closes. |
| Event | **Put wall W:** a close below W, then the next close (of a bar that has a wall) also below W, with the close before them above W. The event is the second close below: `PUT_WALL_BREAK`, pointing **down**. **Call wall:** mirror image, `CALL_WALL_BREAK`, pointing **up**. |
| Same strike | The wall known at **all three** closes must be the same W. A wall that moves to a new strike past a flat price is not a break. |
| Ties | A close exactly on W has no side and breaks the run, as for the flip cross. |
| Measured | Exactly as every level event (`gex.level_event_scores`): desk-symbol returns 15/30/60 min and at the close, and the CFD's 1-minute excursion path, all signed in the event's direction. |
| Not measured | "Reaches the next level." The score has no next-level column. It can be read later from the path against the level map. If it matters, it becomes a new measurement with its own start date, never a re-score of past rows. |
| Alerts | **Telegram**, under the same rules as every level event (`select_alerts`): fresh, 09:45–15:00 ET, and one per CFD family and key within 60 minutes. The message says "not traded". |
| Search count | **1 rule**, the flip cross's three-close structure moved onto the walls, chosen without looking at any data. |

## Evidence so far

None. The forward record starts the day T164 is deployed.
