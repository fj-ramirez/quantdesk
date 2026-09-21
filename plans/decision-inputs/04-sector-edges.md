# Sector-level transmission edges (T94)

The graph reasons about `eq.spx`, `eq.ndx` and `eq.rut`. Every trade the desk actually makes is
in a sector or industry ETF. The eval's observation, confirmed.

## Goal

The transmission graph covers the level the trades live at, so "rates are hitting duration
proxies" is a measured edge rather than an inference from the index.

## What the user sees

Sector rows in the transmission graph and the daily brief: which sectors are currently coupled
to rates, to credit, to the dollar — and which have broken from their own history.

## Data

Equity nodes in `graph.py` today are exactly four: `eq.spx`, `eq.ndx`, `eq.rut`, `eq.msci_em`.
There is no sector node of any kind.

The sector data is already captured. `gex.daily_bars` carries the **11 Select Sector SPDRs plus
12 industry ETFs** verified live on 2026-09-09 (`scan/groups.py`, `Settings.EXTENDED_SYMBOLS`),
at 1,262 bars each, current to today. Exactly the same situation as T91's three missing nodes,
at larger scale — which is why this depends on that task rather than duplicating its adapter.

The counterparty series are populated and current: `ust.10y.real` 5,932 obs to 2026-09-17,
`credit.hy.oas` 792 to 2026-09-17, `fx.usd.broad`, `cmdty.wti` 6,012 to 2026-09-15.

## Design decisions

**Reuse T91's adapter; do not write a second one.** T91 establishes the seam for reading
`gex.daily_bars` into terminal series. This task is that mapping with more rows in it. If T91's
adapter cannot express this without modification, T91 got the seam wrong and that is the thing
to fix.

**Declare edges deliberately, not combinatorially.** Twenty-three sectors against four
macro drivers is 92 candidate edges, and an edge in this graph is a *theoretical claim* with an
`expected_sign` and a `typical_lag_days` — `graph.py` treats `expected_sign = 0` as a real
value meaning "genuinely regime-dependent", not as a default for "we did not think about it".
Ninety-two edges nobody reasoned about would turn a curated graph into a correlation screen
with opinions attached. Pick the ones with a defensible mechanism: rates → the duration-
sensitive sectors, credit → the credit-sensitive ones, dollar → the exporters and commodity
names, oil → energy.

**Sign conflicts are the output that matters.** `terminal_edges(conflicts_only=True)` is
described by the tool itself as the highest-signal view, and a sector edge running against its
theoretical sign is a far sharper signal than the same thing at index level, because the
mechanism is more specific. Make sure the new edges flow into the brief's conflict section.

**Expect thin percentile history at first, and say so.** `CORR_HISTORY_WINDOW` is 756 trading
days and these series will start with ~1,262 bars, so `corr_history_n` will be short for a
while. `graph.py` already reports it per edge; the acceptance below checks it is honest rather
than absent.

## Tasks

## T94 · Sonnet · T91

Extend T91's symbol → series mapping to the 11 sector SPDRs and the industry ETFs in
`EXTENDED_SYMBOLS`, with series ids in the existing naming scheme (`eq.sector.*` or similar —
match what `graph.py`'s existing ids imply rather than inventing a parallel convention).

Add edge definitions for the defensible driver → sector pairs, each with an `expected_sign` and
a `typical_lag_days`, and each with a one-line rationale in the same style as the existing
declarations in `graph.py`.

Backfill, run `edges`, and report which of the new edges come back as sign conflicts.

## Verified facts

Measured 2026-09-21:

* Four equity nodes exist; none is sector-level.
* The sector and industry ETFs are in `gex.daily_bars` at 1,262 bars each, current to today,
  and were verified live against Cboe on 2026-09-09 per `scan/groups.py`.
* All four intended macro counterparties are populated and current.
* `graph.py` constants that govern the result: `BETA_WINDOW = 250`,
  `CORR_HISTORY_WINDOW = 756`, `MIN_EDGE_OBSERVATIONS = 60`, `SIGNIFICANCE_T = 2.0`,
  `CORR_EXTREME_LOW/HIGH = 10/90`.

## Acceptance

* The new series carry ~1,262 observations each with correct `as_of_basis`.
* Every new edge either estimates or reports why it cannot; none renders as an unexplained `·`.
* `terminal_edges(conflicts_only=True)` returns a usable set rather than everything or nothing —
  if every new edge is a conflict, the expected signs were guessed rather than reasoned.
* `corr_history_n` is reported honestly on the new edges.

## Likely first-contact failures

* **Adding all 92 edges.** See the design decision. The graph's value is that each edge is a
  claim someone made.
* **Inventing a second series-naming convention** alongside the existing `eq.*` / `cmdty.*` /
  `credit.*` scheme.
* **Reading a fresh edge's `corr_percentile` as meaningful** when its history is 200 readings
  rather than 756.
* **Assuming a sector ETF is its sector.** XLE is large-cap integrated energy, not energy; the
  metadata note from T91 applies here with more force, not less.

## Out of scope

Industry-level edges beyond what `EXTENDED_SYMBOLS` already covers, single-name edges, and any
change to how edges are estimated.
