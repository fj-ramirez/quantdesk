# UI/UX refresh — a calmer market-analysis workbench

**Status:** proposed 2026-09-10; **complete** (T62–T69 shipped in `9a410a9`) — see the *Result* section.

**Scope:** frontend presentation and interaction only. No trading, order routing, new market-data source, or change to GEX/decision calculations.

## Problem and outcome

The app already contains useful GEX, regime, continuation, flow, report, and decision information. Its presentation gives that information too little hierarchy: a 1126px marketing-template shell, ten peer navigation links, route-specific control treatments, and inconsistent inline layout styles make it harder than necessary to answer:

1. What is the market state now?
2. What deserves attention now?
3. What should I inspect next, and why?

The outcome is a desktop-first market workbench: calm, information-dense, and readable without being a wall of tables. Overview becomes the cross-market morning brief; Dashboard becomes the focused GEX Explorer; Decisions becomes the Opportunities workspace, explicitly still a screening tool rather than a trading terminal.

## Principles

- State before detail: freshness, regime, and the highest-priority signals precede dense charts and tables.
- One job per page: pair a title, a short explanation of what it answers, and its most consequential control.
- Progressive disclosure: show rankings first; open underlying evidence in a desktop side panel or narrow-screen drawer.
- Auditability stays visible: retain source timing, calculation caveats, null/no-data states, score breakdowns, and non-recommendation language.
- Calm visual language: colour conveys semantic state, freshness, selection, and warnings—not decoration. Keep written labels alongside colour.
- Keyboard-first paths: users can switch workspace/symbol, change filters, enter a table, open detail, and close it without a mouse.

## Target information architecture

    ┌───────── App rail ─────────┐ ┌──────────── Context bar ────────────┐
    │ GEX Workbench              │ │ Symbol / expiry / snapshot · fresh │
    │                             │ ├────────────────────────────────────┤
    │ TODAY                       │ │ Page title + market-read summary   │
    │   Overview                  │ ├────────────────────────────────────┤
    │   Opportunities             │ │ Primary analysis                    │
    │                             │ │ cards, charts, ranked table        │
    │ ANALYZE                     │ │                                    │
    │   GEX Explorer              │ │ Optional evidence drawer            │
    │   Regime · Scan             │ └────────────────────────────────────┘
    │   Rotation · Flows          │
    │ REVIEW                      │
    │   Report · History          │
    │ Settings                    │
    └─────────────────────────────┘

The rail is collapsible on desktop and a compact menu at narrow widths. Current routes remain valid, including /dashboard, /decisions, and /report; the new grouping changes orientation, not the route contract or URL-defined filters.

## Experience specification

### Shared shell

- Replace the centered, bordered page shell with a full-height app surface, stable left rail, and sticky context bar. Keep reading-width limits, but do not make charts and tables feel trapped in a marketing card.
- Group the current ten navigation items into Today, Analyze, and Review. Use icon plus visible text, clear active state, and accessible labels; an icon is never the sole signal.
- Keep the context bar route-aware. Symbol pages retain asset, expiry, snapshot, and freshness; universe pages retain their data freshness and relevant shared filters. Remove duplicated page-body selectors only after URL behavior is tested.
- Add a client-side command/search palette (Ctrl/Cmd+K) for route navigation and existing-symbol switching; no new API or market data is required.

### Page priorities

| Workspace | New reading order | Preserved evidence |
|---|---|---|
| Overview | Tape/freshness → concise market read → ranked signals → links to evidence | RegimeStrip, breakout, trend, continuation data and ranking rules |
| Opportunities | Status summary → filter/threshold → ranked table → selected detail → no-trade reasons and track record | URL min_score, generated-from disclaimer, score breakdown, outcomes |
| GEX Explorer | Spot/net-GEX/nearest levels/as-of → GEX-by-strike → profile → semantic key-level table | existing GEX values, chart options, null rules and freshness |
| Scan/Regime/Rotation/Flows | Page question → caveat/freshness → URL-backed toolbar → table/chart → detail | sorting, aria-sort, null-as-em-dash and query contracts |
| Report/History | Context → anchored/collapsible sections or time navigation → detail | CFD validation, capture-now no-data path, disclaimers and deep links |

### Data-display behavior

- Standardize a page header: title, one-line what-this-answers statement, freshness/caveat, and actions.
- Standardize chart frames: title, one-sentence reading cue, source/as-of line, visible legend, and optional expand action.
- Place tables in surface cards with sticky headers, aligned numbers, an obvious selected row, contained horizontal scrolling, and density appropriate to the viewport.
- Use metric cards for current state only, not every datum. Key levels retain an accessible semantic table.
- At 390px, the rail becomes a menu, context controls wrap or deliberately scroll, and detail becomes a focus-managed drawer.

## Design-system contract

Introduce semantic tokens for app/raised/subtle surface, strong/default/muted text, positive/negative/caution/info, border, focus ring, spacing, radius, elevation, and rail width. Map light and dark themes to roles; do not add local hex colours. Chart palettes continue through vizPaletteFor and must stay consistent with DOM tokens.

Build and test these primitives before page migrations:

| Primitive | Responsibility |
|---|---|
| AppFrame, SideRail, ContextBar | route grouping, shared controls, responsive navigation |
| PageHeader | title, reading cue, freshness/caveat, actions |
| Surface, MetricCard, MetricStrip | state hierarchy and summaries |
| Toolbar, segmented control | compact URL-backed filters and keyboard behavior |
| DataTableFrame | title, reading cue, source timing, density/expand slots |
| DetailDrawer | focus-managed evidence view on narrow screens |
| EmptyState, ErrorState, LoadingState | one honest treatment of absence, failure, and pending work |

Replace repeated inline layout styles with these primitives. Existing formatters, query hooks, URL hooks, API types, and calculation tests remain the sources of truth.

## Delivery plan

### T62 · Sonnet · none

**UX baseline and interaction specification**

Paths: plans/ui-ux-refresh; read-only frontend inspection; no production code.

Capture current routes at 1440px, 1024px, 768px, and 390px in both themes. Inventory each route’s job, control, empty/error/loading state, keyboard path, inline-style cluster, and preserved URL parameters. Add annotated desktop/narrow wireframes and approve labels against the terms already used by the app.

Acceptance: each current route maps to a new navigation group and primary job, retained URL state is documented, and the wireframes are approved before implementation.

### T63 · Sonnet · T62

**Tokenized workbench shell and navigation**

Paths: frontend/src/index.css, frontend/src/components/layout, frontend/src/App.tsx, frontend/src/theme, layout tests.

Implement AppFrame, rail, context bar, grouped nav, focus styling, and responsive menu. Carry query strings exactly as the current AppShell does. Do not migrate page content in this task.

Acceptance: all routes and controls still work; active navigation is unambiguous; Escape and keyboard navigation are tested; no horizontal page scroll at 390px; both themes pass approved shell screenshots.

### T64 · Sonnet · T63

**Reusable page hierarchy and accessible data-display primitives**

Paths: shared components under frontend/src/components/ui (or a project-consistent equivalent), index.css, component tests.

Implement the primitives above, including a focus-managed detail drawer. Migrate at most one representative page per primitive, retaining all fetch and calculation contracts.

Acceptance: keyboard/screen-reader tests pass; drawer focus returns to its trigger; non-colour status cues and contrast are checked in both themes; primitives do not introduce palette values of their own.

### T65 · Sonnet · T63, T64

**Today workspace: Overview and Opportunities**

Paths: frontend/src/pages/Overview.tsx, frontend/src/pages/Decisions.tsx, related components and tests.

Apply the daily-brief and opportunity hierarchy. Preserve queries, ranking rules, No Trade evidence, disclaimers, URL parameters, and track record.

Acceptance: API values and default ordering do not change; selected opportunity works by keyboard and at 390px; null/no-data/error states stay honest; representative dark/light and desktop/narrow tests pass.

### T66 · Sonnet · T63, T64

**Analyze workspace: GEX Explorer, Scan, Regime, Rotation, and Flows**

Paths: frontend/src/pages Dashboard, Scan, Regime, Rotation, Flows; their components and tests.

Migrate to the shared header, toolbar, surfaces, chart frame, and detail patterns. Preserve all URL state, sorting, null handling, chart palette/option contracts, and accessibility semantics. Do not add a signal or modify calculations.

Acceptance: API-derived values and chart options are unchanged; deep links survive navigation; table/detail actions work by keyboard; all pages work at 390px, 768px, 1024px, and 1440px.

### T67 · Sonnet · T63, T64

**Review workspace: Report, History, Settings, and visual-debt removal**

Paths: frontend/src/pages Report, History, Settings; related components/tests; frontend/src/index.css.

Migrate remaining pages, remove duplicated page-body selectors only after context-bar parity is verified, convert repeated inline layout objects to primitives, and remove obsolete marketing-template CSS only when unused.

Acceptance: report disclaimers and CFD validation remain; History's no-data capture action works [^t67-history]; no visual change alters API calls, calculations, or documented route semantics.

[^t67-history]: Wrong premise — History never had a capture action; see the Result section below.

### T68 · Sonnet · T65, T66, T67

**Visual QA, accessibility audit, and performance guardrail**

Paths: frontend tests, any selected visual-test setup, and this plan’s Result section.

Audit every route at the four viewport sizes and both themes. Test keyboard-only use, focus visibility, reduced motion, loading/error/empty states, and the 404 capture path. Compare production bundle size before and after; do not add a UI library unless a measured maintenance/accessibility need requires it.

Acceptance: npm test, npm run lint, and tsc -b pass; no unresolved clipping/overlap; no new accessibility violations in the chosen audit; screenshots and intentional exceptions are recorded.

### T69 · Sonnet · T65, T66, T67

**Compact-first density pass: Overview, GEX Explorer, Report**

Paths: frontend/src/pages/Overview.tsx, frontend/src/pages/Dashboard.tsx, frontend/src/pages/Report.tsx, frontend/src/components/overview/CaptureFreshnessStrip.tsx, frontend/src/components/regime/RegimeStrip.tsx, frontend/src/components/ui/MetricCard.tsx (additive variant only), frontend/src/index.css, related tests.

Added 2026-09-10 after the user reviewed the live T62-T68 build (their own notes, informed by
a ChatGPT critique, are in `plans/ui-ux-refresh/02-first-pass-review`): the migration preserved
every value and control but made several pages taller than necessary — freshness/status
information that could be one line instead took several card-rows, and Report's six sections all
render expanded, forcing a long scroll to reach anything below the fold. This task compacts
presentation only; no fetch, ranking, calculation, or URL parameter changes.

In priority order (do Overview first, then GEX Explorer, then Report; the MetricCard/RegimeStrip
work only needs to happen once, shared by whichever pages need it):

- **Overview**: collapse `CaptureFreshnessStrip`'s five metric cards into one compact status
  line (e.g. "5/5 chains fresh · latest capture 16:19 ET"), naming any stale symbol inline only
  when one exists; move the full five-symbol detail behind a disclosure (native `<details>`,
  matching Rotation's existing "About this chart" pattern) rather than always-visible cards.
  Move "Where continuation is" (the ranked breakout/trend signal blocks) so it reads before the
  full `RegimeStrip`, not after it — the freshness line stays first, `RegimeStrip` moves down.
- **GEX Explorer (Dashboard.tsx)**: remove the `[`/`]` keyboard-shortcut hint paragraph (the
  shortcut itself stays; only the visible tip goes — it's discoverable via the command palette
  and this file's own comment can note it instead) and the redundant "As of" metric card (the
  shell's ContextBar already shows snapshot/freshness on this route — confirm that before
  deleting, the same way T67 confirmed ContextBar parity before removing Report's selects).
  Keep Spot/Net GEX/Nearest wall/Flip point as a compact 2-column grid at narrow widths rather
  than wrapping into three rows. Let the user switch between GEX-by-strike and Gamma Profile
  on narrow screens via a toggle (reuse `Toolbar`/`SegmentedControl`) instead of stacking both
  full-height charts; keep both charts side by side at desktop widths as today.
- **Report**: convert Gamma exposure, Premium selling screen, Playbook, Risk alerts, and Summary
  into anchored disclosure sections (native `<details>`, consistent with Rotation's pattern and
  the existing "View full report" panel's `aria-expanded` idiom) so the page doesn't force a full
  scroll through all six by default. "Levels straddling spot" (the key-levels section) and the
  three top summary cards (Current price/Volatility/Market sentiment) stay expanded/visible by
  default — the concise, load-bearing read; the rest starts collapsed and expands on demand.
  Every disclaimer, CFD validation path, and value must survive unchanged — this is the same
  page T67 already rebuilt onto primitives, so verify nothing from that task's Result-section
  claims (disclaimer strings, CFD logic, capture-now flow) regressed.
- **Supporting**: add a compact/dense variant to `MetricCard`/`MetricStrip` (additive — existing
  full-size usages elsewhere must render unchanged) for Overview's status line and anywhere else
  a full card is denser than the value needs. Give `RegimeStrip` a way to show a primary subset
  of its nine tiles first with the rest in a secondary disclosure when embedded on Overview,
  while `/regime` itself keeps showing the complete, unabbreviated strip — picking which tiles
  count as "primary" for Overview is a named judgment call (the plan's own suggestion: the
  vol/term-structure cluster over the four cross-asset macro moves), not a fixed spec.

Acceptance: no API value, ranking, calculation, or URL parameter changes anywhere touched;
every disclosure defaults to the state named above and is reachable/operable by keyboard
(native `<details>` already is); Overview's freshness line still distinguishes "all fresh" from
"N stale" without fabricating certainty; GEX Explorer's chart toggle doesn't lose either chart's
content, only its default visibility at narrow widths; Report's disclaimers/CFD validation/
capture-now flow are unchanged; `npm run lint`, `npx tsc -b`, `npm test`, and `npm run build`
all pass.

## Dependencies

    T62 → T63 → T64 → T65 ─┐
                     ├─ T66 ├→ T68
                     └─ T67 ┘

T65–T67 may proceed in parallel only after T63/T64 land. They must not concurrently edit shared CSS or layout files.

## Non-negotiables

- Analysis only: no broker integration, order ticket, execution language, or routing affordance.
- Preserve T37’s expected-no-data behavior, raw-error prohibition, capture-now affordance, and freshness wording.
- Preserve URL-driven filter/sort/snapshot state and deep links. A view preference may use local storage only if it does not alter URL-defined results.
- Preserve chart accessibility labels, data-table semantics, null versus zero behavior, and vizPaletteFor dark/light behavior.
- Do not add a general component library by default; the existing React/CSS/chart stack is sufficient unless T64 proves a specific gap.

## Verified facts and likely risks

- The shell currently exposes ten peer links: Overview, Dashboard, Report, History, Scan, Regime, Rotation, Flows, Decisions, and Settings. Overview is the landing page; /dashboard is the single-symbol GEX workspace.
- React 19, React Router 7, TanStack Query 5, ECharts, and Lightweight Charts are already present; no runtime dependency is required.
- The root is fixed at 1126px with a border, and the global stylesheet still contains marketing-template assumptions such as global centered text. KeyLevels, Report, and decision/report elements retain inline layout styles while scan pages use a separate CSS vocabulary.
- Screenshot files in the repository depict an earlier report-style screen, not a verified current build. They support the decision to avoid card clutter but are not an implementation reference.
- A rail can crowd strike charts; measure chart minimum widths. Moving controls can drop URL fields or double-fetch. A drawer must trap/restore focus. Reflow can leave ECharts at stale widths. Fixtures must cover all current stale/null/empty variants so a polished UI never fabricates zero or certainty.

## Out of scope

New analytics, intraday streaming, decision calibration, mobile-native apps, accounts, collaboration, alerts, and every broker/trading action.

## Result

**T62** (done 2026-09-10): read-only route/shell inventory + wireframes, no code changes. No
browser-automation tool was installed at the time, so the mandated screenshot pass was
substituted with static-code inspection; wireframes were built with the design-canvas tool
instead. Full inventory, nav mapping, and retained-URL-state table: `01-ux-baseline.md`.
Wireframes approved by the user 2026-09-10 with no changes requested:
https://claude.ai/code/artifact/886d13f2-12f5-468e-afd6-636c13eabb44

**T63** (done 2026-09-10): `AppFrame`/`SideRail`/`ContextBar`/`CommandPalette` built; old
`AppShell.tsx`/`TopBar.tsx` replaced (`ContextBar` is `TopBar` renamed, logic unchanged).
Semantic tokens added to `index.css` (surface/text/status/border/focus-ring/spacing/
radius/elevation/rail-width), mapped in both themes; `--status-*` literal hex copied from
`theme/vizPalette.ts` so a future CSS status cue and the existing ECharts palette agree. Old
`#root` 1126px/border/center-text shell removed along with the two page-level `text-align`
resets that existed only to fight it — verified no other `text-align` rules were touched
(remaining ones are genuine cell/label alignment). Route paths unchanged; only nav labels and
grouping moved (`/dashboard` → "GEX Explorer", `/decisions` → "Opportunities"). Narrow-width
drawer and the Ctrl/Cmd+K command palette share one focus-trap/Escape/backdrop/focus-return
hook (`useOverlayDismiss`). Verified independently (not just from the implementing agent's
report): `git diff --stat` matches the claimed file list exactly, no `package.json`/lock
changes from the agent's local Playwright install, `npm run lint` (0 errors, pre-existing
warnings only), `npx tsc -b` clean, `npx vitest run` 291/291 passing, and manual read-through
of `SideRail.tsx`/`useOverlayDismiss.ts`/`App.tsx`/the CSS diff confirmed the focus-trap,
`aria-current`-equivalent active state, icon+label pairing, and removed dead CSS all match the
acceptance criteria. Known gap: rail collapse state isn't persisted across sessions (not
required); T68 should still run a dedicated accessibility audit rather than rely on this task's
own Playwright pass.

**T64** (done 2026-09-10): primitives added under `frontend/src/components/ui/` — `Surface`,
`PageHeader`, `MetricCard`/`MetricStrip`, `Toolbar`/`SegmentedControl`, `DataTableFrame`,
`DetailDrawer` (built on T63's `useOverlayDismiss`) — plus `LoadingState` alongside the existing
`EmptyState`/`ErrorState` (left unchanged; already a clean primitive shape). `/decisions`
(Opportunities) is the one representative page migrated, exercising every primitive at once;
`OpportunityDetail`/`TrackRecord` were trimmed to content-only since `DetailDrawer` now owns
the panel chrome. `DetailDrawer` must be mounted unconditionally by its caller (a conditionally
-mounted `{open && <DetailDrawer/>}` would destroy `useOverlayDismiss`'s hook instance before its
focus-return effect can run) — `Decisions.tsx` follows this, matching `CommandPalette`'s existing
pattern. DetailDrawer traps focus/returns it to the trigger at every viewport, not only the
narrow one — a deliberate simplification over two different focus disciplines per breakpoint.
Verified independently: `git status` matches the claimed file list exactly, `npm run lint` (0
errors, same 5 pre-existing warnings), `npx tsc -b` clean, `npx vitest run` 313/313 passing (44
files), a grep of every new primitive for hex/rgb literals returned nothing, and a direct read of
`DetailDrawer.tsx` and `Decisions.tsx` confirmed the unconditional-mount contract and the
focus-trap wiring match the report. Known gaps: only `/decisions` migrated (T65-T67's job for
the rest); no dedicated contrast-ratio tool run against the four status tokens beyond reusing
`vizPalette.ts`'s already-measured values (T68).

**T65** (done 2026-09-10): Overview gained a `PageHeader`, a new `CaptureFreshnessStrip`
(surfaces `GET /api/health/capture`'s per-symbol `SymbolCaptureHealth` — already fetched
elsewhere, no new endpoint — through `MetricStrip`, honestly showing "No capture yet"/"Stale"
rather than fabricating a value), and `OverviewBlock` now renders on `Surface`; every loading
`<p>` became `LoadingState`. Decisions/Opportunities (T64's representative page) got its
`MetricStrip` reordered to precede `Toolbar`, matching the plan's status-summary-first reading
order exactly. All ranking logic, URL params, and API values unchanged on both pages — verified
independently: `git status` matches the claimed file list, `npm run lint` (0 errors, same 5
pre-existing warnings), `npx tsc -b` clean, `npx vitest run` 318/318 passing (up from 313), and
direct reads of the new keyboard-only drawer test and the DOM-order test in `Decisions.test.tsx`
confirm they test what the report claims (real `Enter`/`Escape` keydowns, not clicks; a
`compareDocumentPosition` check on summary→toolbar→table order). No screenshot tool was
available (same gap as T62-T64); narrow/dark-light coverage is static-CSS inspection + hex/rgb
grep, not a rendered visual pass — still owed to T68.

**T66** (done 2026-09-10): GEX Explorer/Dashboard, Scan, Regime, Rotation, and Flows all
migrated onto `PageHeader`/`Toolbar`/`SegmentedControl`/`DataTableFrame`/`LoadingState`, in the
plan's "question → caveat → toolbar → table → detail" order. `KeyLevels.tsx`'s 9 inline-style
clusters rebuilt onto `Surface` + token-only CSS — the net-GEX sign dot deliberately kept its
`vizPaletteFor` diverging-positive/negative color rather than switching to the generic
positive/negative status tokens (different semantic pairing; verified no hex/rgb literals were
introduced). Dashboard gained a spot/net-GEX/nearest-wall/flip-point/as-of `MetricStrip` (the
plan's named fit). Scan's `BreakoutDetail`/`TrendDetail` and Flows' 4-state model (loading/
error/empty/no_flow_data+banner) moved onto `DetailDrawer`/`PageHeader` without collapsing the
richer state model. Verified independently: `git status` matches the claimed file list exactly
(only files inside T66's allowed paths changed), `npm run lint` (0 errors, same 5 pre-existing
warnings), `npx tsc -b` clean, `npx vitest run` 318/318 passing, a hex/rgb grep of `KeyLevels.tsx`
returned nothing, and a direct read confirmed the null-vs-zero level formatting still routes
through the existing `formatStrike`/`formatDistance` helpers unchanged. Known gaps: no rendered
390/768/1024/1440px pass (static-CSS inspection only, consistent with every prior task in this
plan) — still owed to T68; Scan's Trend-view detail drawer has no dedicated keyboard test
(Breakouts' does), though it shares the same already-tested `DetailDrawer` mechanism.

**T67** (done 2026-09-10): Report.tsx's 82 inline-style clusters rebuilt onto `PageHeader`,
`MetricCard`, `DataTableFrame`, `Surface`, and `EmptyState` — `LevelChip`'s support/resistance
colour is the one deliberate exception, still reading `vizPaletteFor` directly rather than a
generic status token (same precedent T66 set for KeyLevels' net-GEX dot). Report's inline
symbol/expiry selects were removed from the page body — verified first, not assumed: `/report`
is confirmed absent from `ContextBar.tsx`'s `SCAN_FAMILY_PATHS` set, so it already gets the
shell's `AssetSelector`/expiry/snapshot controls, and a T63 test already covered this
(`ContextBar.test.tsx`: "renders the dashboard controls on /report and /history too"). The CFD
input has no shell equivalent and stayed page-level. Settings and History got `PageHeader` +
consistent empty/loading treatment only (History's underlying table is still T15's unbuilt
stub — untouched, correctly out of scope here).

**Correction to this plan's own acceptance line**: "History's no-data capture action works"
(this file's T67 acceptance bullet) assumed a Capture-now/T37 affordance on History that never
existed — independently confirmed via `git log` (only T12's original scaffold and T55's
unrelated pass touch `History.tsx`, neither adds a capture action), a grep for
`useCaptureSnapshot`/"Capture now" across the whole frontend (matches only in `Report.tsx` and
its own test/mocks), and a read of the backend's `get_levels_history` endpoint (`backend/app/api/
gex.py`), which returns a plain `200 []` on no data — it never 404s the way `/api/report/
{underlying}` does, so there is no clean "never captured" signal to key a button off. The
implementing agent correctly declined to build that (T15's job, out of scope for a restyle
task) rather than fabricate the feature to satisfy the acceptance line, and documented the
reasoning in `History.tsx`'s own docstring. Filed here so the plan's premise isn't repeated as
fact later.

Verified independently otherwise: `git status` matches the claimed file list exactly (nothing
outside T67's paths touched), a grep for the app's three core disclaimer strings ("not a
recommendation", "never routes an order", "Screening output") found all three verbatim in
`Report.tsx` post-edit, `npm run lint` (0 errors, same 5 pre-existing warnings), `npx tsc -b`
clean, `npx vitest run` 318/318 passing. Known gap: no rendered visual pass (static-CSS
inspection only) — still owed to T68.

**T68** (done 2026-09-10): the real rendered pass every prior task deferred. Method: Chromium
via Playwright (`npm install --no-save playwright@1.63.0 @axe-core/playwright` in `frontend/`,
matching T63's approach — installing both together in one command matters, since a lone
`--no-save` install prunes any other not-in-package.json package already present, which bit
this task twice before that was worked out), driving the app's own `docker compose` dev server
at `localhost:5173` rather than a separately started one (already running; confirmed no other
process needed killing). Not a static-CSS fallback — every finding below came from an actual
rendered page.

*The environment itself was broken first.* `frontend/.env.local` (gitignored, not part of any
diff) was still pointed at a one-off T16 verification backend (`127.0.0.1:8031`, MSW mocks
disabled) that hasn't existed in a long time. With mocks off and the target port dead, MSW's
Service Worker never registers and every page sat in permanent "Loading…" — true for a real
browser, not just Playwright. Reset to `.env.example`'s documented default
(`VITE_API_BASE_URL=http://localhost:8001`, `VITE_ENABLE_MOCKS=true`) and restarted the
`frontend` container; MSW came back and every subsequent check ran against real fixture data.
Recorded here since it would otherwise silently reappear for the next person who opens the app.

**Bugs found and fixed (all verified independently pre/post via a rendered pass, not just code
review):**

1. **Production build was completely broken.** `index.css`'s new T64 primitives comment
   (around what was line 1319) embedded `--surface-*/--text-*/...` as a `/`-separated list —
   the literal `*/` inside that list closed the CSS comment early. Browsers/dev-mode tolerate
   this (the resulting garbage becomes an invalid selector that gets silently dropped, along
   with the next real rule — `.ui-surface--app`, i.e. every `DataTableFrame`'s background,
   silently unstyled app-wide), but `vite build`'s stricter `lightningcss` minifier hard-fails
   on it. `npm run build` would not produce a `dist/` at all before this fix. Changed the
   `/`-separators to commas; no other content changed.
2. **Page-level horizontal overflow at real widths (not caught by prior tasks' static
   inspection because it only shows up once real, unwrapped content is measured):**
   `KeyLevels.tsx`'s 4-column table and `Report.tsx`'s `CandidateTable` were bare `<table>`s
   with no scroll container — wrapped both in the same `.scan-table-container` (`overflow-x:
   auto`) every scan-family table already uses. `/rotation`'s `.rotation-layout__chart` had an
   unconditional `min-width: 480px` that forced the page wider than the viewport at 390px and,
   in the two-column layout, at 1024px too (480+360+16 didn't fit the content column once the
   rail's width came out of it). Fix has two parts: the single-column breakpoint widened from
   900px to 1150px (the width below which two columns genuinely can't fit), and the chart
   itself is now contained-scrollable (`.rotation-chart-scroll` / `.rotation-chart-box`,
   `min-width: 480px` moved onto the scrollable child) rather than forced to squeeze below its
   legible size — squeezing it, tried first, made the RRG chart's axis/quadrant labels overlap
   illegibly well before 390px, which a bare overflow-fix would have silently introduced as a
   new bug. Verified with a `document.documentElement.scrollWidth` vs `clientWidth` check
   across 390/768/1024/1150±1/1200/1300/1440px, 11 routes × 2 themes (88 combinations): zero
   page-level overflow, before *and* after re-verifying against real (not loading-state)
   content.
3. **Accessibility (axe-core, one run per route × theme at 1440px, plus a 390px spot-check on
   `/decisions` and `/scan`):**
   - `color-contrast` (serious) on the vast majority of muted secondary text and every accent-
     colored link, app-wide (133 nodes on `/overview` alone at the start). Root cause:
     `--text-muted: color-mix(in srgb, var(--text) 65%, var(--bg))` measured 2.3–2.9:1 against
     `--code-bg`-based card surfaces (need 4.5:1), and `--accent` (#aa3bff, light theme)
     measured 3.95–4.39:1 as link/text color. Fixed by raising the light-theme mix to 95% (a
     new explicit 80% override added for dark, which already had more headroom) and darkening
     light's `--accent` to `#9935e6` (same hue, GexByStrike.tsx's hardcoded mirror of this
     token updated per that file's own "keep in sync by hand" docstring) — both now clear
     4.5:1 against every surface in the app in both themes. This resolved the token-driven
     majority of violations everywhere (e.g. `/history` and `/settings` went from a handful of
     violations to zero). **Not fixed, and flagged as a known gap:** a separate, pre-existing
     `color: var(--text); opacity: 0.55–0.85` idiom, used ad hoc in ~30 selectors across
     Scan/Rotation/Flows/Decisions/TrackRecord predating this plan (T43–T61 era), is the same
     root problem and still fails contrast in the same way — left alone because some of those
     rules dim non-text content too (e.g. a whole table row), so a blanket swap to
     `--text-muted` needs case-by-case judgment this task's "keep it minimal" mandate didn't
     leave room for. Worth its own follow-up.
   - `heading-order` (moderate): `/overview`'s "Tape" panel and every one of `/report`'s
     top-level sections sat directly under the page's own `h1` as `h3`/`h4`, skipping a level
     (both pre-existing, not introduced by T63–67). Fixed: `OverviewBlock` gained an optional
     `headingLevel` prop (default unchanged, `h3`; `Tape` alone passes `h2`), and every
     `report-section-title`/`report-playbook-card__title` retagged `h3→h2`/`h4→h3` to match
     where they actually sit. Checked each for a font-weight regression first (the generic
     `h1, h2 {font-weight:500}` rule would have lightened Report's titles); pinned
     `font-weight: 700` explicitly where the tag change would otherwise have changed it,
     confirmed visually unchanged by screenshot.
   - `nested-interactive` (serious, `/decisions`): `NoTradeList`'s disclosure put a real
     `SymbolCell` link inside a `<summary>` — two interactive controls nested inside each
     other, and in practice a symbol click also toggled the disclosure under it. Moved the
     link to a plain sibling; only the reason text is the disclosure trigger now.
   - `landmark-unique` (moderate, `/decisions`): its own `<aside>` had no `aria-label`, so it
     shared an unnamed "complementary" role with `SideRail`'s persistent one — every other
     scan-family page's side `<aside>` already has one (Scan: "Open breakouts", Rotation:
     "Rank table and breadth"); this one was simply missing it. Added `aria-label="No trade"`.
   - `scrollable-region-focusable` (serious, narrow widths): every `.scan-table-container` and
     the new `.rotation-chart-scroll` are scrollable but had no way to reach them by keyboard.
     Added `tabIndex={0}` to all six call sites.
   - Re-ran the full sweep after every fix: zero violations left on `/`, `/overview`,
     `/dashboard`, `/history`, `/settings`; only the documented pre-existing opacity pattern
     remains elsewhere. `npm run lint` (0 errors, same 5 warnings), `npx tsc -b` clean,
     `npx vitest run` 318/318 passing after each round of fixes.
4. **Keyboard-only pass** (real `Tab`/`Enter`/`Escape`/`Space` key events, not clicks, 1440px):
   full shell tab order is sane (collapse toggle → nav links in visual order, active one
   marked → theme toggle → into the page's own toolbar), every stop has a real, visible focus
   ring (`outline: solid 2px`, never suppressed). Ctrl/Cmd+K palette: opens with focus on its
   input, Escape closes it, focus returns to exactly whatever had it before. `SideRail`'s
   narrow-width drawer: same. Decisions' table→`DetailDrawer` flow: Tab to a row, Enter opens
   the drawer with focus on its Close button, Escape closes it, focus returns to the row. All
   of T63/T64's focus-trap/return claims hold up in a real browser, not just jsdom.
5. **Reduced motion:** emulated `prefers-reduced-motion: reduce` and opened every overlay
   (palette, a `DetailDrawer`, the narrow rail drawer) — all open/close identically. Not
   because a media query suppresses anything: a repo-wide grep found zero `transition`/
   `animation`/`@keyframes` rules anywhere in `frontend/src`, and every ECharts option that
   sets `animation` sets it `false` already. There is no motion to reduce today; this is the
   rendered confirmation of that rather than a fix.
6. **Loading/error/empty states and the T37 capture-now flow:** attempted via Playwright
   `page.route()` network overrides first, per the task's own "or throttle/block a network
   request" option. This does not work reliably against this app: MSW's Service Worker claims
   control of the page before its first paint (a deliberate MSW design so mocking works from
   the very first load), so it wins the race against `page.route()` on essentially every
   attempt regardless of a fresh browser context — confirmed by instrumenting a route handler
   directly (`routeHit: false`, `controller: true`) rather than trusting an assertion that
   might itself be wrong. Blocking Service Workers at the context level to force the issue
   instead makes the whole app fail to render: `main.tsx` awaits `worker.start()` with no
   fallback before ever calling `createRoot(...).render()`, so a blocked SW means a blank page,
   not a fallback state — a real dev-bootstrap fragility, but one that only a test harness
   disabling SW entirely would ever hit, not a real user. Given that, relied on the existing
   vitest/`msw-node` test suite instead (the task's explicitly sanctioned alternative, and one
   that doesn't share this problem at all): confirmed present and passing —
   `CaptureFreshnessStrip`'s honest "no capture yet"/"stale" wording, Flows' 4-state model
   (loading/error/empty/`no_flow_data`-with-per-symbol-reason banner) including the exact
   `SMH`/`GDX`/`QQQ`/`USO`/`history since 2026-09-08` nuances T62 documented, Report's
   404-vs-real-error split and the `Capture now` button's pending/error contract
   (`Report.test.tsx` + `EmptyState.test.tsx` together), and Decisions' no-trade reasons list.
   Nothing here regressed; this is a method substitution, recorded honestly rather than
   claiming a browser-driven check that didn't actually happen.
7. **Bundle size before/after**, both built with `npm run build`. Before: a clean `git worktree
   add <tmp> HEAD` checkout at this branch's last commit (`cc39a51`, i.e. none of T63–67's
   uncommitted work), `npm install` + `npm run build` there, worktree removed after —
   CSS 17.08 kB (gzip 3.65 kB), JS 1,685.23 kB (gzip 546.21 kB). After (current tree, all of
   T63–67 plus this task's fixes): CSS 30.89 kB (gzip 5.92 kB), JS 1,696.74 kB
   (gzip 549.95 kB). Delta: **+13.81 kB CSS / +2.27 kB gzip, +11.51 kB JS / +3.74 kB gzip**
   (~+6 kB gzip total) — no new runtime dependency (`package.json`/`package-lock.json` show no
   diff at any point in this task), so the whole delta traces to the new primitives/tokens/
   markup, exactly as the plan expected of a token-and-primitive shell rather than a UI library.

**Acceptance status:** `npm run lint` (0 errors, same 5 pre-existing warnings), `npx tsc -b`
(clean), `npx vitest run` (318/318) all pass on the final tree. No unresolved page-level
horizontal overflow or clipping at any tested width/theme (the RRG-chart and gamma-profile
label-collision findings below are the two visual exceptions, one fixed, one not — see below).
No *new* accessibility violations from the automated pass — all found were pre-existing,
several are now fixed, one class is left as a documented, explicitly out-of-scope gap.

**Found, reported, deliberately not fixed:** the GEX Explorer's gamma-profile chart
(`GammaProfile.tsx`, pre-existing T14/T36 code, not part of T63–67) can render its "Spot" and
"Flip" reference-line labels overlapping into illegible interleaved text — reproduces with the
live SPX fixture (spot 7,711.4, flip 7,649.71, only 61 points apart). T36 already anchored one
label to the line's top and the other to its bottom specifically to guard against this, but
that isn't sufficient when the two lines are this close together and the plot area this short.
Fixing it properly means teaching the chart's label placement to detect and handle this case,
which is GEX-engine/chart-internals work, not a shell/primitive fix — left as a found-but-
unfixed defect for a dedicated task rather than reached into chart internals under a "keep it
minimal" QA mandate.

**Housekeeping:** all Playwright/axe scripts and screenshots lived under a `frontend/.t68-qa/`
scratch directory (git-ignored by virtue of never being added), deleted before finishing; the
temporary `git worktree` used for the "before" bundle build was removed
(`git worktree remove`); the only non-source change kept is `frontend/.env.local` being reset
to `.env.example`'s documented default, which is itself gitignored and was already broken
before this task touched anything.

**T69** (done 2026-09-10): compacted Overview, GEX Explorer, and Report per the user's own
live-build review (`02-first-pass-review`). `MetricCard` gained an additive `density="compact"`
variant and `MetricStrip` an additive `className` passthrough — both default to the prior
rendering when omitted, verified against every existing call site (Decisions, Dashboard, Regime,
Rotation, Flows, Report). Overview's `CaptureFreshnessStrip` collapsed from 5 metric cards to
one compact status line ("5/5 chains fresh · latest capture …", naming a stale symbol inline
rather than hiding it) with the full per-symbol table behind a native `<details>`; `RegimeStrip`
moved to render after "Where continuation is" instead of immediately in the tape block, and
gained an additive `compact` prop (Overview-only — `/regime` never passes it, confirmed by grep
finding no `compact` reference in `Regime.tsx`) splitting its nine tiles into four always-visible
(VIX9D/VIX, VIX/VIX3M, VVIX, SPY VRP — the vol/term-structure cluster, the implementing agent's
named judgment call) plus five in a secondary disclosure. Dashboard lost its visible `[`/`]`
keyboard-hint paragraph (the shortcut itself unchanged) and its redundant "As of" metric tile,
removed only after confirming `/dashboard` gets the shell's full freshness/snapshot controls
(absent from `ContextBar.tsx`'s `SCAN_FAMILY_PATHS`, independently re-confirmed); it gained a
narrow-width chart toggle (GEX-by-strike vs. Gamma Profile, one mounted at a time below 880px to
avoid a stale-width ECharts redraw, both mounted together above it, unchanged). Report's Gamma
exposure/Premium selling screen/Playbook/Risk alerts/Summary became collapsed-by-default native
`<details>`; Levels-straddling-spot and the three top summary cards stay expanded. Verified
independently: `git status` matches the claimed file list, `npm run lint` (0 errors, same 5
warnings), `npx tsc -b` clean, `npx vitest run` 334/334 passing (up from 318), `npm run build`
succeeds (CSS 32.06 kB/gzip 6.11 kB, JS 1,699.24 kB/gzip 550.56 kB — a small expected increase
over T68's baseline), and direct reads of `MetricCard.tsx`, `RegimeStrip.tsx`, and a disclaimer
grep on `Report.tsx` confirm the additive-prop and preserved-string claims exactly. Known gap
(the implementing agent's own, confirmed reasonable): jsdom doesn't implement the UA rule that
visually hides a closed `<details>`'s content, so Report's disclosure tests assert `.open`
state directly rather than visibility — a real rendered pass would be the stronger check, and
none was run for this task specifically (T68's Playwright/axe pass predates T69's changes).
