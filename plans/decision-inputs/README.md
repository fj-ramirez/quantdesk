# decision-inputs — what the trade path reads (T90–T96)

Opened 2026-09-21, out of [docs/market-research-eval.md](../../docs/market-research-eval.md):
the market-research agent's own answer to "which sources would polish your choices?" Its
Tier 0-3 list is the origin of this initiative, but **the list was verified against the
database and the code before any of it was planned, and it did not survive intact.** What
follows is the verified version. Where this document and the eval disagree, this one is right
and the eval's reasoning is recorded so the same wrong turn is not taken twice.

## What the verification changed

**Three of the eval's headline items describe things that already exist**, and are dropped:

* `RSP` equal-weight breadth — already in `SCAN_UNIVERSE`, and `scan/rotation.py`'s
  `sector_breadth` already computes the RSP/SPY reading. `RSP` has 1,262 daily bars with
  volume, identical coverage to SPY.
* Index-level realized-vs-implied vol — `indicators.realized_vol` exists and
  `scan/cross_asset.py` already derives a variance risk premium from it against VIX. Per-*name*
  RV/IV against the option chain is genuinely new and is not planned here.
* The `gex.intraday_bars` "outage" — the table holds two days, 2026-09-11 and 2026-09-21, and
  **is writing again today** (540 rows, 6 symbols, last write 19:55Z). It was dead for ten days,
  which matches the rename incident documented in `compose.yaml`, not a `gex-capture` bug.

**Two of its factual claims are wrong:**

* *The correlation percentiles are read backwards.* `graph.py:379` computes the percentile of
  the **signed** rolling correlation: `100 × (history ≤ corr) / len(history)`. A low percentile
  means the correlation is at the *most negative* end of its history — inverse coupling
  unusually **strong**. The eval reads `ust.10y.nominal → eq.spx` at the 0.66th percentile
  (corr −0.3042) as "transmission at its weakest in 250 days" and turns that into a risk
  warning. It is the strongest inverse coupling in the stored history, and the warning points
  the wrong way. Same inversion on `ust.10y.real → eq.ndx` (corr −0.2712, 4.63rd percentile).
  The third row, `fx.usd.broad → cmdty.wti` — sign conflict, 87.57 — is read correctly.
* *`etf_shares_outstanding` is not collapsing.* Actual coverage: 9/09 **26**, 9/10 **27**,
  9/11 1, 9/17 17, 9/18 **26**, 9/19-9/20 weekend, 9/21 pending (the flows job runs 18:30 ET).
  101 rows across 27 symbols, current to today — not "82 rows, 27 → 17 → 9 → 1".

**And the verification found something the eval missed entirely**, which is now T90 and is the
reason this initiative is P0 rather than a roadmap: the nightly terminal sequence aborts at the
`policy` step and never reaches `edges`.

## What survived, and what is confirmed

| Claim | Status |
|---|---|
| `daily_bars.volume` populated and unread by any signal | **Confirmed.** 149,560 / 157,152 rows, 120 symbols. The 5 without volume are `^SKEW`, `^VIX3M`, `^VIX6M`, `^VIX9D`, `^VVIX` — correctly null |
| IWM ran hot into Friday | **Confirmed.** Seven consecutive sessions 9/10-9/18 above its 60-day average (1.29, 1.26, 1.24, 1.22, 1.34, 1.07, **1.51**). Today 0.91 |
| No market-implied Fed path | **Confirmed, and worse than stated.** Zero `policy.ff.*` observations; `cli.py:666` makes the CME settlement file a *required positional*, and `cli.py:229` records that automated access is prohibited by CME's terms |
| No event calendar | **Confirmed.** `terminal.releases` has 0 rows and `tables.py:123` documents it as "Empty, and knowingly so." The FOMC calendar is a JSON file, not in the database |
| No sector-level graph nodes | **Confirmed.** Equity nodes are exactly `eq.spx`, `eq.ndx`, `eq.rut`, `eq.msci_em` |
| `credit.hy.oas → eq.rut` declared and empty | **Confirmed — and the cause is not what the eval assumed.** `eq.rut` has **zero observations**. So do `eq.msci_em` and `cmdty.gold`. Three declared nodes are simply never ingested, while IWM, EEM and GLD each carry 1,262 daily bars in `gex.daily_bars`, current to today |
| "These eight are one trade" is an assertion | **Confirmed** by the eval's own admission, and it needs no new data source |

## Dependency graph and dispatch order

```
T90  nightly abort ......... P0, blocks nothing but spoils everything
  ├── T91  empty nodes ..... lights up 3 declared edges from data already held
  │     └── T94  sector edges
  └── T96  calendar + policy path (source decision)

T92  relative volume ....... independent
T93  factor cap ............ independent, highest value
T95  crude term structure .. independent
```

| File | Task |
|---|---|
| [00-nightly-abort.md](00-nightly-abort.md) | T90 — the sequence dies before `edges` |
| [01-empty-nodes.md](01-empty-nodes.md) | T91 — feed `eq.rut`, `eq.msci_em`, `cmdty.gold` from bars already held |
| [02-relative-volume.md](02-relative-volume.md) | T92 — the cheapest signal upgrade on the list |
| [03-factor-cap.md](03-factor-cap.md) | T93 — stop emitting seventeen correlated shorts |
| [04-sector-edges.md](04-sector-edges.md) | T94 — transmission at the level the trades live at |
| [05-crude-term-structure.md](05-crude-term-structure.md) | T95 — squeeze versus froth |
| [06-calendar-and-policy-path.md](06-calendar-and-policy-path.md) | T96 — a source decision, not a wiring job |

## A standing instruction for the trade workflow

The eval's best line is one it did not follow: **read `corr_percentile`, not the sign**
(`graph.py:222`). Add to that: read it in the direction the code computes it. An extreme
percentile means the relationship is at the edge of its own range, and `graph.py:70` is
deliberately agnostic about which — *"either unusually reliable or has broken."* Any workflow
step that turns a percentile into a trading conclusion has to say which of the two it is
claiming, and why.

## Out of scope

Per-name RV/IV from the option chain; short interest and days-to-cover; ADV and spread
liquidity; the cross-series plausibility layer (Tier 3). All are reasonable, none is planned
here, and each needs its own measurement before it earns a task.
