# T156: COT positioning extremes, stage-1 study. Declared before the first run.

**Frozen 2026-10-13, before any result was computed.** Nothing below changes after the first
run. A variant is a new family with its own declaration and counts against the ceiling as well.

## Hypothesis

When a trader group's net futures position sits at an extreme of its own three-year range, the
CFD on that future has a predictable drift over the next one to four weeks. Two mechanisms
point in opposite directions, so both directions are tested:
- **contrarian:** a crowded position has to unwind;
- **follow:** the group is informed, or its flows persist.

## Search space: 84 trials

| Axis | Values | Count |
|---|---|---|
| Instrument × positioning series | see table below | 14 |
| Direction | `contrarian`, `follow` | 2 |
| Hold | 5, 10, 20 trading days | 3 |

| CFD | Price proxy (Yahoo daily) | Positioning series (net = long − short, contracts) |
|---|---|---|
| `S&P.fs` | `^GSPC` | Legacy non-commercial `13874A`; TFF leveraged funds; TFF asset managers |
| `NAS100.fs` | `^NDX` | the same three on `209742` |
| `DJ30.fs` | `^DJI` | the same three on `124603` |
| `XAUUSD` | `GLD` | Legacy non-commercial `088691`; Disaggregated managed money |
| `EURUSD` | `EURUSD=X` | the same three as the indices, on `099741` |

`contrarian` and `follow` are mirror images before costs, so the 84 trials are really 42
independent signals. All 84 still count towards N.

## Fixed choices (none of them searched)

- **Extreme:** the net position's percentile within the trailing 156 reports, including the
  current one (the same as `derive.rolling_percentile`), is **≥ 90** (the group is crowded
  long) or **≤ 10** (crowded short).
  - `contrarian` goes short when the group is crowded long, and long when it is crowded short.
  - `follow` does the opposite.
- **When the signal is known:** the release date is the Friday the adapter's `release_as_of`
  gives.
  - If that day is a US federal holiday or the day after Thanksgiving, it moves to the next
    weekday.
  - Reports whose publication was delayed by a government shutdown are **dropped as
    signals**. These are report dates 2013-09-30 → 2013-10-31, 2018-12-24 → 2019-03-01 and
    2025-09-30 → 2026-01-13.
- **Entry:** the close of the first trading day of the proxy **strictly after** the release
  date. That is usually Monday's close. It gives up the Friday-to-Monday move, but leaves no
  doubt about when the bar closed relative to 15:30 ET, which matters for the FX daily bar.
- **Exit:** the close *h* trading days after entry, or the stop if it is hit first.
- **Stop:** 2 × ATR(20) of the proxy's daily bars at entry, checked from the day after entry
  on daily highs and lows. A gap through the stop fills at the open. The executor requires a
  stop on every order (invariant 11), so the study has one too. **R = signed P&L ÷ stop
  distance.**
- **One position per trial.** While a trade is open, new signals are ignored. Once it has
  exited, the next release that is still extreme can enter again.
- **Costs**, as fractions of price measured on the broker's own data on 2026-10-13 and applied
  in proportion to the proxy's price:
  - Round-trip spread: 1.15e-4 (`S&P.fs`, 0.90 on 7856), 8.1e-5 (`NAS100.fs`, 2.5 on 30,800),
    7.7e-5 (`DJ30.fs`, 4.0 on 51,930), 3.8e-5 (`XAUUSD`, 0.16 on 4162) and 6.2e-5 (`EURUSD`,
    7 points on 1.12).
  - Swap per calendar night held, from `broker.symbol_specs`. `XAUUSD`: long −1.48e-4, short
    +9.7e-5. `EURUSD`: long −5.7e-5, short +3.05e-5. The `.fs` CFDs: 0.
  - Rolls: a `.fs` trade held across a quarterly index roll (the Monday of the week of the
    third Friday of Mar/Jun/Sep/Dec, which is where T145 found every observed `.fs` roll, for
    example 2026-12-14) pays one extra round-trip spread, on the assumption that it is closed
    and reopened.
    *Amended 2026-10-13, before the first run: the first draft said "eight days before the
    third Friday", which does not match T145's observed roll Mondays. This is a cost-model
    fact, not a searched parameter.*
  - The gate doubles the spread and the swap together.
- **Index carry (gross, not a cost):** a cash index is not the future the `.fs` CFD tracks. A
  long future earns the cash return minus (r − q). r is the 13-week T-bill (`^IRX`). q is
  fixed: S&P 1.8 %, NDX 0.8 %, DJI 2.0 %. So r − q is subtracted from longs and added to
  shorts, pro rata per calendar day. This is not doubled.
- **Split:** in-sample before **2018-01-01**, out-of-sample from then on. Calendar years are the
  walk-forward windows.
- **Clusters for the SE:** an *episode* is a run of consecutive releases with the same
  extreme side. Trades from one episode form one cluster.

## Gate (the edge-research skill's stage 1, as written)

1. ≥ 100 trades and ≥ 30 independent clusters (episodes).
2. Mean R > 0 after doubled costs.
3. Mean > 2 × the episode-clustered SE, at doubled costs.
4. Positive in most calendar years, at doubled costs.
5. Positive in both the in-sample and out-of-sample windows (the regime check).
6. OOS Sharpe at 1× cost above the noise ceiling √(2 ln N) / √(OOS years), with N = the
   registry's trial count plus these 84. The family-only ceiling (N = 84) is reported as well.

Verdict per trial: **pass** (all six), **lead** (everything except 6, with t ≥ 2), or **fail**.

## Known weaknesses, stated in advance

- Weekly extremes are rare and persistent. Twenty percent of ~880 weeks, in long runs,
  means criterion 1 may be **unreachable at 10- and 20-day holds by construction**. If so, that
  is a property of the signal, not a reason to loosen the gate.
- Before about 2012, the minis carried only part of the positioning (see the plan's *Verified
  facts*).
- The swap is today's swap, applied as a constant fraction. Historical swap followed rate
  differentials (EUR rates were negative 2015–21), so it can be wrong in either direction.
- GLD is not spot gold (fees of 0.40 %/yr, US hours). `EURUSD=X` is Yahoo's composite, not
  Axi.
- The release-date rule is at least as late as the true publication in every case checked,
  and later in some. Being late is the safe direction.
