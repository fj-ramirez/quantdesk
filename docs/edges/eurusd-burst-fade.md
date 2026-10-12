# eurusd-burst-fade: a copy-trading provider's EUR/USD bursts

**Stage:** **stage 1 failed** (2026-10-11, ledger #429–430). See *Stage 1 result*.
**Next gate:** none for this rule. The 72 logged bursts stay as the sample any *new* hypothesis
about the provider's entry selector is found on. Such a hypothesis is a new candidate, pre-declared,
and is tested on data outside the bursts.

> **The statement contradicts two claims below (2026-10-11).** Bursts are **not** milliseconds
> apart: orders fill at about 3 per second over up to 28 s. And they do **not** all win: 8 of 66
> sell bursts lost, the worst at −20 pips, and none of the 8 is tied to news yet.
**Source:** the user, copying the provider "the holy grail" on their own MT5 account.
Conversation of 2026-10-10.

## What was observed

Reported by the user, from their own account while copying. These are not the provider's
published stats, which do **not** show a 100% win rate.

- **Bursts:** the provider opens 10–40 EUR/USD orders of **0.01 lot** each, a few milliseconds
  apart, all in the same direction and at the same price. Everything else he trades is a single
  0.01 lot.
- **Stops:** no stop-loss or take-profit is visible on the orders.
- **Outcome:** in **25+ bursts** that the user saw, every one closed in profit, at **≥ $0.80
  per 0.01 lot**, i.e. at least 8 pips.
- **The only failures seen:** bursts that were still open when **news** came out.

Bursts milliseconds apart at one price are an automated loop placing a single 0.10–0.40 lot
position in pieces. Because the entries are all at the same price, it is not a grid.

## The first three bursts, matched to the broker's feed

The user's times are Axi **server time, UTC+3**. Of the offsets from −7 to +4 hours, only +3
puts all three entry prices inside the M1 bar for that minute (`broker.bars`, EURUSD, bid +
recorded spread). This test is a clock check, not a search for a rule.

| # | Entry UTC | Side, lots | 15 min before | 60 min before | Position in prior 24h range | Won | First touch of close price | Worst against |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-08 16:00 | buy 0.08 | −4.9 pips | −16.0 | 0.36 | +16.1 pips ($1.61 / 0.01) | 73 min | 7.2 pips |
| 2 | 2026-10-07 15:38 | buy 0.04 | −3.0 | −0.9 | 0.22 | +8.5 ($0.85) | 39 min | 4.9 |
| 3 | 2026-10-06 12:05 | sell 0.21 | −6.1 | −33.9 | 0.99 | +12.3 ($1.23) | 23 min | 1.7 |

The "before" columns are signed so that **negative means price had moved against the direction
he then traded**. Close times were not reported. "First touch" is the first M1 bar after entry
that reached the close price, so it is a lower bound on the holding time.

Burst #2 is 0.04 lot. Read as 4 orders, it is below the 10–40 range the user describes, so the
order count of these three bursts is unconfirmed.

## Working reading (hypotheses, untested, n = 3)

1. **Short-term fade.** Every entry went against the previous 15 minutes (−3 to −6 pips). All
   three were inside the London–New York overlap, 12:00–16:00 UTC.
2. **Size grows with the stretch.** The lot size ranks the same as the 60-minute move into the
   entry: 0.9 → 0.04, 16 → 0.08, 34 → 0.21 (burst #3 also sat at the 24-hour high). This is the
   user's "something produces high conviction". With three points, a consistent order is weak
   evidence.
3. **The exit is not a fixed $1.** Profit ran from $0.85 to $1.61 per 0.01, so the close comes
   from a minimum-profit rule, a trailing stop or a signal, not a fixed take-profit.
4. **Loss mode: news with the burst still open.** A fade with no stop loses when a release turns
   a temporary move into a real one.

**Checked, no match:** the entries as band extremes, and the closes as moving-average touches.
This covered SMA and standard deviation over 14, 20, 50, 100 and 200 periods on M1, M5, M15 and
H1. That is **20 combinations**. Burst #3 is −1.5 to −3.7 SD on most of them. Burst #2 is not
stretched on any. The exits land on no average. Whatever sets the entry is not a textbook band,
or it is a short-horizon move threshold.

**Search count so far:** 3 bursts examined; 20 MA/band combinations; 1 lot-vs-stretch ranking
noticed after the fact. Every one of these counts against any rule frozen from them.

## Reverse-engineered from a real-account statement (2026-10-11)

**Source:** the user's Axi copy account, MT4-format HTML statement, 1:500 leverage,
2026-04-27 → 07-10 (the file stays out of the repo because it carries the account holder's name
and number). 1,936 closed trades: 1,858 EURUSD, 59 USDCHF, 19 GBPCHF. Orders opened within
5 s in the same direction were grouped into **69 EURUSD bursts**, with 1–93 orders each. Matched
to `broker.bars` EURUSD M1 through the MCP connector.

**Clock:** server time is **UTC+3**. Of the offsets from −5 to +5 h, +3 puts **69/69** entry
prices inside their M1 bar, and no other offset gets more than 5. This agrees with the first
three bursts.

### What the trades are

| | Finding | n |
|---|---|---|
| Direction | **66 sells, 3 buys.** Every USDCHF and GBPCHF trade is a buy, so the whole book is long USD | 69 |
| Payoff | 58 of 66 sells won, averaging **+6.7 pips**. Losers averaged −8.7. The full losers were −16 to −20 pips (closes per order to −28). Mean **+5.6 pips per burst** | 69 |
| Spread at entry | 0.6–0.7 pips (`broker.bars.spread`). Commission is 0 | 69 |
| Hold | median 110 min, range 1 min to 33 h, often overnight | 69 |
| Lot per order | 0.01 → 0.02 → 0.03 over the period. This is the copier's equity scaling, not the provider's signal. Order count does not predict the result (r = 0.06) | 69 |

### Entry: sell when price is above its recent mean (a lean, not a threshold)

The features are taken at the last completed M5 bar before entry. Each is ranked against
**every** M5 bar from 2026-04-25 to 07-11, so a random entry averages 0.50.

| Measure (sell bursts, n = 66) | Mean percentile | Share above the 80th |
|---|---|---|
| 15-min move | 0.64 | — |
| 60-min move | 0.69 | — |
| 4-h move | 0.72 | — |
| z-score vs 4-h mean (48 × M5) | 0.72 | 0.58 |
| z-score vs 12-h mean | **0.76** | 0.58 |
| Position in 24-h range | 0.74 | 0.44 |

- **The sells lean hard toward "price is up versus the last 4–24 h".** Naively, 0.76 is about
  7 SE above 0.5, but the bursts are clustered (same-day repeats), so the real SE is larger.
- **It is not a clean threshold.** 42% of the sells were below the 80th percentile of every
  measure, and a few were below the median.
- **The less-stretched sells lost more:**

  | z vs 4-h mean | n | Win rate | Mean pips |
  |---|---|---|---|
  | ≥ 1.0 | 41 | 90% | +7.0 |
  | < 1.0 | 25 | 84% | +4.0 |

  Four of the 8 losers (#34, #35, #64, #65) were sold *below* the 4-h mean.
- **The 3 buys came after large down days:** −102 and −121 pips in 24 h, at the bottom of the
  24-h range. So the fade does run both ways, but buys need a much bigger stretch. In this sample
  that asymmetry is indistinguishable from a **short-USD-only bias** in a falling EUR/USD
  (1.173 → 1.133 → 1.143).

### Exit: a trailing take of a few pips, a wide or discretionary stop

| | Finding |
|---|---|
| Winners | closed **1.9–4.7 pips (IQR) below their best excursion**, median 3.1. That is a trail or a "lock in once it turns" rule, not a fixed TP: the realised pips run from 0.2 to 29 |
| Winners' adverse excursion | median 5.8 pips, **max 25.3**. Winners sat through 20+ pips against them |
| Losers | #64 closed at its worst point (−20.1, MAE 20.6). #3 and #5 closed −20/−19 after a 28-pip MAE. These look like an equity or loss stop around 20 pips, or a manual cut |
| Basket closes | unrelated bursts and the CHF trades close in the **same minute** (e.g. 2026-06-25 14:42 server, with 8 entry times across 3 symbols). Part of the exit is account-level |

**Working reading:** a short-biased EUR/USD mean-reversion scalper. It sells after price runs
above its 4–12 h mean, banks a few pips with a trail, and tolerates ~20–25 pips of heat. That is
a negative-skew profile: about +6.7 pips × 88% against −20 pips × the occasional full loss.
Whether that survives without a falling EUR/USD is the open question. **The sample cannot
answer it**, because 66 of the 69 bursts are on the side the trend favoured.

**Search count added by this pass:** 6 entry measures × 2 cutoffs (80th percentile, z ≥ 1), and
1 offset scan (11 offsets). All of it counts against any rule frozen from these bursts.

## Frozen spec (stage 0, 2026-10-11)

Frozen by the user ("freeze it as written and run") **before** any backtest. It does not change.
A tweak is a new candidate with its own slug.

| Field | Rule |
|---|---|
| Hypothesis | EUR/USD reverts after stretching away from its 12-hour mean, as the copied provider's sells do. The mechanism is short-horizon liquidity provision: the retracement after flow-driven extensions |
| Instrument | Axi `EURUSD` (spot FX CFD, 0.01-lot minimum, 5 digits). Data: `broker.bars` M1 bid, plus the recorded spread |
| Signal | At each closed **M5** bar (M1 resampled, close = the last M1 close): z = (close − mean) / sd over the last **144 M5 closes** (12 trading hours), current bar included, sample sd |
| Entry, version A (#429) | z ≥ +1.5 → **sell**. z ≤ −1.5 → **buy** |
| Entry, version B (#430, short-only control) | z ≥ +1.5 → sell. Never buys |
| Fill | The open of the first M1 bar at or after the M5 bar's close, at most 10 min later. A sell fills at bid, a buy at bid + spread |
| Filters | One position at a time. A signal is skipped when the entry bar's spread is > 15 points (1.5 pips) or 0 (bad print) |
| Exit | **Stop-loss** 25 pips from the fill. **Trail:** once the best excursion reaches +5 pips, exit when price gives back 3 pips from the best. **Time stop:** the open of the first bar 24 h after entry. Long exits at bid, short exits at ask (bid + that bar's spread). Within one M1 bar the stop is checked first, then the trail against the best known *before* that bar, then the best is updated (conservative). A gap through a level fills at the bar's open |
| Costs | The bid/ask fills already pay one spread per round trip. **Gate figure: one further spread at entry (cost doubled).** Swap from `broker.symbol_specs` (2026-10-10): long −6.38 points per night, short +3.42, triple on Wednesday, charged at each 17:00 New York crossing |
| Regime condition | Always on |
| Test window | 2019-01-01 → 2026-04-26, plus 2026-07-11 → 10-05. Entries inside the burst periods (2026-04-27 → 07-10, 10-06 → 08) are excluded |
| Gate | ≥ 100 trades and ≥ 30 independent days. Mean net pips (doubled cost) > 0 with **t > 2, SE clustered by entry day**. Positive in most calendar years. Run **once**. B is reported beside A, and A passing on the strength of B's short leg only, during falling-EUR years, counts as the trend, not the fade |
| Kill rules | Written only if stage 1 passes, from that bootstrap |
| Search count | 12 feature cuts + 11 clock offsets (reverse-engineering), then 2 frozen variants run once |
| Script | `scripts/edges/2026-10-11-eurusd-burst-fade.py` |

## Stage 1 result (2026-10-11): fail

Run once, as frozen. Ledger #429–430.

| | A: two-sided | B: short-only |
|---|---|---|
| Trades / days | 16,317 / 2,259 | 8,146 / 1,871 |
| Mean, gate figure (2× spread + swap) | **−2.01 pips** | **−2.05 pips** |
| Mean at 1× spread | −1.01 | −1.05 |
| t, clustered by day | −20.4 | −14.2 |
| Years positive | **0 / 8** | **0 / 8** |
| Win rate · average win · average loss | 76% · +4.4 · −22.5 | 76% · +4.5 · −22.6 |
| 2026-07-11 → 10-05 | −1.3 (n = 295) | −1.6 (n = 132) |
| At 0.01 lot | −$36 / month | −$18 / month |

- **No edge on either side.** Before any spread the gross is about 0, so the doubled spread is
  the whole loss. Sells and buys lose alike (−2.06 / −1.96), and so does the short-only control.
- **The payoff shape matches the provider's:** about three-quarters wins at +4 pips, with
  occasional −25 stops. That is what a 5/3 trail with a 25-pip stop produces on any entry.
  A high win rate is the exit's signature, not evidence of an edge.

**Diagnostic, not a test** (it uses the burst period, so it counts toward nothing): the same
frozen exit applied to

| Entries, 2026-04-27 → 07-10 | n | Mean (1× spread) | Win rate |
|---|---|---|---|
| The provider's actual sells | 66 | **+4.0** | 94% |
| A sell every 30 min | 2,492 | +0.57 | 83% |
| A buy every 30 min | 2,492 | −2.21 | — |

The falling EUR/USD gave sells about +2.8 pips over buys. The provider's entries beat random
sells by a further ~3.4 pips. That is selection skill, or luck over 66 clustered bursts in one
regime. **It is not the 12-hour z-stretch**, which earns nothing over 7 years. What the
selector is (news flow, order flow, a different horizon, discretion) is unknown. A new
hypothesis about it is a **new candidate with its own pre-declared rule**, never a re-tune of
this one.

## Selector exploration (2026-10-12): the entries can be described, not reproduced

The user's position is that the provider's results are not luck ("it keeps happening"). This
pass looked for the entry selector in Axi bars. It is a **search on the burst sample**, not a
test. Script: `scripts/edges/2026-10-11-burst-selector-explore.py`.

1. **Price path around the 67 sells:**
   - Before entry: +12 pips over the prior 4 h and +5 over 1 h, but **flat over the last
     5 min**. They sell a rally after it pauses, not into the spike.
   - After entry: in their favour 66% of the time at +3 min. The median move is only +0.9 pips
     at +5 min and +4.9 at +4 h.
2. **Where they sell is fully describable.** Scored against all 84,634 minutes in the burst
   weeks (45 features, as AUC):
   - The strongest features are 12-h z (0.77), RSI on M15 (0.75), position in the 24-h range
     (0.74) and the 8-h return (0.74).
   - Every 1–5 min feature is about 0.5, i.e. chance.
   - Hour, minute, weekday and round numbers add nothing.
   - The provider's entries sit around the **top 10%** of a combined score.
3. **What separates them from equally stretched minutes:**
   - a stalled last 5 min (−0.15 against +0.8 pips);
   - a smaller 24-h move (+4.5 against +24.5);
   - price further below the previous day's high (16.6 against 4.7 pips);
   - gold *not* rallying with EUR (xau 120-min +11 against +24 bps).

   The 20-day trend filter does **not** separate them: 82% of all minutes were below that
   average anyway.
4. **But no rule built from those features reproduces the result, even in-sample.** Same frozen
   exit, one position at a time, sells, 2026-04-27 → 07-10:

   | Entries | n | Mean pips (1× spread) |
   |---|---|---|
   | Provider's actual | 66 | **+4.0** |
   | Random sells (every 30 min) | 2,492 | +0.57 |
   | Top 1–10% of the score | 49–175 | −0.12 … +1.75 |
   | Explicit rules V1–V6 (stretch + stall, ± below the prior-day high, ± small 24-h move, ± gold not joining) | 39–100 | +0.30 … +1.48 |

   **Conclusion:** the bars show *where* the provider sells (rallies stretched over 2–24 h that
   have paused), but not *which* of those rallies they pick. Rules fitted to the very weeks they
   were found on recover at most a third of the gap to the provider. Whatever picks the rallies
   is not in EURUSD, gold or S&P M1 bars. Candidates are news and release timing (no calendar
   history before T139), other FX pairs the desk does not ingest (DXY, EURGBP, USDCHF), order
   flow, or discretion.
   - **No rule was frozen from this**, so it adds no ledger rows.
   - **Search count of the pass:** 45 + 45 AUC features, 5 score cut-offs, 18 cross-asset and
     clock splits, and 6 explicit rules: about 120 looks on 66 sells.

**What would move this forward is more provider data, not more searching of these 66 sells:**
- **The copy account's statement from 2026-07-11 to today.** Months of new bursts the analysis
  has not seen, so a fair test of anything frozen now.
- **The provider's own history page** on the copy-trading platform. If it lists trades, it is
  a sample of hundreds.
- **Bursts in a rising EUR/USD.** If the provider also *buys* stretched dips when EUR/USD
  trends up, the skill is not just the 2026 downtrend.

## Selector loop (2026-10-12)

The user asked for a loop until a good result. Its rules, set before round 1: score each family on
the provider's *timing* (recall within −30/+5 min of their sells, and precision). A family goes to
an out-of-sample test only if in-sample it reaches recall ≥ 0.25 and ≥ +2.5 pips over ≥ 30 trades.
It is then frozen as written in the script and run once on 2019-01 → 2026-04-26 + 2026-07-11 →
10-05, cost doubled, gate t ≥ max(2, √(2 ln k)).

- **Round 1 (29 families, `2026-10-12-burst-selector-loop.py`):** Bollinger touch and re-entry,
  RSI 70/30, stochastic 80/20, Keltner, envelopes, pin bars, RSI divergence, CCI, the
  Asian-range false break, and a stall after a 2–4 h push, on M5/M15/H1/H4.
  - **0 of 29 pass the screen.** They fire 2–10 episodes a day against the provider's ~1.3, and
    precision is 0.03–0.12.
  - Best recall: BB touch on M5, 0.57.
  - In-sample means are −0.7 … +2.4 pips. Small sets near +4 (RSI H1, n = 11) don't count.
- **Round 2 (levels and clock, `2026-10-12-burst-levels-clock.py`):**
  - **No anchoring to a level.** Floor pivots, the prior-day high, the daily open and the VWAP
    bands are within 3 pips of an entry no more often than of a random minute.
  - VWAP z (AUC 0.71) is one more stretch measure.
  - **The clock does show:** sells cluster from 04:00 to 12:00 New York, with 8 a.m. NY at
    3.6× the random rate (the 08:30 releases).
- **Round 3 (structure, `2026-10-12-burst-baskets.py`): the provider is partly a grid.**
  - 10 of 47 sell baskets **add while losing**, usually 3–9 pips higher (15 at most), sometimes
    with a bigger lot (0.48 → 1.47, 0.60 → 0.81).
  - The basket closes together a few pips beyond its average price (median +3.9 for multi-burst
    baskets). The other 37 are single entries.

### Frozen grid spec (round 3, ledger #431)

| Field | Rule |
|---|---|
| First entry | As #429: z of the M5 close vs the last 144 M5 closes ≥ +1.5 → sell, ≤ −1.5 → buy; fill at the next M1 open; spread ≤ 1.5 pips; one basket at a time |
| Adds | One more unit (same size) each time price moves **6 pips** further against the last fill, at that level (or at the bar's open if gapped). **At most 5 units** |
| Take-profit | Close all when price reaches **5 pips** beyond the basket's average price, in its favour |
| Stop | Close all when price is **25 pips** against the *first* fill |
| Time stop | Close all at the open of the first bar 24 h after the first fill |
| Bar order | Stop first, then adds, then the take-profit (against the new average) |
| Cost | Bid/ask fills, plus one extra spread per unit at its entry. Swap ignored (holds < 24 h, and EURUSD swap is < 0.7 pips a night) |
| Unit of result | Pips × units per basket (each unit = 0.01 lot = $0.10 a pip) |
| Gate | Mean per basket > 0, t ≥ 2 clustered by day, positive in most years, out of sample only |

### Round 3 result and the end of the loop

**#431 fails:**
- **Out of sample:** 21,362 baskets, **−2.86 pips × units**, t −16.5, 0/8 years positive, 87% of
  baskets winning, worst basket −169.
- **In the burst weeks it was modelled on:** −2.19.

**The loop stops here**, by its own rule. Every family that can be built from the data on hand
has failed:
- price indicators: 29 families;
- price levels: pivots, VWAP, the daily open, the prior-day high;
- the clock;
- gold and S&P;
- the provider's grid structure;
- the single-entry fade (#429–430).

Searching the same 66 sells further would only raise the chance that the next "hit" is noise:
about 160 looks have been spent on them.

**What the provider must be using is something these bars don't contain.** The one lead that
needs only data the desk can get is **other currency pairs** (DXY components, EURGBP, USDCHF,
GBPUSD). Axi's MT5 history goes back years, and EURUSD's came back to 2018 within a day of being
added to `broker-ingest`. Adding them is a config change plus a homeserver deploy, and it is the
user's call. After that, the test is a new pre-declared hypothesis (e.g. "sell EURUSD when it
rallies while EURGBP and USDCHF don't confirm"), screened on the bursts and run once out of sample.

## Indicator observations (2026-10-12)

What standard indicators read across M1 → D1 at each entry and exit is written up in
[eurusd-burst-observations.md](eurusd-burst-observations.md). The headline: the provider sells
M15/H1 upper-band rallies **inside a D1 downtrend**, and takes profit when M5 is oversold. That
daily-trend context was missing from #429–431. It is untested.

## Observe-and-test loop (2026-10-12)

**Protocol, fixed before iteration 1:**
- **Where hypotheses come from:** only from the provider's readings
  ([eurusd-burst-observations.md](eurusd-burst-observations.md)) and from where a failed rule
  disagrees with the provider. Never from tuning on test results.
- **Development set:** EURUSD 2019-01-01 → 2022-12-31. Every iteration runs here once.
- **Holdout:** 2023-01-01 → 2026-04-26 plus 2026-07-11 → 10-05. It is touched **once**, by
  the final candidate only.
- **Development gate:** ≥ 100 trades, mean > 0 at doubled cost, t (clustered by day) ≥
  max(2, √(2 ln k)) where k = iterations so far, and ≥ 3 of 4 years positive.
- **Reference only:** each iteration also reports how well it replicates the provider in the
  burst weeks (recall, precision, mean).
- **Budget:** at most 6 iterations.
- **Script:** `scripts/edges/2026-10-12-pullback-loop.py <iteration>`.

**Common mechanics:**
- **Timing:** indicators from each timeframe's last completed bar. A signal is evaluated at
  each M1 close and fills at the next M1 open, at bid (sell) or ask (buy).
- **Positions and costs:** one position at a time; entry spread ≤ 1.5 pips; stop 25 pips;
  24 h time stop; one extra entry spread charged (cost doubled).
- **D1** is the broker day (17:00 New York), using EMA 20/50/200 of the D1 closes.

### Iteration 1 (ledger #432): trend pullback, as observed

| | Sell (mirror for buy) |
|---|---|
| Regime | Last completed D1: EMA20 < EMA50 < EMA200, and live price below the D1 EMA50 |
| Entry | Live M15 %B (Bollinger 20/2 of completed M15 bars) ≥ 0.85, **and** last completed M5 stochastic 14/3/3 %K ≥ 75 |
| Exit | Last completed M5 stochastic %K < 20 → close at the next M1 open. Otherwise the stop or the time stop |

The thresholds are the winners' median entry and exit readings (0.85, 75, 17 → 20).

**Result: fail.**

| Window | n | Mean pips | t | Years positive |
|---|---|---|---|---|
| Development 2019–22 | 2,725 | **−1.62** | −5.83 | 0/4 |
| Burst weeks (in-sample) | 87 | −0.92 | | |

**Observation step (burst weeks only):**
1. **The exit is wrong.** The provider's own entries with this exit make +1.15, against +4.0
   with the 5/3 trail (#429's exit). M5 oversold is a state at their exit, not its trigger.
2. **The rule sells a different kind of rally:**

   | Median at entry | Provider | Rule |
   |---|---|---|
   | 15-min return | +2.0 | +5.7 |
   | M5 %B | 0.79 | 1.05 |
   | M5 stochastic | 73 | 85 |
   | 4-h return | +11.4 | +5.2 |
   | H1 %B | 0.82 | 0.68 |
   | H1 RSI | 55.5 | 49.7 |
   | 24-h return | +4.5 | +13.8 |

   The provider sells a slow H1-scale rally that is *not* spiking on M5, on a day that is not
   strongly up. The rule sells M5 spikes.

### Iteration 2 (ledger #433): H1-scale rally, no M5 spike, trailing exit

| | Sell (mirror for buy) |
|---|---|
| Regime | As iteration 1 (D1 bearish stack, price below the D1 EMA50) |
| Entry | Live H1 %B ≥ 0.8 **and** live M15 %B ≥ 0.8 **and** live M5 %B < 0.9 (no M5 spike) |
| Exit | #429's: once the best excursion is +5 pips, exit on a 3-pip giveback; stop 25; 24 h time stop |

The thresholds are rounded provider medians (0.82, 0.87, 0.79).

**Result: fail.**

| Window | n | Mean pips | t | Years positive |
|---|---|---|---|---|
| Development 2019–22 | 2,180 | **−1.97** | −7.17 | 0/4 |
| Burst weeks (in-sample) | 62 | −0.09 | | |

In the burst weeks the sells made +1.24, but the mirror buys lost −1.60.

**Observation step:**
- **The conjunction of three medians was true at only 14% of the provider's sells.** It
  describes a corner of the provider's behaviour, not the behaviour.
- **The "avoids strong up-days" reading is retracted.** On calendar time the provider's 24-h
  return at entry has an IQR of −3 … +29 pips, and winners had *larger* 24-h rallies than
  losers (+12.6 against −4.8). The earlier figure came from a bar count.
- **Entry timing inside a condition run** doesn't matter in the burst weeks: +2.4 … +4.9 at
  0–60 min in.

### Iteration 3 (ledger #434): the provider's envelope

Thresholds chosen on **provider data only**, so the conditions jointly cover most of their sells.
D1 bearish + live H1 %B ≥ 0.5 + live M15 %B ≥ 0.5 covers 58% of the sells: 62% of the winners and
25% of the losers. The D1 condition alone caps coverage at 88%.

| | Sell (mirror for buy) |
|---|---|
| Regime | As iteration 1 |
| Entry | Live H1 %B ≥ 0.5 **and** live M15 %B ≥ 0.5 (upper half of both bands) |
| Exit | As iteration 2 (#429's trail / stop / 24 h) |

**Result: fail.**

| Window | n | Mean pips | t | Years positive |
|---|---|---|---|---|
| Development 2019–22 | 5,428 | **−1.95** | −11.9 | 0/4 |
| Burst weeks (in-sample) | 109 | −1.57 | | |

**Observation step: raw forward returns** (bid to bid, no costs, no exit logic), from the
start of each condition run:

| Iteration-3 condition | 15 min | 1 h | 4 h | 8 h |
|---|---|---|---|---|
| Sells, development 2019–22 (n = 1,686) | +0.24 | +0.23 | **+1.38 ± 0.55** | **+1.41 ± 0.75** |
| Sells, all 2019–26 ex bursts (n = 2,153) | +0.17 | +0.30 | +0.85 ± 0.48 | +1.02 ± 0.65 |
| Buys (mirror), all 2019–26 ex bursts | +0.15 | +0.25 | −0.35 | −0.92 |
| Provider's own sells (burst weeks) | +1.54 | +1.65 | +2.54 | **+4.89** |

- **This is the first out-of-sample edge in the loop.** Sells on a D1-downtrend pullback drift
  in their favour over 4–8 h. It is small, about 1–1.4 pips.
- **The mirror buys do not.** The provider never bought uptrend pullbacks either.
- **Every variant so far shows about −0.9 pips gross with a 76–77% win rate.** The 5/3 trail
  sets that shape, and it cuts off a move that builds over hours.

### Iteration 4 (ledger #435): sell the D1-downtrend pullback, hold 8 hours

| | Sell only |
|---|---|
| Regime | As iteration 1 |
| Entry | As iteration 3 (live H1 %B ≥ 0.5 and live M15 %B ≥ 0.5) |
| Exit | **Time: the open of the first M1 bar 8 h after entry.** Stop 25 pips. No trail, no target |
| Buys | None. The observation shows no buy-side drift, and the provider took no trend-pullback buys |

The 8 h horizon is the provider's own forward-return curve, which is still rising at 8 h. It was
not picked from the development numbers: 4 h and 8 h are about equal there.

**Result: fail.**

| Window | n | Mean pips | t | Years positive |
|---|---|---|---|---|
| Development 2019–22 | 1,355 | **−2.66** | −3.85 | 1/4 |
| Burst weeks (in-sample) | 31 | −1.64 | | |

The 25-pip stop fires on 38% of trades inside 8 h, so it truncates the recovering trades the
drift lives in.

### Where the loop ends (4 of 6 iterations used, holdout untouched)

- **The observable part of the provider's approach is real but too small to trade.** Selling
  the H1/M15 upper-half pullback inside a D1 bearish stack drifts **+1.0 to +1.4 pips over
  4–8 h** out of sample (2.5 SE on development). That is below the round-trip cost:
  - one spread (~1 pip) leaves about +0.4;
  - the doubled-cost gate (~2 pips) is negative.
- **No exit fixes that:**
  - the 5/3 trail cuts the move (gross ≈ the spread, 76% wins);
  - a 25-pip stop truncates it;
  - no stop at all is the forward-return figure above.
- **The provider's own sells drift about 3.5 pips more** than the envelope does (+4.9 against
  +1.4 at 8 h). That surplus is not in any indicator or timeframe measured here. Every
  observation of where they sell is now explained, and *which* of those moments they take is
  not.
- **The last two iterations were not spent.** With no observation left that points somewhere
  new, they would be fitting the development set. The holdout (2023 → 2026) stays untouched
  for any future candidate.
- **What could change it is cost, not search.** At an all-in round trip ≤ 0.5 pips (a raw-spread
  account, commission included) the +1.0 to +1.4 drift would be worth re-testing as its own
  pre-declared candidate. On the current 0.7–1.1 pip spread it is not.

### Resumed (2026-10-12): searching inside the setup, with the holdout as judge

The user challenged the stop ("there's an edge there"). The protocol allows going on, because
**the 2023 → 2026 holdout is untouched**. From here 2019–22 is exploration ground, and the holdout
alone decides.

**Breakdown** (`scripts/edges/2026-10-12-pullback-dose.py`, development only):
- Setup events ≥ 4 h apart: n = 1,614, raw sell-signed drift +0.87 at 4 h, +0.83 at 8 h.
- 11 features × quintiles, about 55 cells. Most zigzag, which is noise. Two cells are positive
  at 8 h in **all four years** and have a mechanism:

  | Cell | n | 4 h | 8 h | Years (8 h) |
  |---|---|---|---|---|
  | (A) live M5 %B > 1.04 (an M5 spike above the band inside the H1/M15 pullback) | 323 | +3.08 ± 1.34 | +3.46 ± 1.91 | +4.4 / +3.4 / +4.4 / +2.6 |
  | (B) price ≤ 0.6 D1 ATR below the D1 EMA50 (the rally has reached daily resistance) | 323 | +2.21 ± 1.27 | +3.82 ± 1.71 | +3.7 / +1.8 / +8.6 / +3.0 |

  London (03–08 NY) is +3.77 at 4 h but only 3/4 years at 8 h, so it was not taken.

### Iterations 5 and 6 (ledger #436, #437): frozen for the holdout

| | 5 (A): M5 spike | 6 (B): at the daily EMA50 |
|---|---|---|
| Base | D1 bearish stack, price below the D1 EMA50, live H1 %B ≥ 0.5, live M15 %B ≥ 0.5 | same |
| Plus | live M5 %B ≥ 1.0 | (D1 EMA50 − price) ≤ 0.6 × D1 ATR(14) |
| Side | sell only | sell only |
| Exit | open of the first M1 bar 4 h after entry; **40-pip** stop | same |
| Costs | one extra entry spread (doubled) | same |

- **Gate (holdout only, run once):** 2023-01-01 → 2026-04-26 plus 2026-07-11 → 10-05. Mean > 0
  at doubled cost, t (clustered by day) ≥ 2.0 (k = 2: √(2 ln 2) < 2), ≥ 100 trades, and positive
  in ≥ 3 of the 4 calendar years 2023–26.
- **Development runs are now in-sample.** They only check that the trade simulation reproduces
  the breakdown.

**Result: both fail on development, so neither was sent to the holdout.**

| Iteration | n | Mean pips | Gross | t | Years positive | Burst weeks |
|---|---|---|---|---|---|---|
| 5 (A), M5 spike | 1,457 | **−1.85** | −0.81 | −3.19 | 0/4 | −4.42 (n = 33) |
| 6 (B), at the D1 EMA50 | 441 | **−2.27** | −1.27 | −2.29 | 0/4 | never fired: the provider was never near the D1 EMA50 |

**Retraction, stated first:** the breakdown cells, and the "+1.0 to +1.4 pip drift" above,
**do not survive being traded.**
- **How the drift was measured:** from M15 closes or condition-run starts, events ≥ 4 h apart.
- **Taken as trades:** at the first M1 close where the condition holds, the same setups make
  about 0 before the spread.
- **What that means:** a real edge does not flip sign with the sampling. These were sampling
  noise, and the ~55-cell breakdown expects 2–3 such cells.
- **So the setup has no tradable edge:** nothing observable at the provider's entries is
  profitable after costs on 2019–22.
- **The holdout (2023 → 2026) is still unspent.**

**What the observations do say about the provider's edge:**
- Their sells are in their favour within minutes: +1.54 pips at 15 min, against about +0.2 for
  the same setup at any other moment.
- The surplus is there from the first minutes, so the selector works at **sub-minute timing**.
  That points to information M1 bars don't carry: tick flow, release timing, or another market.
- Axi EURUSD ticks are in `broker.ticks` only since 2026-10-10. The bursts that matter are the
  ones **still happening**, so the evidence-producing step is to **log them as they occur**
  against the tick record. Searching the 66 historical bursts again won't produce it.


### Entry replicator (ledger #438): a model learns the provider's entries; exit fixed by the user

The user's direction (2026-10-12): replicate the **entries**, with the exit fixed at **$0.50 per
0.01 lot, i.e. a 5-pip take-profit**.

- **Stop and time limit:** a 25-pip stop (the provider's real losers ran −16 … −28, and the
  executor needs a stop) and a 24 h time stop.
- **Break-even:** +4 / −26 after the spread → **≈ 87% wins** (more at doubled cost).

**Frozen spec:**

| Field | Rule |
|---|---|
| Universe | Every M5 close from 07:00 to 21:00 UTC (the provider never opens outside these hours) |
| Features (at the M5 close, completed bars only) | Live %B of M5, M15 and H1; RSI14 of M5, M15 and H1; M5 stochastic %K; returns over 15, 60, 240 and 1440 min (pips); (close − M15 EMA50) / M15 ATR; D1 EMA stack (−2 … +2); (close − D1 EMA50) / D1 ATR; bars since the M15 20-bar high |
| Model | L2-regularised logistic regression (λ = 1, features standardised on the burst weeks). Label: a provider *sell* opens inside the next M5 bar. Trained on 2026-04-27 → 07-10 only |
| Buys | The same model on mirrored features: returns, EMA distances and the D1 terms negated, %B → 1 − %B, RSI and stochastic → 100 − x, bars since the M15 20-bar *low* |
| Threshold | The score quantile at which the burst weeks give **≈ 1.3 sell episodes per trading day** (episodes ≥ 30 min apart), the provider's rate. One threshold for both sides |
| Entry | The next M1 open after the M5 close. One position at a time; spread ≤ 1.5 pips |
| Exit | TP 5 pips; SL 25 pips (checked first within a bar); 24 h time stop. Shorts exit at ask |
| Costs | Bid/ask fills plus one extra entry spread (doubled) |
| Gate | **Development 2019–22**, run once: ≥ 100 trades, mean > 0 at doubled cost, t (clustered by day) ≥ 2, ≥ 3/4 years positive. Pass → **holdout** 2023-01 → 2026-04-26 + 2026-07-11 → 10-05, run once, same gate |
| Script | `scripts/edges/2026-10-12-burst-replicator.py` |

**Result: fail on development, so the holdout was not spent.**

| | n | Mean (doubled cost) | Win rate | Notes |
|---|---|---|---|---|
| **Provider's own entries**, same TP5/SL25 exit | 66 sells / 3 buys | **+2.49 / +4.33** | 94% / 100% | the target to replicate |
| Model, burst weeks (in-sample) | 331 | −1.34 | 80% | sells +0.97 (81), mirrored buys −2.09 (250) |
| **Model, development 2019–22** | 10,224 | **−2.03** (t −16.7) | 79% | sells −2.11, buys −1.90; 0/4 years |

- **Training:** 59 provider sells (7 fell outside 07–21 UTC or shared an M5 bar) against 9,240
  M5 closes.
- **Fit:** in-sample AUC 0.83. At the provider's own rate (≈ 1.3 episodes a day) the model fires
  within −30/+5 min of **half** their sells.
- **Weights:** largest on M5 RSI (+), H1 %B (+), H1 RSI (−), and the D1 bearish stack and
  distance below the D1 EMA50 (−). That is the observations' picture: M5 overbought, H1 upper
  band, daily downtrend.
- **The model finds the provider's kind of moment but not their choice of moment.** The other
  half of its signals, at the same rate, win only ~80% against the 87% break-even, even
  in-sample. The 94% that separates the provider is not in these 15 readings.
- **The mirrored buys fire far more** (250 in-sample) and lose. The provider's buys are not a
  mirror of their sells.


## Risk shape

- **The wins are small:** 8–16 pips, about $1 per 0.01 lot.
- **The losses can be large:** with no stop, one release (NFP, CPI, an ECB or FOMC decision) can
  move EUR/USD 50–100 pips against an open burst. On 0.21 lot that is $105–210, against about
  $25 for a winning burst of the same size.
- **The biggest bursts carry the most risk:** they follow the most stretched moves, which is when
  news-driven moves are most likely.
- **A 100% win rate is not the evidence.** It is what a strategy with no stop produces by
  construction. Profitability turns on how large the news losses are compared with all the
  small wins.
- **The exposure is predictable:** bursts last 23–73 minutes, so one is exposed only if it is
  open in about the hour before a scheduled release.

## What to log per burst

Add rows to [eurusd-burst-fade-observations.csv](eurusd-burst-fade-observations.csv). Server
time is fine; it is converted to UTC when matched. Leave unknowns empty (null ≠ zero). **Log the
losses too, especially at news.** A log of wins only measures memory, not the strategy.

| Field | Why |
|---|---|
| Open time (to the second), direction, number of orders, total lots, entry price | Entry rule, and size against stretch |
| Close time and close price | The exit rule: minimum profit, trailing stop or time limit |
| Any burst that lost: release, loss in $, time held | Sizes the loss tail that decides whether it pays |

The provider page's **maximum drawdown**, **profit factor** and equity curve (as opposed to the
balance curve) would size the losses without trade-level data.

## Path forward

1. ~~**Compile 15–20 bursts**, including any losses.~~ Done 2026-10-11: 72 bursts, 8 of them losers.
2. **Freeze a spec (stage 0)** from them, in a new section here. Candidates to freeze from the
   reading above: sell when z vs the 12-h mean (M5) ≥ a fixed level, symmetric buy at the mirror
   level; trail of 3 pips once in profit; stop at 25 pips; 24 h time stop. **Freeze both a
   symmetric and a short-only version.** The short-only one only shows whether the result was the
   trend. The spec must contain:
   - the entry threshold and window;
   - the sizing rule;
   - the exit;
   - a news blackout;
   - **a maximum-loss exit**.

   A rule with no stop has no R, and so no kill rule.
3. **Stage 1** on EURUSD M1 history that **does not overlap** the compiled bursts
   (2026-04-27 → 07-10 and 2026-10-06 → 08). `broker.bars` already holds Axi EURUSD M1 from
   **2018-12-20** (2.9 M rows on 2026-10-11). That is enough for a walk-forward across 2019–2025,
   with years of rising and falling EUR/USD. A 7-year bar-by-bar simulation will not fit in the
   MCP connector's 10 s statement limit. It needs a local export (`backend/data/broker-export/`,
   alongside the index CFDs), the same as the other edge scripts.
   - Run it with and without the news blackout.
   - The calendar's timestamped history starts only at T139, so before that the blackout cannot
     be applied. Report those results as such.

## Stage log

| Date | Event |
|---|---|
| 2026-10-10 | Observation filed. Three bursts matched to the broker feed: clock UTC+3, all three fades in the overlap, lot size ranked with the 60-minute stretch. EURUSD added to `broker-ingest` (PR #21) to give the broker's own bars and ticks for matching. |
| 2026-10-11 | Real-account statement (2026-04-27 → 07-10) reverse-engineered: 69 bursts, clock UTC+3 (69/69). 66 of 69 bursts are sells, entered after price rose above its 4–12 h mean; exits trail ~3 pips off the best, and losses run to ~20 pips. Contradicts "milliseconds apart" and "every burst won" (8 of 66 sells lost). No rule frozen. |
| 2026-10-11 | Spec frozen by the user and run once on Axi EURUSD M1 2019-01 → 2026-10 (bursts excluded). **Stage 1 fail:** A −2.01, B −2.05 pips per trade net, 0/8 years each (#429–430). The provider's actual entries with the same exit made +4.0 against +0.57 for random sells in the same weeks. |
| 2026-10-12 | Selector exploration (~120 looks, no rule frozen): the entries are rallies stretched over 2–24 h that have paused, but no bar-based rule reproduces +4.0 even in-sample (best +1.75). Next: more provider data (the statement from 2026-07-11 on, or the provider's history page). |
| 2026-10-12 | Loop resumed at the user's challenge: a dev-set breakdown found two 4/4-year cells. Frozen as #436–437 and traded on development, they lose −1.85 / −2.27. Holdout not spent. The earlier "+1.0 to +1.4 drift" is retracted as a sampling artifact. The provider's surplus appears within minutes of entry, i.e. sub-minute information. |
| 2026-10-12 | Observe-and-test loop, iterations 1–4 (#432–435), all fail on development 2019–22. Observed: D1-downtrend upper-band pullback sells drift +1.0 to +1.4 pips over 4–8 h out of sample, below round-trip cost. The provider's surplus (~3.5 pips) is unexplained. Holdout untouched. |
| 2026-10-12 | Selector loop: 29 indicator families + levels + the clock (none passed the in-sample screen); the provider's grid structure frozen and run once, #431 fail (−2.86 pips × units, 0/8 years). Loop stopped: nothing in EURUSD/gold/S&P bars reproduces it. Next lead needs other FX pairs ingested. |

### #439: the replicator with TP 10 (2026-10-12)

Same model, threshold and stop as #438; target 10 pips at the user's request. Development 2019–22: n = 6,751, **−1.97 pips** net (gross −0.95), t −10.3, wins 67.6%, 0/4 years positive → **fail**. In-sample burst weeks −2.17. The provider's own 69 entries under the same exit: +3.44 net, 81% wins (sells +3.70, buys −2.33; TP 5 gives +2.13 at 93%).
