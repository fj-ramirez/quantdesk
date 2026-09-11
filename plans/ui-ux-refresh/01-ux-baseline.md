# T62 — UX baseline and interaction specification

Status: **done**, 2026-09-10. Read-only inspection; no production code touched.

Wireframes (approval-gated per plan): https://claude.ai/code/artifact/886d13f2-12f5-468e-afd6-636c13eabb44

## Method note — screenshots substituted with code inspection

The plan calls for capturing each route at 1440/1024/768/390px in both themes. No
browser-automation tool (Playwright, Puppeteer, a Chrome DevTools MCP) is installed or
configured in this environment, so literal pixel screenshots were not possible. This baseline
substitutes a static-code inventory (routes, components, inline styles, ARIA/keyboard paths,
URL state) plus drawn low-fidelity wireframes for the two size classes (desktop ~1440px, narrow
390px) built with the design-canvas tool. If a screenshot-capable tool becomes available later,
re-run a literal visual pass before or during T68 (visual QA) rather than trusting this
substitute for pixel-level regressions.

## Navigation mapping (current → target)

| Current nav link | Target group | New name |
|---|---|---|
| Overview | Today | Overview |
| Decisions | Today | Opportunities |
| Dashboard | Analyze | GEX Explorer |
| Scan, Regime | Analyze | Regime · Scan |
| Rotation, Flows | Analyze | Rotation · Flows |
| Report, History | Review | Report · History |
| Settings | (ungrouped, bottom) | Settings |

Route paths (`/`, `/dashboard`, `/report`, `/history`, `/scan`, `/regime`, `/rotation`,
`/flows`, `/decisions`, `/settings`) do not change — only the rail's grouping and labels do.

## Route inventory

| Route | File | Job | Key control | Empty/error/loading | Keyboard gaps | Inline-style clusters | URL params | Duplicated controls |
|---|---|---|---|---|---|---|---|---|
| `/` `/overview` | `Overview.tsx` | Cross-market morning brief: where is continuation, what's open now | none (read-only; links out) | Loading: `.scan-page__loading` text per block. Error: `ErrorState` per block, independent per query. Empty: `EmptyState` distinguishing "no data yet" vs "not enough resolved events" | All controls are router `<a>` links via `OverviewBlock`'s `to` — keyboard-native | 0 direct | none | none |
| `/dashboard` | `Dashboard.tsx` | Single-symbol GEX state: levels, GEX-by-strike, gamma profile | expiry filter (topbar) + `[`/`]` symbol-cycle shortcut | Loading `aria-live` text; `ErrorState`; no explicit "no snapshot yet" case here (that's Report's) | `[`/`]` shortcut well-guarded (ignores form focus/modifiers) | 0 in page; `KeyLevels.tsx` has 9 clusters (inline background/color/padding) | `symbol, filter, snapshot` | none — symbol/filter/snapshot live only in TopBar |
| `/report` | `Report.tsx` | Full options-intelligence report/playbook for one symbol | symbol/expiry/CFD-spot selects **in the page body** | Loading `aria-live`; `NoDataYet` w/ Capture-now (T37 contract); error distinguishes 404-no-snapshot from real errors | Collapsible "View full report" uses `aria-expanded` correctly | **82 clusters** — heaviest page; `Card`, `LevelChip(s)`, `ScreeningNotice`, `CandidateTable`, `PlaybookCard`, `FullReportPanel`, `NoDataYet`, `ReportBody` all hand-styled inline | `symbol, filter, cfd` | **Yes** — own symbol/expiry `<select>`s duplicate TopBar's `AssetSelector`/`ExpiryFilterSelect`; CFD-spot input is report-only |
| `/history` | `History.tsx` | Level-history over time for one symbol (stub — table/chart is TODO T15) | none yet | Loading text; `ErrorState`; no empty-state distinction beyond a row-count sentence | nothing interactive to reach | 0 | `symbol, filter` | none |
| `/scan` | `Scan.tsx` | Which symbols just broke range, and which are trending/chopping | View toggle (Breakouts/Trend) + N/k/Lookback choice-rows | Loading text names *why* Trend is slow (3.5–5s); `ErrorState`; `EmptyState` per view | Toggle/choice-row buttons use `aria-pressed`; row→detail via `ScanTable`'s `tabIndex=0` + Enter/Space | 0 | `view, n, k, lookback, sort, dir` | none |
| `/regime` | `Regime.tsx` | Dealer-positioning regime per symbol right now | Filter toolbar (ALL/0DTE/EX-0DTE) | Same error/pending/empty triad | Filter buttons `aria-pressed`; table inherits ScanTable | 0 | `filter, sort, dir` | none (TopBar hides its own filter on scan-family routes) |
| `/rotation` | `Rotation.tsx` | Sector/industry/asset relative rotation | Group/Benchmark/Weeks choice-rows | Error/pending/empty triad; generic "no bars stored" empty message | Buttons `aria-pressed`; `<details>` "About this chart" is native | 0 | `group, benchmark, weeks, sort, dir` | none |
| `/flows` | `Flows.tsx` | ETF creation/redemption flow per fund | Window toggle (5/20/60d) | Error/pending/empty triad **plus** a `FlowsBanner` for source lag and a `no_flow_data` per-symbol reason list — a richer 4th state | Window buttons `aria-pressed`; table inherits ScanTable | 0 | `window, sort, dir` | none |
| `/decisions` | `Decisions.tsx` | Ranked trade-idea opportunities, No-Trade reasons, track record | Filter + Min-grade choice-rows | Error/pending; empty distinguishes "no chain captured" vs "below threshold/no-trade reason" | Buttons `aria-pressed`; table inherits ScanTable; `OpportunityDetail` has a close button but **no Escape handler, no focus trap, no focus-return** | 0 | `filter, min_score, sort, dir` | none |
| `/settings` | `Settings.tsx` | Confirm API base URL, toggle theme | ThemeToggle | none — static content | Everything native | 0 | none | none |

## Shell inventory

- Nav order today (`AppShell.tsx`): Overview, Dashboard, Report, History, Scan, Regime,
  Rotation, Flows, Decisions, Settings — 10 flat peers, no grouping.
- Root shell: `frontend/src/index.css` — `#root { width: 1126px; max-width:100%;
  text-align:center; border-inline:1px solid var(--border); }`, confirmed live. `text-align:
  center` is reset per-page (`.app-shell`, `.dashboard`, etc.) — every new page has had to
  remember to re-reset it; T63's shell should set this once at the root instead.
- Narrow-viewport handling: none in the shell itself today. `.navbar` only `flex-wrap`s; no
  hamburger/collapse. Several page grids drop to one column under 900px, but the
  shell/topbar/nav do not adapt structurally.
- `TopBar.tsx` is already route-aware: a `SCAN_FAMILY_PATHS` set swaps in a compact toolbar
  (BarsFreshness + ThemeToggle) for `/`, `/scan`, `/regime`, `/rotation`, `/flows`, `/overview`,
  `/decisions`; every other route gets AssetSelector + expiry + snapshot + freshness badge. T63's
  ContextBar should absorb this existing split, not invent a new one.
- `AssetSelector` already has Escape-to-close, outside-click-to-close, correct
  `aria-haspopup/expanded/labelledby`, `role="listbox"`/`"option"` — reuse, don't rebuild.

## Cross-cutting notes for T63/T64

- Only `Report.tsx` (82 clusters) and `KeyLevels.tsx` (9 clusters) carry real inline-style debt.
  Every scan-family page (Scan, Regime, Rotation, Flows, Decisions, Overview) already uses a
  `className`-only, token-driven vocabulary (`.scan-*` in `index.css`) — T64's primitives should
  generalize *that* vocabulary rather than invent a third one; T67 retires `Report.tsx`'s
  bespoke `Card`/`LevelChip`/etc. in favor of it.
- No detail/drawer view anywhere in the app currently manages focus (checked
  `OpportunityDetail`, `BreakoutDetail`, `TrendDetail`) — T64's "drawer focus returns to its
  trigger" acceptance criterion is new work, not a preservation task.
- `ScanTable` (shared by Scan/Regime/Flows) already has correct `aria-sort`, null-sorts-last,
  keyboard row activation — a strong base to reuse.
- Route-level control duplication is narrow: only `/report`'s inline symbol/expiry selects
  genuinely duplicate the shell's controls. T63's "remove duplicated page-body selectors" work
  is essentially a `Report.tsx` fix, not app-wide.

## Retained URL state (must survive T63–T67 unchanged)

`/dashboard`: `symbol, filter, snapshot` · `/report`: `symbol, filter, cfd` · `/history`:
`symbol, filter` · `/scan`: `view, n, k, lookback, sort, dir` · `/regime`: `filter, sort, dir` ·
`/rotation`: `group, benchmark, weeks, sort, dir` · `/flows`: `window, sort, dir` ·
`/decisions`: `filter, min_score, sort, dir`.

## Acceptance

- [x] Each current route maps to a new navigation group and primary job (table above).
- [x] Retained URL state is documented (section above).
- [x] Wireframes approved by the user, 2026-09-10, as drawn — no changes requested.
