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
`frontend/src/components/regime/RegimeStrip.tsx`, `api/queries.ts`, `api/types.ts`, MSW
handlers and fixtures, tests both sides, `docs/validation-scan.md`.

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
  host path and are **not verified**.
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
