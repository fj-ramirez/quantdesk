# Read review — 2026-10-07 session

The market-research skill was asked for trade opportunities in QQQ, SPY, IWM and GLD, "taking
0DTE gamma and the terminal regime into account". This file records what the read said, what went
wrong with it before the open, and how the session actually traded against it. It covers one
session. Nothing here is evidence of an edge.

## 1. The first read used the prior close as the current price

The first pass, at about 09:35 ET, quoted Tuesday's EOD `spot` from `gex_levels` as though it
were the live price. It said QQQ was "on the 760 wall" at 760.2. The desk's own
`gex.intraday_bars` already showed QQQ at 754.3, SPY at 775.5 and GLD at 374.6. GLD had already
broken through its flip (379.8), and the read did not mention it.

Nothing was broken in the data. The first intraday chain capture is scheduled for 09:45 ET
(`INTRADAY_FIRST_CAPTURE`, `gex/jobs/scheduler.py`), so between the open and 09:45 the newest
snapshot is always the prior session's EOD book. The `ZERO_DTE` rows were null for the same
reason: Tuesday's expiries were gone, and Wednesday's sat in `net_gex_this_week`.

**Fix:** two rules were added to `.claude/skills/market-research/SKILL.md` (Levels section):

- `spot` in `gex_levels` is the price at capture time. During a session, the live price comes
  from `gex.intraday_bars` and must be shown beside it. The regime is judged from the live price.
- An EOD capture has no 0DTE profile for the next session. Until that session's first capture
  lands, the read says so instead of interpreting the null rows.

The rules are untested. No eval ran (see §5).

Coverage gap found on the way: **IWM is EOD-only.** It has one capture per session, no intraday
captures, and no intraday bars (it is not in `INTRADAY_BARS_SYMBOLS`). During a session the desk
cannot say where IWM trades.

## 2. The corrected read (09:45 ET capture)

Today's 0DTE layer flipped SPY and QQQ to negative total gamma. All four index products opened
just below their flips.

| | Spot | Flip (ALL) | Flip (0DTE) | Net ALL / 0DTE |
|---|---|---|---|---|
| SPY | 774.7 | 776.2 | 779.2 | −3.3bn / −2.6bn |
| QQQ | 752.7 | 754.8 | 758.5 | −1.3bn / −1.8bn |
| SPX | 7774 | 7730 | 7792 | +52bn / −8.3bn |
| GLD | 375.0 | 378.2 | 377.1 | −324mn / −43mn |

SPX and SPY disagreed on total gamma sign, so the multi-day index read was ambiguous. They agreed
on the 0DTE layer.

What the read said:

- **Headline:** "Since all four are below their flips, today favours momentum over fading."
- **Breakdown levels** (where moves should extend): SPY 773, QQQ 750, SPX 7750, GLD 375/370.
- **Reclaim levels** (where moves switch back to damping): SPY 776, QQQ 755, SPX 7792, GLD 378.
  After a reclaim, the targets were SPY 780 and QQQ 760.
- **GLD:** the cleanest one-way setup, matching the engine's open CONTINUATION_DOWN short (#350,
  entry 381.8, stop 387.8).
- **IWM:** no intraday read. Tuesday's levels (negative gamma, loss of 280 → toward 275) were the
  only information, along with a lean against the engine's CONTINUATION_UP long (#362).

## 3. How the session traded (5-minute bars, 09:45–16:00 ET)

| | Breakdown level | Low | Reclaim level | Reclaimed? | High | 0DTE flip | Close |
|---|---|---|---|---|---|---|---|
| SPY | 773 | 773.6 (10:50) | 776 | yes, ~11:30 | **779.1** | **779.2** | 777.3 |
| QQQ | 750 | 751.8 (09:45) | 755 | yes, ~11:30 | **758.2** | **758.5** | 757.9 |
| SPX | 7750 | 7763 (10:50) | 7792 | yes, ~11:30 | **7807** | **7805** | 7801 |
| GLD | 375 / 370 | 374.9 (09:45) | 378 | touched at 378.2 (13:10), didn't hold | **378.2** | 378.2 (ALL) | 375.9 |
| IWM | 280 | 276.9 | · | no | 279.0 | · | 277.7 |

IWM's row is from `gex.daily_bars`, because no intraday bars exist for it.

How gamma changed through the session (hourly captures, ALL filter):

| | 09:45 | 10:45 | 11:45 | 12:45 | 13:45 | 14:45 | 15:45 |
|---|---|---|---|---|---|---|---|
| SPY net | −3.30bn | −4.71bn | −1.25bn | +0.27bn | +2.03bn | +1.54bn | +1.12bn |
| QQQ net | −1.31bn | −0.77bn | +0.62bn | +0.59bn | +1.08bn | +1.22bn | +1.32bn |
| SPX net | +52bn | +44bn | +69bn | +76bn | +87bn | +88bn | +89bn |
| SPX 0DTE net | −8.3bn | −12.1bn | −6.4bn | −5.5bn | −2.9bn | −3.6bn | −0.3bn |

## 4. Scorecard

| Claim | Result |
|---|---|
| Headline: momentum over fading | **Wrong.** None of the breakdown levels broke. The low of the day came in the first hour, and buying that dip worked. |
| Below the flip, moves amplify | **Not seen.** For two hours in negative gamma, the range was tight (SPY 773.6–776). There was no acceleration. |
| A reclaim switches back to damping | **Right.** All three reclaimed around 11:30 ET, and total gamma turned positive (QQQ by 11:45, SPY by 12:45). SPX then held 7790–7807 for over four hours and closed at its 7800 wall. |
| Targets after a reclaim: SPY 780, QQQ 760 | **Missed.** Both stalled just below, at the 0DTE flip, which stayed negative all day. |
| GLD one-way short | **Neither.** It never broke 375 or 370. It rallied to exactly its flip and failed. #350 is unstopped and marked at about +1R at the close. |
| IWM: lean against the long, 280 → 275 | **Mostly right.** It opened below 280, reached a low of 276.9, and didn't get to 275. #362 traded through its 277.5 stop (the scoring job will confirm). IWM fell while SPX rose. |

**The observation worth tracking:** each index product's high of the day landed on its 0DTE
flip: SPY 779.1 vs 779.2, QQQ 758.2 vs 758.5, SPX 7807 vs 7805. GLD's high landed exactly on its
ALL flip, 378.2. When spot rallied up to the flip from below, the flip acted as a ceiling. That
is n=1, so it is a hypothesis to test across sessions, not a finding.

## 5. Lessons for how the read is written

1. **Live price first.** This is now a skill rule (§1).
2. **When spot is near a flip, give both paths equal weight.** "Below the flip" did not mean
   "moving away from it". On this day the flips pulled price back up toward them. The two
   conditional paths in the read were both right as conditions; leading with one was the
   mistake.
3. **Name the 0DTE flip as a level**, not only the walls. On this day it marked the session high
   more precisely than any wall did.

Not done yet: an eval for the skill change. Three cases are proposed: a pre-09:45 gap, an
EOD-only symbol, and a weekend capture that must not be called stale. Also open: whether to put
IWM in `INTRADAY_BARS_SYMBOLS` and the intraday chain capture.
