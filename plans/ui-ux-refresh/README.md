# UI/UX refresh — a calmer market-analysis workbench

**Status:** proposed, 2026-09-10

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

Acceptance: report disclaimers and CFD validation remain; History’s no-data capture action works; no visual change alters API calls, calculations, or documented route semantics.

### T68 · Sonnet · T65, T66, T67

**Visual QA, accessibility audit, and performance guardrail**

Paths: frontend tests, any selected visual-test setup, and this plan’s Result section.

Audit every route at the four viewport sizes and both themes. Test keyboard-only use, focus visibility, reduced motion, loading/error/empty states, and the 404 capture path. Compare production bundle size before and after; do not add a UI library unless a measured maintenance/accessibility need requires it.

Acceptance: npm test, npm run lint, and tsc -b pass; no unresolved clipping/overlap; no new accessibility violations in the chosen audit; screenshots and intentional exceptions are recorded.

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

Not started. Append implementation outcomes, measured regressions, visual-audit screenshots, and intentional scope cuts here as T62–T68 land.
