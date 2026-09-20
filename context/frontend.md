# Frontend conventions

React 19 + TypeScript + Vite 8, TanStack Query v5, React Router v7, ECharts (via
`echarts-for-react`) for GEX bars and profile curves, TradingView Lightweight Charts for the
price panel. Vitest + jsdom + Testing Library, MSW for request mocking. ESLint flat config.

Dev server on **5173**, API on **8001**.

## Layout, since T75

The app is a **module host**. `src/App.tsx` is two lines of routing: `/` renders
`shell/Launcher`, and each module contributes a `<Route>` element it exports itself
(`modules/gex/routes.tsx`). Imported by name, never discovered — same rule as the backend.

- `src/shell/` — `AppFrame`, `SideRail`, `CommandPalette`, `ThemeToggle`, `navConfig`,
  `icons`, `Launcher`. The chrome around whichever module is mounted.
- `src/modules/gex/` — `routes.tsx`, `api/`, `pages/`, `components/`, `state/`, `mocks/`.
  Everything that knows what a gamma wall is.
- `src/lib/`, `src/theme/`, `src/components/ui/` — **shared, and must stay that way.** A
  percentage, a strike, a New York timestamp and a Surface must render identically in every
  future module, so they cannot belong to one.

One edge deliberately crosses: `shell/AppFrame` renders `modules/gex/components/layout/
ContextBar`, which is GEX-specific. T81 owns making the frame module-agnostic; until then the
import is commented in place rather than papered over.

## Routes

Every GEX route is one segment deeper since T75. `modules/gex/routes.tsx` nests them under
`AppFrame`:

| Path | Page |
|---|---|
| `/` | `Launcher` (T75; T81 makes it a real page) |
| `/gex`, `/gex/overview` | `Overview` — the module's landing page |
| `/gex/dashboard` | `Dashboard` |
| `/gex/history` | `History` |
| `/gex/settings` | `Settings` |
| `/gex/decisions` | `Decisions` (T60: ranked opportunities with a detail panel; `min_score` in the URL) |
| `/gex/report`, `/gex/scan`, `/gex/regime`, `/gex/rotation`, `/gex/flows` | the scan family |
| `/gex/demo/gamma-profile`, `/gex/demo/gex-by-strike` | component sandboxes (`pages/demo/`) |

## Data layer — three files, three jobs

- **`api/types.ts`** — the wire contract. Mirrors the backend's `api/schemas.py`; update both
  together. `Underlying`, `ExpiryFilter`, `GexResult`, `LevelHistoryRow`, `SnapshotSummary`,
  `ChainResponse`.
- **`api/client.ts`** — typed `fetch` wrapper. Knows how to turn a path + params into a parsed
  typed response or a thrown `ApiError` (carrying `status` and `url`). **No caching or retry
  logic here.** Base URL comes from `VITE_API_BASE_URL`, falling back to
  `http://localhost:8001` — never hardcode it at a call site.
- **`api/queries.ts`** — TanStack Query owns caching/retry. `queryKeys` is the single source of
  cache keys; the hooks are `useGexResult`, `useLevelsHistory`, `useChainLatest`,
  `useSnapshots`. Components call hooks, never `apiClient` directly.

## Error and empty states (T37 — do not regress)

A symbol with no snapshot yet is the **expected** state on first run and after
`docker compose down -v`. It is not an error.

- The backend returns a clean 404 with a specific `detail`; distinguish it from a genuine
  transport/server failure and render an **empty state**, not an error.
- **Never render a raw response body.** No `{"detail": …}` ever reaches the screen; parse the
  envelope and surface the message or a mapped friendly string.
- The empty state explains what happens on its own (EOD capture at 16:20 ET on trading days,
  plus startup catch-up) and offers a "Capture now" affordance hitting
  `POST /api/gex/snapshots/capture?underlying=…` (~2 s).
- `/history` gets the same treatment for a symbol with no level rows.

## State

UI state that a user would want to link to or reload into lives in the **URL**, not in React
state: `state/urlState.ts` exposes `useDashboardParams()` with `DEFAULT_SYMBOL = 'SPX'` and
`DEFAULT_FILTER = 'ALL'`. Add new dashboard controls there rather than lifting another
`useState` into a page.

## Theming

`theme/ThemeContext.tsx` provides `ThemeProvider` / `useTheme()` over `'light' | 'dark'`.
Chart colors come from `theme/vizPalette.ts` (`VIZ_PALETTE_LIGHT`, `VIZ_PALETTE_DARK`,
`vizPaletteFor(theme)`) — charts must read the palette rather than inlining hex values, so
both themes stay consistent. `ThemeToggle` lives in `components/layout/`.

## Formatting

`lib/format.ts` and `lib/time.ts` hold every display transform: `formatGex`, `formatStrike`,
`formatPrice`, `formatDistance`, `formatDistancePct`, `formatIv`, `formatCount`,
`formatNyTime`, `formatNyDateTime`, `formatDelay`, `formatFreshness`. Timestamps arrive as
UTC ISO strings and are rendered in New York time. `formatFreshness` implements the T34 rule:
report data staleness honestly, using `effective_at` and `delayed_minutes`, not the capture
wall clock.

## Testing and mocks

Colocated `*.test.tsx` next to the component. `vitest.config.ts` merges the Vite config and
adds jsdom, globals and `src/test/setup.ts`.

MSW handlers in `modules/gex/mocks/handlers.ts` serve the fixtures in `mocks/fixtures/`,
matched with an origin wildcard so they work whatever `VITE_API_BASE_URL` resolves to. **They
must be reprefixed in lockstep with the client**: a handler still matching `*/api/...` after
the client moved to `*/api/gex/...` matches nothing, and the symptom is an empty dashboard
rather than an error. `mocks/browser.ts` is
the dev-time worker, `mocks/server.ts` the test-time server.

Caveat carried in the handlers' own docstring: they do **not** reimplement the GEX engine.
Filter-dependent numbers there are a crude scale factor and are illustrative only — never
treat a mock figure as a correctness reference.
