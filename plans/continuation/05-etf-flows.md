# 05 · ETF flows

## Goal

Show actual money entering and leaving sector and industry ETFs, not a price-and-volume proxy.
A creation or redemption changes the fund's shares outstanding; that daily change times NAV
is the flow. This is the honest version of a "money flow" page.

## What the user sees

A `/flows` route. A horizontal bar chart of net flow over 5 and 20 days as a percentage of
fund assets, one bar per fund, sorted; a sparkline of cumulative flow over 60 days per fund;
a banner naming the data source and its last successful update per fund family. Funds with no
usable source are listed at the bottom as "no flow data", never drawn at zero.

## Data

Daily shares outstanding published by the issuers. **This is the fragile part of the whole
initiative and the supervisor has not verified any of it.** Known starting points, each of
which may have changed:

- State Street (Select Sector SPDRs, SPY): fund pages on `ssga.com` expose an XLSX or CSV
  download per fund that includes shares outstanding; there is also a daily "all funds" file.
- iShares (IWM, TLT, HYG, EEM, EFA, XLRE-equivalents): fund pages accept a `fileType=csv`
  download whose header block carries shares outstanding and NAV.
- VanEck (GDX, SMH), Invesco (QQQ, RSP), ARK (ARKK), USCF (USO, UNG): unknown.

NAV per share for the flow calculation: the issuer file where present, otherwise the day's
close from T42's bars, with the `source` column saying which.

## Design decisions

- **Survey before build.** The first deliverable of T52 is `docs/etf-flows-sources.md`: for
  each fund family in the universe, the exact URL tried, whether it returned shares
  outstanding, whether history or only the latest value is available, and how brittle the
  format looked. Only families with a working source get a fetcher. This is the "permit
  honest failure" rule from `context/workflow.md` applied up front.
- **Latest-only sources mean history starts the day the job starts.** Most issuer pages give
  today's value only. The table therefore accumulates from first run; the page shows "history
  since <date>" and computes 20-day flow only when 20 days exist. No backfill is promised.
- **Flow definition**: `flow_t = (SO_t − SO_{t−1}) × NAV_t`; `flow_pct_t = flow_t /
  (SO_{t−1} × NAV_t)`. Aggregates are sums over the window; percent aggregates divide by
  the window-start AUM.
- **Never substitute a proxy silently.** Chaikin money flow and on-balance volume are not
  flows. If the user wants them later, they belong in the trend scorer as labelled columns.
- **Separate table, separate job**, 18:30 ET, after bars. Issuer pages update after the close
  and a fetch too early yields yesterday's value under today's date; the job must compare the
  file's own as-of date and skip when it is not today.

## Tasks

### T52 · Sonnet · T42
**Shares-outstanding ingest**

Paths: `docs/etf-flows-sources.md` (the survey, written first), `backend/app/providers/
etf_flows.py` (one small fetcher class per fund family behind a `SharesOutstandingProvider`
ABC; each returns `(symbol, as_of_date, shares_outstanding, nav | None)`),
`backend/app/models/db.py` (table `etf_shares_outstanding`: `symbol, date, shares, nav,
source`, unique `(symbol, date)`), one Alembic migration, `backend/app/storage/
flows_repository.py`, `backend/app/jobs/flows.py` (per-symbol try/except; skips when the
file's as-of date is not today; logs a per-family summary), `backend/app/jobs/scheduler.py`
(register 18:30 ET mon–fri, additive), `backend/app/scan/flows.py` (pure: `compute_flows(
so_frame, nav_frame, windows) -> DataFrame`), `backend/app/api/scan.py` (`GET
/api/scan/flows?window=5|20|60`), `backend/app/api/health.py` (additive `flows` block),
tests with recorded fixture files per family.

Constraints: no scraping of pages that require a login or set anti-bot cookies; if a family
needs that, mark it unsupported in the survey and move on. Every fetcher is tested against a
recorded fixture, never the live site. Never kill processes by image name.

Acceptance: the survey document exists and names at least the SPDR and iShares outcomes; for
every family marked supported, `uv run python -m app.flows_fetch --symbols XLK,IWM` inserts a
row with today's as-of date and a second run inserts nothing; a fixture where the as-of date
is yesterday causes a skip, not a row; `compute_flows` on a hand-built fixture returns the
hand-computed percentages; `uv run pytest` and `ruff check .` pass.

### T53 · Sonnet · T52, T55
**Flows page**

> **UI spec superseded (2026-09-09):** the page spec and task block for this view now live in
> [07-ui.md](07-ui.md); the paragraph below is kept for history. Dispatch from 07-ui.md.

Paths: `frontend/src/pages/Flows.tsx`, `frontend/src/components/flows/FlowBars.tsx`
(echarts), `frontend/src/components/flows/FlowSparkline.tsx`, `api/queries.ts`,
`api/types.ts`, `state/urlState.ts` (`window`), `App.tsx`, `AppShell.tsx`, MSW handlers and
fixtures, tests.

Acceptance: the source banner shows the per-family last-update date from the health block;
funds without data appear in the "no flow data" list and never as a zero bar; a window
larger than the available history renders "history since <date>" instead of a number; `npm
test` and `npm run lint` pass; supervisor sees at least one live family rendering.

## Verified facts (2026-09-09)

- Nothing about the issuer endpoints is verified. Everything under *Data* is a starting point
  for the survey.
- The health router (`backend/app/api/health.py`) is the established place for freshness
  reporting; T42 adds a `bars` block there, so follow its shape.

## Likely first-contact failures

- Issuer files served as XLSX; `openpyxl` is not a dependency. Prefer CSV endpoints; if only
  XLSX exists, add `openpyxl` and say so in the survey.
- Shares outstanding reported in thousands or millions depending on issuer. Assert the
  magnitude against the fund's known AUM order on first fetch and record the unit per family.
- An issuer changing its download URL silently. The health block must show staleness per
  family so the user sees the break within a day.

## Out of scope

Mutual fund flows, EPFR-style aggregate flows, any paid feed, backfilling history that the
issuer does not publish.
