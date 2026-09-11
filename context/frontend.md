# Frontend conventions

React 19 + TypeScript + Vite 8, TanStack Query v5, React Router v7, ECharts (via
`echarts-for-react`) for GEX bars and profile curves, TradingView Lightweight Charts for the
price panel. Vitest + jsdom + Testing Library, MSW for request mocking. ESLint flat config.

Dev server on **5173**, API on **8001**.

## Routes

`App.tsx` nests everything under `AppShell`:

| Path | Page |
|---|---|
| `/` | `Dashboard` |
| `/history` | `History` |
| `/settings` | `Settings` |
| `/decisions` | `Decisions` (T60: ranked opportunities with a detail panel; `min_score` in the URL) |
| `/demo/gamma-profile`, `/demo/gex-by-strike` | component sandboxes (`pages/demo/`) |

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
  `POST /api/snapshots/capture?underlying=…` (~2 s).
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

MSW handlers in `mocks/handlers.ts` serve the fixtures in `mocks/fixtures/`, matched with an
origin wildcard so they work whatever `VITE_API_BASE_URL` resolves to. `mocks/browser.ts` is
the dev-time worker, `mocks/server.ts` the test-time server.

Caveat carried in the handlers' own docstring: they do **not** reimplement the GEX engine.
Filter-dependent numbers there are a crude scale factor and are illustrative only — never
treat a mock figure as a correctness reference.
