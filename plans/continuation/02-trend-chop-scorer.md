# 02 · Trend versus chop scorer

## Goal

A per-symbol, cross-sectional view of *whether an asset is currently trending or ranging*, from
several independent measures shown side by side, plus a realized-versus-implied volatility
ratio where an option chain exists. It complements the breakout ledger: the ledger says what
happened to past breakouts, the scorer says what the current tape looks like.

## What the user sees

The `/scan` page gains a "Trend" view: the same symbol table with component columns and one
composite percentile, sortable by any column. A detail panel shows the components' 126-bar
history as sparklines and, for optioned symbols, IV and RV on one axis.

| Symbol | ADX14 | ER20 | CHOP14 | VR(5) z | RV20 | IV30 | IV/RV | Trend pct |

## Data

Bars from T42. For `IV30`, the ATM ~30-day IV already computed by `app/gex/report.py` for the
five option underlyings (and for the extended set once T47 lands). No IV is imputed for
symbols without a chain; the cell is empty.

## Design decisions

- **Components, then a rank composite, never thresholds.** Absolute cut-offs ("ADX > 25 means
  trend") are folklore. Each component is converted to a cross-sectional percentile across the
  universe on the same date, and the composite is the mean of those percentiles. The page
  always shows the components next to the composite so it is never a black box.
- **Variance ratio instead of Hurst.** A Hurst exponent on 126 daily bars is too noisy to rank
  by. Lo–MacKinlay VR(q) with the heteroskedasticity-robust z-statistic is well defined and
  testable. Report the z with its sign: positive means momentum, negative means mean reversion.
- **Definitions** (implement exactly, then record deviations in `docs/validation-scan.md`):
  ADX Wilder 14; Kaufman efficiency ratio `|C_t − C_{t−n}| / Σ|ΔC|` with n = 20; Choppiness
  `100·log10(Σ_n TR / (max H − min L)) / log10(n)` with n = 14; VR(q=5) on log returns over
  126 bars; RV20 = annualized std of log returns over 20 bars, `sqrt(252)`.
- **IV/RV is a hint, not a component.** It enters the table but not the composite, because it
  exists for a minority of the universe.

## Tasks

### T45 · Opus · T42, T43
**Trend/chop scorer: pure module and API**

Paths: `backend/app/scan/indicators.py` (extend: `adx`, `efficiency_ratio`, `choppiness`,
`realized_vol`, `variance_ratio` returning `(vr, z_robust)`), `backend/app/scan/trend.py`
(`score_symbol(bars, iv30: float | None) -> TrendComponents`,
`rank_universe(components_by_symbol) -> list[TrendRow]` with percentiles and composite),
`backend/app/api/scan.py` (add `GET /api/scan/trend` and `GET /api/scan/trend/{symbol}` with
the component history), the IV lookup in the API layer only (read the latest stored snapshot
the way `api/report.py` does; the pure module receives the number), tests,
`docs/validation-scan.md`.

The agent must state, in the module docstring, how it validated each indicator: against
hand-computed values on a 30-bar fixture, and against the properties below. Do not add an
indicator library as a dependency.

Acceptance: a straight-line series has `ER = 1.0`, minimal CHOP and a large positive VR z;
i.i.d. Gaussian noise (seeded) has VR ≈ 1 and |z| < 2 in at least 95 of 100 seeds; ADX on the
fixture matches a hand-computed Wilder value to 1e-6; percentiles across a 3-symbol fixture
are exactly {0, 0.5, 1}; `GET /api/scan/trend` returns `iv30=None` for a symbol without a
chain and a number for SPY; `uv run pytest` and `ruff check .` pass.

### T46 · folded into T44
**Scan page: trend view**

> **UI spec superseded (2026-09-09):** the page spec and task block for this view now live in
> [07-ui.md](07-ui.md); the paragraph below is kept for history. Dispatch from 07-ui.md.

Paths: `frontend/src/pages/Scan.tsx` (view toggle in URL state, `?view=trend`),
`frontend/src/components/scan/TrendTable.tsx`, `frontend/src/components/scan/Sparkline.tsx`
(echarts), `api/queries.ts`, `api/types.ts`, MSW handlers and fixtures, tests.

Acceptance: sortable columns; empty `IV30` cells render as a dash, never `0`; deep link
`/scan?view=trend` works; `npm test` and `npm run lint` pass; the supervisor sees live rows.

## Verified facts (2026-09-09)

- `app/gex/report.py` already computes an ATM ~30-day IV (T39) and gates regime labels on
  history; reuse the function, do not reimplement the interpolation.
- `scipy` is available for the normal CDF in the VR z-statistic.

## Likely first-contact failures

- ADX warm-up: Wilder smoothing needs about 2n bars before values are meaningful. Return
  `None` for the first 2n bars, never a partially-smoothed number.
- Illiquid symbols with repeated identical closes blowing up CHOP through
  `max H − min L = 0`. Guard and return `None`.
- Percentiles with ties. Use average rank and say so.

## Out of scope

Signals, alerts, any weighting of the composite other than equal.
