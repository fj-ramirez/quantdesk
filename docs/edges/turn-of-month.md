# turn-of-month: long the index over the month boundary

**Stage: lead, not a candidate (2026-10-16, ledger #539–540).** On 1990–2019 history the rule
earns +0.25% (^GSPC) and +0.45% (^NDX) per trade after the doubled CFD spread, with t 2.54 and
2.71. Both halves of the sample are positive, and 21–22 of 30 years are positive. It does not
clear the search bar of 3.55. Its margin over an ordinary 3-day hold is t 1.85 / 1.75. On the
CFDs (2019–26, #295–297) it is positive with t < 1.2.

*Under the selection-count bar (2026-10-16, `search-log.md`):* its family is #295–297 + #539–540,
so k = 5 and the t bar is 2.0, which both rows clear. The verdict is not changed retroactively.
What stands between it and a candidate is criterion 5: the margin over an ordinary 3-day hold is
t 1.85 / 1.75. Promoting it takes a new, declared run.

## Spec (frozen; the #295–297 rule, unchanged)

| Field | Value |
|---|---|
| Hypothesis | Index returns concentrate from the last trading day of a month to the 3rd trading day of the next. |
| Mechanism | Month-boundary cash flows: salaries, pension contributions, coupons and dividends reinvested at the turn (Ogden 1990). *Reasoning, from the literature.* |
| Instruments | ^GSPC → S&P.fs, ^NDX → NAS100.fs. Same index; the `.fs` CFDs track the future, and roll steps are removed in the broker-span test. |
| Entry | The 16:00 close of the month's last trading day. Known in advance from the calendar. |
| Exit | The 16:00 close of the 3rd trading day of the next month. |
| Regime condition | Always. |
| Costs assumed | The CFD's median recorded spread as a % of price, doubled. `.fs` swap is 0. |
| Search count | 2 tests here (#539–540) + 3 on the broker span (#295–297). Cumulative N = 544. |
| Source | Lakonishok and Smidt (1988); Ariel (1987); McConnell and Xu (2008). |

## Result (run once, 2026-10-16)

| | ^GSPC | ^NDX |
|---|---|---|
| Trades | 353 | 353 |
| Net mean (gross) | +0.251% (+0.288) | +0.451% (+0.478) |
| t | 2.54 | 2.71 |
| Halves 1990–2004 / 2004–19 | +0.338% t 2.28 / +0.164% t 1.26 | +0.602% t 2.04 / +0.301% t 1.96 |
| Years positive | 21/30 | 22/30 |
| Every 3-session hold (drift) | +0.065% | +0.154% |
| Excess over drift | +0.186%, t 1.85 | +0.297%, t 1.75 |

**Reading:** the effect is real in the sense the literature means, with the sign stable for 36
years. It is too small, and too much of it is ordinary drift, to clear a bar set by 544 tests.
It weakened in the second half. Under [search-log.md](search-log.md)'s rules a lead is not demo-traded.

## What would move it

Only a decision by the user, recorded here **before** any demo trade: accept it as a calendar
**bias**, not an edge, for example "on turn-of-month days, take only the long-side level
events". That is a new candidate with its own slug, and it runs as a forward test only. See
[directional-signals-2026-10-16.md](directional-signals-2026-10-16.md).

## Stage log

| Date | Event |
|---|---|
| 2026-10-09 | #295–297 on the CFDs: positive, n < 100, t < 1.2. |
| 2026-10-16 | #539–540 on independent history: lead. |
