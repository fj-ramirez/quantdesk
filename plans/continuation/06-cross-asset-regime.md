# 06 · Cross-asset regime strip

## Goal

One row of tiles that says what kind of tape it is for everything at once: volatility term
structure, vol-of-vol, the volatility risk premium, cross-sector correlation, and the
dollar, gold and rates moves. When this strip says "fade-friendly", the per-symbol regime
board is telling the user where the exceptions are. It also tells the user when the whole
board is likely to flip.

## What the user sees

A `RegimeStrip` component rendered at the top of `/regime` and `/scan`. Tiles:

| VIX9D/VIX | VIX/VIX3M | VVIX | VIX 1y pct | SPY VRP | Sector corr 20d | UUP 20d | GLD 20d | TLT 20d |

Each tile: current value, its one-year percentile as a small bar, and a one-word label with a
tooltip explaining the rule. Tiles with no data render "n/a" with the reason.

## Data

- Cboe index histories, keyless CSVs at (unverified, verify first)
  `https://cdn.cboe.com/api/global/us_indices/daily_prices/{VIX,VIX9D,VIX3M,VIX6M,VVIX,SKEW}_History.csv`.
  These are OHLC daily files going back years, so they fit T42's `daily_bars` table under a
  new provider rather than a new table.
- Everything else from T42's bars: SPY for realized vol, the 11 sector ETFs for the
  correlation, UUP, GLD, TLT for the 20-day moves.
- **MOVE index**: no verified free daily source. Omit the tile unless the agent finds one and
  records it; do not substitute without a label. TLT 20-day realized vol may be shown as
  "rates vol (TLT RV, proxy)" if the user wants it, clearly named as a proxy.

## Design decisions

- **A second bars provider, not a second pipeline.** T42's registry routes symbols by group.
  T54 adds `CboeIndexHistoryProvider` for symbols listed in `BAR_PROVIDER_GROUPS` as
  `cboe_index:^VIX,^VIX9D,^VIX3M,^VIX6M,^VVIX,^SKEW`. The bars job and backfill then handle
  them like any other symbol.
- **Definitions**: term-structure ratios are plain close ratios; `contango` when VIX/VIX3M < 1
  and VIX9D/VIX < 1, `backwardation` when both > 1, `mixed` otherwise. VRP = VIX − SPY RV20
  (in vol points, both annualized). Sector correlation = mean of the pairwise 20-day
  correlation of daily log returns across the 11 sector ETFs. Percentiles are over 252 bars.
- **No composite score.** Six tiles with rules are more useful than one number and cannot hide
  a bad input.
- **Labels are rules, not opinions.** `contango` and `backwardation` are facts about the
  curve. The tooltip may say "fade-friendly historically" for contango with low VVIX, and the
  strip must not go further than that.

## Tasks

### T54 · Sonnet · T42, T45
**Cross-asset regime: Cboe index bars provider, metrics, API, strip component**

Paths: `backend/app/providers/cboe_index.py` (the provider; reuses the `httpx` client
injection pattern from `providers/cboe.py`; parses the CSV header block defensively and
raises `ProviderError` on a non-CSV body), `backend/app/config.py` (`BAR_PROVIDER_GROUPS`
default gains the `cboe_index` group), `backend/app/scan/cross_asset.py` (pure:
`term_structure`, `vrp`, `sector_correlation`, `percentile_252`, returning a frozen
`CrossAssetRow`), `backend/app/api/scan.py` (`GET /api/scan/cross-asset`),
`frontend/src/components/regime/RegimeStrip.tsx` (visual spec in [07-ui.md](07-ui.md), built on
T55's shared kit), `api/queries.ts`, `api/types.ts`, MSW handlers and fixtures, tests both
sides, `docs/validation-scan.md`.

The first step is a live request for each of the six CSV URLs. Record in the provider
docstring which exist, their column names, and the earliest date. A missing index is dropped
from the default group with a comment, not guessed at.

Constraints: `providers/cboe.py` (the option chain provider) is not modified. Do not add a
table; index bars go in `daily_bars` with `source="cboe_index"`. Never kill processes by
image name.

Acceptance: `uv run python -m app.bars_backfill --years 3 --symbols ^VIX,^VIX3M` populates
rows via the new provider; a fixture CSV with the recorded header parses and a fixture with
an HTML body raises `ProviderError`; `term_structure` on hand-built closes yields the three
labels; sector correlation on a fixture where all sectors share one return series is exactly
1.0 and on orthogonal series is approximately 0; `percentile_252` with fewer than 60 bars
returns `None`; the strip renders "n/a" tiles for missing inputs; both test suites and both
linters pass; supervisor sees live tiles.

## Verified facts (2026-09-09)

- The option-chain Cboe provider lives at `backend/app/providers/cboe.py` and is the pattern
  for HTTP, client injection and `ProviderError` handling; index CSVs are a different Cboe
  host path.

**All six index CSVs verified live by the supervisor at 21:00 ET on 2026-09-09.** Every URL in
*Data* above exists and returns `200 text/csv`. No preamble: the header is line 1. Dates are
`MM/DD/YYYY`, as the failure list predicted. All six carried **today's** row (`09/09/2026`)
already at 21:00 ET, so the 17:30 ET bars job will get same-day data.

| Symbol | Columns | Earliest date | Bytes |
|---|---|---|---|
| `^VIX` | `DATE,OPEN,HIGH,LOW,CLOSE` | 01/02/1990 | 472 KB |
| `^VIX9D` | `DATE,OPEN,HIGH,LOW,CLOSE` | 01/04/2011 | 200 KB |
| `^VIX3M` | `DATE,OPEN,HIGH,LOW,CLOSE` | 09/18/2009 | 218 KB |
| `^VIX6M` | `DATE,OPEN,HIGH,LOW,CLOSE` | 01/02/2008 | 240 KB |
| `^VVIX` | **`DATE,VVIX`** — close only | 03/06/2006 | 109 KB |
| `^SKEW` | **`DATE,SKEW`** — close only | 01/02/1990 | 203 KB |

**The two close-only schemas are the one thing this task has to decide.** `daily_bars` declares
`open`, `high`, `low` and `close` all `nullable=False` (`app/models/db.py`), so VVIX and SKEW as
published cannot be stored as-is. Three options, and this is a judgment call to make explicitly
rather than in passing:

1. Store `open = high = low = close`. Cheapest, no migration, and it matches **Cboe's own
   convention for its early history** — the 1990 `^VIX` rows are literally
   `17.240000,17.240000,17.240000,17.240000`, so the file format already carries closes dressed
   as OHLC. Cost: a consumer cannot distinguish "no intraday range published" from "genuinely
   flat day". `source="cboe_index"` plus the provider docstring is the mitigation.
2. Widen the four columns to nullable. Honest, but it is a migration plus a nullability
   widening across everything that reads bars — exactly the class of change the supervision
   report caught hiding nine type errors, and T54 is not the task to spend that on.
3. Keep VVIX and SKEW out of `daily_bars`. Contradicts the plan's own "no new table" constraint.

Recommendation: **(1), documented in the provider docstring and in `docs/validation-scan.md`.**
The strip needs closes only. Do not silently do (1) without the note.

Two smaller observations:

- `^VVIX`'s early history is sparse — `03/06/2006` is followed by `03/15/2006`. Anything
  computing a 252-bar percentile over the full file must count bars, not calendar days
  (`percentile_252` returning `None` under 60 bars already covers the shape of this).
- The symbol names in `BAR_PROVIDER_GROUPS` carry the `^` prefix (`^VIX`) while the URLs do not
  (`VIX_History.csv`). Map one to the other inside the provider.
- **`^VIX` changes hands.** T42 already fetches it from Yahoo; listing it in the `cboe_index`
  group moves it to Cboe, because `BarProviderRegistry` resolves a group entry ahead of the
  default (`providers/bars.py`, `provider_name_for`) — so there is no ambiguity to resolve, but
  there *is* a switch to make deliberately. Do make it: the Cboe file is OHLC back to 1990 and
  has none of Yahoo's partial-last-bar problem (`00-foundation-daily-bars.md`). Existing Yahoo
  `^VIX` rows carry `source="yahoo"` and the unique constraint is `(symbol, date)`, so a
  re-backfill updates them in place and the `source` column records the changeover — check what
  `upsert_bars` does to `source` on conflict before assuming it rewrites it.
- T42's registry design (see `00-foundation-daily-bars.md`) is what makes this a provider
  addition rather than a pipeline; if T42 shipped without group routing, add it here as the
  first step and note the interface change in `TASKS.md`.

## Likely first-contact failures

- Cboe CSVs with a multi-line preamble before the header or with dates in `MM/DD/YYYY`.
  Parse by locating the header row, not by fixed offset.
- VIX9D or VIX6M files missing or renamed. Drop with a comment.
- Sector correlation window straddling a missing bar for one ETF, silently shrinking the
  sample. Align on the intersection of dates and report the effective sample size.

## Out of scope

Futures term structure (VX contracts), MOVE, any intraday value, a composite regime score.
