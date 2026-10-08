# Read review — 2026-10-08 session

The market-research skill was asked, in one conversation, for (1) the top three intraday trades
for the day with macro considered, (2) whether the previous day's failed "GEX says down" read is
related to today's selloff, (3) how to pick a direction when gamma is non-directional, and (4) a
backtest of the flip-cross rule that answer proposed. The backtest overturned that rule. This
file records what was said, what the data later showed, and the follow-up filed as **T142**.
It covers one session and a 12-session test. Nothing here is evidence of an edge.

## 1. The morning read (about 09:36 ET)

**Freshness.** `desk_status` showed the 10/07 EOD book (158 snapshots, 28 symbols), the terminal
at 10/07 22:00 UTC, and a calendar fetched at 07:05 UTC. The first intraday capture was not due
until 09:45, so there was no 0DTE profile, and live prices came from `gex.intraday_bars`.

**Macro context.** The 30y yield was 5.67% (`ust_cc.30y`, up 20bp since 9/24), the 10y 5.28%
and the 10y real yield 2.91%. HY OAS had moved 2.80 → 3.24 → 3.03. VIX was 15.5 with VIX9D at 11.8.
Brent was 125 and WTI 96. The calendar had jobless claims at 08:30 (consensus 200K, actual not
stored, see T140), the **30y auction at 13:01 ET** (last one 5.31% high yield, 2.6 bid-to-cover;
the publisher rated it "Low"), and Musalem at 13:40. The read treated the auction as the day's
main event.

**Track record used** (`gex_track_record`, resolved = `result_r IS NOT NULL`):

| Key | n | avg R ± se |
|---|---|---|
| CONTINUATION_DOWN (all) | 154 | +0.25 ± 0.11 |
| CONTINUATION_DOWN since 9/22 | 99 | +0.08 ± 0.12 |
| FADE_CALL_WALL since 9/22 | 22 | −0.85 ± 0.25 |
| CONTINUATION_UP since 9/22 | 18 | −0.58 ± 0.14 (0 wins) |

**The three ideas, all one bet on rates rising:**

1. **IWM short.** Net GEX −5.7bn, spot 277.7 against a flip of 291.1. Engine grade A. First stall
   point 275, invalidation on a reclaim of 280. IWM has no intraday bars, so it can't be
   checked intraday.
2. **GLD short on a rejection at the flip (379–380).** Stop 380.6, first target 376.5.
3. **SPY conditional short** on a break below about 774, target 767, stop 778. SPX (+89bn, flip
   7721) and SPY (flip 775.3) disagreed, so 769–775 was treated as a transition band.

It also listed what not to trade: today's FADE_CALL_WALL (SPX 7900, SPY 785, QQQ 760) and
CONTINUATION_UP decisions.

**How they traded, as of about 14:45 ET:**

| Idea | Result |
|---|---|
| GLD | High 379.9 at 13:15 ET, inside the zone and below the stop, then 377.6. Target 376.5 not reached; last 377.8. |
| SPY | Broke 774 around 12:45 ET, low 770.4, target 767 not reached; last 772.8. The real floor was the SPX flip, not the SPY put wall. |
| IWM | Not checkable intraday. |

## 2. Yesterday vs today: same setup, different push

At 09:45 ET the two sessions were nearly identical:

| at 09:45 ET | 10/07 | 10/08 |
|---|---|---|
| SPY spot vs flip, net gamma | 774.7 vs 776.2, −3.3bn | 774.6 vs 775.3, −2.1bn |
| QQQ spot vs flip, net gamma | 752.7 vs 754.8, −1.3bn | 753.4 vs 754.8, −1.3bn |
| SPX spot vs flip, net gamma | 7774 vs 7730, +52bn | 7774 vs 7741, +48bn |
| SPX 0DTE gamma | −8bn | −10bn |

SPY and QQQ sat below their flips, where dealers are short gamma, while the much larger SPX book
was long gamma above its own flip. This is the "SPX and SPY disagree" case the skill flags as
ambiguous.

- **10/07:** the gap-down stalled at SPX 7763, above its flip. SPY reclaimed its flip around noon
  and its gamma went −4.4bn → +3.7bn by the close. Hedging switched from chasing moves to
  pinning price. The regime read was right, but the direction read was wrong.
- **10/08:** the morning chopped around the SPY flip. The selloff began around **12:40–12:50 ET,
  roughly 15 minutes before the auction result**. The desk has no intraday yields and doesn't
  store auction results, so the cause isn't attributed. SPY gamma went from about −0.2bn to −10.2bn
  and QQQ from −1.2bn to −4.5bn. SPX gamma (all expiries) fell from +63bn to +4bn and SPX 0DTE gamma
  from −8bn to −19bn. **The low, 7731, came at the SPX flip (7736–7740)** and price bounced to about
  7760. That is the second session running where the SPX flip held as the floor.

**Conclusion:** gamma told us how hard moves would be pushed and where they would stop. It
didn't say which way the market would go or when.

## 3. Where direction has to come from

The answer listed six inputs: price crossing the flip, the trend that continuation signals use, the
downside lean of short gamma (vanna), a catalyst confirmed by VIX, where 0DTE gamma is building, and
the larger book when SPX and SPY disagree. It then proposed a rule built on crossing the flip.
**Section 4 overturns that rule.** The other inputs are reasoning, not tested rules. Note that
`greeks.py` computes vanna and charm per contract, but nothing aggregates them into exposures
(PLAN.md §3 item 5), so the desk cannot map the vanna effect yet.

## 4. The flip-cross backtest

**Data.** 12 full sessions (9/21, 9/22, 9/25 to 10/7) plus 10/8 up to about 14:30 ET. 9/23 and
9/24 have bars but no in-session captures. Symbols: SPX, SPY, QQQ, GLD and DIA, which have both
5-minute bars and 15-minute captures. 9/11 starts at 13:15 and was effectively excluded because
crosses there were rare.

**No lookahead.** Each 5-minute bar (`ts` is the bar start) is compared with `flip_point` (filter
`ALL`) from the latest in-session capture with `captured_at <= ts + 5 min`. Bars before the
session's first capture are excluded, so the previous EOD flip is never used.

**Rule.** A cross is two consecutive closes on one side of the flip, with an earlier close that
day on the other side. Enter at the second close. Exit at the first close back across the flip, or
at the last bar of the day. No overnight holds and no costs.

### Per trade

| Variant | Trades | Symbol-days | Avg (bps) ± se | Win % | Bars held |
|---|---|---|---|---|---|
| Cross, 2 closes | 83 | 23 | −1.9 ± 2.3 | 27% | 14.5 |
| Plus gamma sign agrees | 69 | 22 | −4.6 ± 2.8 | 25% | 16.3 |
| Short side only | 43 | 19 | −0.9 ± 3.5 | 26% | 14.8 |
| Long side only | 40 | 21 | −2.9 ± 3.1 | 28% | 14.3 |

**Verdict: the rule as proposed loses before costs.** Three trades in four are stopped out by a
close back across the flip. Price keeps returning to the flip, and the flip itself moves
between captures.

### Forward return after a cross, with no stop

Signed in the direction of the cross:

| | n | 15 min | 30 min | 60 min ± se | to close ± se |
|---|---|---|---|---|---|
| Down-cross | 46 | +5.6 | +3.8 | +10.3 ± 3.6 | +6.9 ± 3.5 |
| Up-cross | 40 | −0.8 | −6.7 | −11.4 ± 3.7 | −10.3 ± 5.3 |

Baseline: the average 60-minute forward return across all bars from 09:45 was +1.7 bps, and
open-to-close averaged −0.1 bps over 70 symbol-days, 54% of them down. **Market drift doesn't
explain the asymmetry.**

### Averaged per session

Crosses aren't independent: SPX, SPY and DIA cross together, often several times a day.
Averaging the 60-minute return per session gives this:

| | Sessions | Events | Avg (bps) ± se | Followed through | Per session |
|---|---|---|---|---|---|
| Down-cross | 9 | 36 | +5.2 ± 7.4 | 7 of 9 | 09-25 −45, 09-28 +7, 09-29 +9, 09-30 +27, 10-01 +2, 10-02 +25, 10-05 +13, 10-07 −12, 10-08 +19 |
| Up-cross | 9 | 36 | −9.1 ± 5.4 | 2 of 9 | 09-25 +16, 09-28 −7, 09-29 −21, 09-30 −6, 10-01 −4, 10-02 −37, 10-05 −1, 10-07 +3, 10-08 −25 |

**Leaning, not a result.** Down-crosses tend to follow through and up-crosses tend to fail. That
fits the engine's CONTINUATION_DOWN vs CONTINUATION_UP record and the downside lean of short
gamma. But:

- Neither row clears two standard errors.
- The pattern was found after trying four rule variants and several horizons, so it is partly a
  product of looking.
- The sample is 12 sessions from one three-week regime.
- SPX is the index itself and can't be traded directly.

### What changed

- **Withdrawn:** "enter on the flip cross, exit on the re-cross". It was presented as a workable
  rule before it was tested, and that was a mistake.
- **An up-cross is a warning, not a buy.** In this sample, crossing up into positive gamma tended
  to cap price rather than start a rally.
- **A down-cross is at most a candidate** for a short with a fixed holding period rather than a
  flip stop. It needs data it wasn't found on, which is **T142**.

## 5. Reproducing the backtest

All four queries ran read-only through the MCP `query_sql` tool. The core is shared:

```sql
WITH caps AS (
  SELECT s.underlying sym, s.session_date d, s.captured_at, l.flip_point flip, l.net_gex
  FROM gex.snapshots s JOIN gex.gex_levels l ON l.snapshot_id = s.id
  WHERE l.filter = 'ALL' AND NOT s.is_eod
    AND s.underlying IN ('SPX','SPY','QQQ','GLD','DIA')
    AND (s.captured_at AT TIME ZONE 'America/New_York')::date = s.session_date
    AND (s.captured_at AT TIME ZONE 'America/New_York')::time BETWEEN '09:30' AND '16:00'
    AND l.flip_point IS NOT NULL),
bars AS (
  SELECT symbol sym, (ts AT TIME ZONE 'America/New_York')::date d, ts, close
  FROM gex.intraday_bars WHERE symbol IN ('SPX','SPY','QQQ','GLD','DIA')),
j AS (  -- the flip known at each bar's close
  SELECT b.*, c.flip, c.net_gex FROM bars b
  JOIN LATERAL (SELECT flip, net_gex FROM caps c
                WHERE c.sym = b.sym AND c.d = b.d AND c.captured_at <= b.ts + interval '5 min'
                ORDER BY c.captured_at DESC LIMIT 1) c ON true)
-- side = sign(close - flip); a cross event is side = lag(side) = -lag(side, 2);
-- position = side when side = lag(side) and an opposite-side close occurred earlier that day;
-- P&L per bar = position * (next_close / close - 1) * 1e4, flat at the last bar of the day.
```

The forward-return tables use `lead(close, 3 | 6 | 12)` and the session's last close, signed by
the cross direction, from the cross events defined above.
