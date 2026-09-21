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
