# 04 · Sector rotation

## Goal

Show where relative strength is moving between sectors and industries against a benchmark,
so the user can see which groups are being bought before the continuation shows up in the
index. This is the "money flow between sectors" page, built from prices. Real fund flows are
a separate tool (05) because they need a different, less reliable data source.

## What the user sees

A `/rotation` route. Left: a relative-rotation scatter, RS-ratio on x and RS-momentum on y,
four shaded quadrants (leading, weakening, lagging, improving), each symbol drawn as a 10-week
trail ending in a labelled dot. Right: rank tables of relative return versus the benchmark at
1, 4 and 13 weeks, plus a small breadth block. Toggles in URL state: group (`sectors`,
`industries`, `assets`), benchmark (`SPY` default, `RSP` optional), trail length.

## Data

Weekly closes resampled from T42's daily bars (`W-FRI`, last close of the week). Groups are
fixed lists in code drawn from `SCAN_UNIVERSE`:

- sectors: XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC
- industries: SMH, XBI, KRE, XOP, ITB, XHB, XRT, IGV, ARKK, JETS, GDX
- assets: SPY, QQQ, IWM, GLD, SLV, USO, TLT, HYG, UUP, EEM, EFA, FXI

## Design decisions

- **JdK RS-Ratio and RS-Momentum are proprietary.** Implement the common open approximation
  and name it honestly (`rs_ratio_approx`, `rs_momentum_approx`), never claiming parity:
  `rs = 100 · P / B`; `rs_ratio = 100 + z(rs, w)` where `z` is the rolling z-score over `w`
  weeks (default 14); `rs_momentum = 100 + z(rs_ratio_t − rs_ratio_{t−1}, w)`. Record the
  formula in `docs/validation-scan.md` and in the page's info tooltip.
- **Weekly, not daily.** Daily RRG trails are noise. Weekly with a 10-week trail is the
  standard reading and needs only 24 weeks of bars.
- **Breadth is coarse and says so.** Constituent-level breadth (percent of S&P 500 stocks
  above the 50-day average) needs 500 symbols of bars and is out of budget. What the app can
  compute honestly: RSP/SPY ratio and its 20-day change (equal-weight versus cap-weight
  leadership), and the count of the 11 sector ETFs above their own 20- and 50-day averages.
  Label the block "sector-level breadth".
- **Relative return** = `(P_t / P_{t−n}) / (B_t / B_{t−n}) − 1` over n ∈ {5, 20, 65} bars.

## Tasks

### T50 · Opus · T42
**Rotation math and API**

Paths: `backend/app/scan/rotation.py` (pure: `weekly_closes(daily) -> DataFrame`,
`rrg_approx(prices, benchmark, w) -> DataFrame` with columns `rs_ratio, rs_momentum` per
symbol per week, `relative_returns(prices, benchmark, windows)`, `sector_breadth(prices)`),
`backend/app/scan/groups.py` (the three fixed lists; T49 and T53 may import them),
`backend/app/api/scan.py` (add `GET /api/scan/rotation?group=&benchmark=&weeks=`), tests,
`docs/validation-scan.md`.

Acceptance: a symbol whose prices are a constant multiple of the benchmark sits at exactly
(100, 100) for every week after warm-up; a symbol with linearly rising `rs` lands in the
leading quadrant with positive momentum then drifts toward weakening as the z-score saturates,
and the test asserts that sequence; relative returns on a hand-built 3-symbol fixture match to
1e-9; breadth counts on a fixture where exactly 4 of 11 sectors are above their 20-day
average returns 4; the API rejects an unknown group with 422; `uv run pytest` and `ruff check
.` pass.

### T51 · Sonnet · T50, T44
**Rotation page**

Paths: `frontend/src/pages/Rotation.tsx`, `frontend/src/components/rotation/RrgChart.tsx`
(echarts scatter with `lines` series for trails and `markArea` quadrants; no new dependency),
`frontend/src/components/rotation/RankTable.tsx`, `frontend/src/components/rotation/Breadth.tsx`,
`api/queries.ts`, `api/types.ts`, `state/urlState.ts` (`group`, `benchmark`, `weeks`),
`App.tsx`, `AppShell.tsx`, MSW handlers and fixtures, tests.

Acceptance: quadrants render in both themes with the trail's last point emphasised; hovering
a dot shows the symbol, both coordinates and the 4-week relative return; the rank table sorts
by any window; deep link `/rotation?group=industries&benchmark=RSP` drives the query; the
info tooltip states the approximation; `npm test` and `npm run lint` pass; supervisor sees
live trails.

## Verified facts (2026-09-09)

- `echarts` 6.x is installed and `echarts-for-react` wraps it; the existing `GexByStrike`
  chart in `frontend/src/components/charts/` shows the theme-aware option pattern to copy.
- The dataviz palette rules for this repo live in the `dataviz` skill; categorical colour for
  up to 12 symbols needs care, so prefer labelling dots over relying on colour alone.

## Likely first-contact failures

- Weekly resampling across a holiday-shortened week producing a Thursday close labelled
  Friday; fine for the math, but the tooltip date must be the actual last bar date.
- The z-score window with fewer than `w` weeks after a new symbol is added to the universe;
  return `None` rows rather than a partial-window number.
- Twelve trails on one chart become unreadable; default to showing labels and let the user
  toggle a symbol subset in the legend.

## Out of scope

Constituent-level breadth, sector weights, any factor decomposition.
