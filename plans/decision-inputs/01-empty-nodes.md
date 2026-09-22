# Three declared nodes that were never ingested (T91)

Three series are declared in the transmission graph, referenced by five edges, and have zero
observations. Their ETF proxies are sitting in the sibling schema with five years of history.

## Goal

`eq.rut`, `eq.msci_em` and `cmdty.gold` carry observations, so the three edges that depend on
them estimate instead of rendering as `·`.

## What the user sees

Three more rows in the transmission graph with real numbers — including
`credit.hy.oas → eq.rut`, the exact edge the eval says Trade C needed and could not get.

## Data

Of the 15 declared edges, five are empty. Two need `policy.ff.meeting_1`, which is T96's
problem. The other three fail for a much simpler reason:

| Series | Observations | Proxy in `gex.daily_bars` | Bars | Last |
|---|---|---|---|---|
| `eq.rut` | **0** | `IWM` | 1,262 | 2026-09-21 |
| `eq.msci_em` | **0** | `EEM` | 1,262 | 2026-09-21 |
| `cmdty.gold` | **0** | `GLD` | 1,262 | 2026-09-21 |

The three edges they block:

* `credit.hy.oas → eq.rut` (expected −1, credit chain)
* `cmdty.wti → eq.msci_em` (expected +1, dollar chain)
* `ust.10y.real → cmdty.gold` (expected −1, gold chain)

Both of the *other* series in each pair are populated: `credit.hy.oas` 792 obs to 2026-09-17,
`cmdty.wti` 6,012 to 2026-09-15, `ust.10y.real` 5,932 to 2026-09-17. So each edge is one series
away from estimating.

## Design decisions

**Feed them from `gex.daily_bars` rather than adding a fourth external source.** The data is
already captured, already backfilled five years, already updated daily by a job that works, and
already lives in the same Postgres instance. Adding a vendor for a series the desk holds would
be new failure modes for nothing.

**This is a module boundary crossing, and it needs a named seam.** `modules/terminal` currently
knows nothing about `modules/gex`, which is deliberate — the initiative README for `quantdesk/`
argues a shared `core/` should not be designed before three real consumers exist. This is the
first genuine cross-module read, so it goes through a new terminal **adapter** — the same shape
as `adapters/fred.py`, `adapters/cme.py` and the rest — that happens to read a Postgres table
rather than an HTTP endpoint. Terminal keeps its adapter-shaped world; gex exposes nothing new.

**An ETF proxy is not the index, and the series metadata must say so.** IWM is not the Russell
2000: it carries tracking error, an expense ratio, and a distribution that the index does not.
For a correlation percentile that is immaterial; for anyone reading the series as a level it is
not. Record the proxy and the fact of proxying in `series_metadata`, so a later reader does not
have to infer it from the value.

**Point-in-time rule applies unchanged (invariant 10).** These land as ordinary observations
with an `as_of` and an `as_of_basis`; a revision adds a row. A bar corrected by the vendor is a
new row, never an overwrite of the old one.

## Tasks

## T91 · Sonnet · T90

Add a terminal adapter that reads `gex.daily_bars` for a configured symbol → series mapping
(`IWM → eq.rut`, `EEM → eq.msci_em`, `GLD → cmdty.gold`) and emits observations in the same
shape every other adapter produces, including `as_of_basis`. Register it in the `ingest` step so
it runs nightly with the others and writes its own `ingest_batches` row.

Set `series_metadata` for the three, naming the proxy instrument explicitly.

Backfill the full 1,262 bars each, then confirm the three edges estimate.

Depends on T90 only because a sequence that aborts before `edges` will not show the result.

## Verified facts

Measured 2026-09-21:

* The three series have zero rows in `terminal.observations`; their counterparties are all
  populated.
* IWM, EEM and GLD each have exactly 1,262 bars in `gex.daily_bars`, last dated today.
* `terminal.observations` holds 219,107 rows across 75 series, so three more series at ~1,262
  rows each is a rounding error on the table.
* `MIN_EDGE_OBSERVATIONS = 60` and `BETA_WINDOW = 250` (`graph.py`), so 1,262 bars is ample for
  a beta; `CORR_HISTORY_WINDOW = 756` means the percentile history will be shorter than its
  intended three years at first and will fill in over time. Expect `corr_history_n` well below
  756 on these three and do not treat that as a bug.

## Acceptance

* `terminal.observations` carries the three series with ~1,262 rows each.
* All three previously-empty edges return numbers from `terminal_edges`, with
  `sign_conflict` and `significant` populated.
* A second nightly run adds the new day and does not duplicate the history.
* `series_metadata` names the proxy for each.

## Likely first-contact failures

* **Reading `gex.daily_bars` through the terminal's own psycopg facade with the wrong
  `search_path`.** `store/db.py`'s `connect()` pins `search_path` to the terminal schema, so
  `gex.daily_bars` must be schema-qualified or it will not be found.
* **Treating the ETF price as the index level.** Fine for correlation, wrong for anything that
  reads the number as a level. The metadata is what prevents this.
* **Forgetting `as_of_basis`.** Per-row, and per invariant 10 it is the authority — not the
  series' dominant basis. A backfill from stored bars is not the same basis as a live fetch.
* **Expecting `corr_percentile` to be meaningful immediately.** See the verified fact about
  `corr_history_n`.

## Out of scope

Any other missing series, ingesting the real index levels from a paid source, and the two
`policy.ff.meeting_1` edges (T96).

---

## Result — T91

**Done and deployed 2026-09-21.** 1,179 backend tests green (12 added), ruff clean.

`app/modules/terminal/adapters/prices.py` — a fifth source, `prices`, shaped exactly like the
other four and reading `SELECT date, close FROM gex.daily_bars` instead of HTTP. It runs in
the same `ingest` loop, writes its own `ingest_batches` row, and reports its failures the same
way. The three `_pending` entries became a `PRICES` list whose `display_name` and `notes` both
name the proxy instrument.

On the homeserver: **1,262 observations each for `eq.rut`, `eq.msci_em` and `cmdty.gold`**,
2021-09-10 to 2026-09-21, every row `as_of_basis = derived_lag`. A second run inserted 0 and
found 1,262 already present, so the nightly adds a day rather than a copy.

**The graph went from 10 edges estimated to 13.** All three previously-empty edges compute,
with `significant` and `sign_conflict` populated:

| Edge | beta | t | corr | pct | corr_history_n | significant |
|---|---|---|---|---|---|---|
| `credit.hy.oas → eq.rut` | −0.1210 | −10.4 | −0.552 | 74 | 508 | **yes** |
| `ust.10y.real → cmdty.gold` | −0.1196 | −3.5 | −0.215 | 54 | 756 | **yes** |
| `cmdty.wti → eq.msci_em` | 0.0617 | 1.9 | 0.119 | 92 | 756 | no |

`credit.hy.oas → eq.rut` is the edge the eval said Trade C needed and could not get. It is
significant, its sign matches the prior, and it is the strongest of the three. The remaining
two uncomputable edges both want `policy.ff.meeting_1`, which is T96.

**`cmdty.wti → eq.msci_em` is not significant, and that is the answer, not a shortfall.** t
1.9 on 250 observations with r² 0.01: crude does not currently move EM in any way this beta
can distinguish from noise. The 92nd-percentile correlation flag says the relationship is
nonetheless unusually strong *by its own standards*, which is exactly the pair of facts the
graph exists to show side by side.

**This file's warning about a short percentile history was half wrong, and worth correcting.**
It predicted `corr_history_n` well below 756 on all three. Two came back at the full 756,
because 1,262 bars of proxy history is more than three years; the one that did not,
`credit.hy.oas → eq.rut` at 508, is limited by `credit.hy.oas`'s own 792-observation history,
not by the new series at all. Reading a short percentile history as evidence about the new
data would have been backwards.

### Two things found on the way, both fixed here

**Two pending series also named `prices` as their eventual source, with no code.** `eq.sx5e`
and `fx.usdcnh` would have been handed to the new adapter as the symbol `""` the moment it
existed, failing the whole source's batch for series that were never going to load. A
registered series with no `source_code` is now excluded from `fetchable()` and from the map an
adapter is built with, and is still counted in the `no_adapter` tally — the notes on both now
say the prices adapter reads the desk's bars and the desk captures no proxy for them.

**`--source` restated the source list instead of deriving it.** `--source prices` was rejected
by argparse while the same source ran perfectly well under `--source all`. Derived from
`FETCHABLE_SOURCES` now, with a test parametrized over the real set so it fails the day a
sixth source is added rather than the day someone tries to name it.
