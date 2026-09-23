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
- `src/modules/research/` — same shape (T78). EdgeLab's leaderboard and paper watchlist.
- `src/modules/terminal/` — same shape (T80). The cross-asset board, regime, transmission graph,
  policy path and brief.
- `src/lib/`, `src/theme/`, `src/components/ui/` — **shared, and must stay that way.** A
  percentage, a strike, a New York timestamp and a Surface must render identically in every
  future module, so they cannot belong to one. T78 moved two more things here for that
  reason: `lib/http.ts` (base-URL resolution, `ApiError`, `apiFetch` — `resolveBaseUrl` in
  particular must never be copy-pasted, since its correctness rests on a chain of reasoning
  about same-origin deployment that a drifting second copy would break), and `src/mocks/`,
  which composes every module's MSW handlers into the one server and worker.

One edge deliberately crosses: `shell/AppFrame` renders `modules/gex/components/layout/
ContextBar`, which is GEX-specific. T81 owns making the frame module-agnostic; until then the
import is commented in place rather than papered over. **This is why research has its own
`ResearchFrame`** rather than reusing `AppFrame`: `AppFrame` hard-mounts that ContextBar and a
`SideRail` bound to GEX's `NAV_GROUPS`, and half-building T81's module switcher here would
leave T81 undoing it rather than doing it. `ResearchFrame` is a placeholder that uses the same
shared primitives and should disappear into T81's shell.

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
| `/research` | `Leaderboard` — ranked trials with the noise ceiling (T78) |
| `/research/paper` | `Paper` — the forward-tracking watchlist |
| `/terminal` | `Board` — the normalized change board (T80) |
| `/terminal/regime`, `/graph`, `/policy`, `/brief` | the other four screens |

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

## The research module (T78) — the noise ceiling is not optional

`/research` renders the output of a search over 134,377 combinations, and a search that hard
produces an impressive-looking best row whether or not any edge exists. So:

- **The ceiling ships in the same response as the rows.** `GET /api/research/leaderboard`
  returns `noise_ceiling`, `total_trials` and a per-row `above_ceiling`; there is no separate
  endpoint and nothing is computed in the browser. A component cannot render rows while
  forgetting the ceiling, because it cannot get the first without the second — and the types
  make both non-nullable.
- **Every row is judged against its own OOS span**, not the headline figure. The ceiling scales
  as 1/sqrt(years), so a one-year row and a sixteen-year row at the same Sharpe are very
  different claims. Using one ceiling for all rows would flatter short histories, which is
  exactly where overfitting hides.
- **Rows below their ceiling are dimmed, not hidden**, and carry the words "below noise
  ceiling" — text, because the verdict has to survive a screen reader and a greyscale
  screenshot. Hiding them would be dishonest in the other direction: that the best of 134,377
  attempts is *still* under the floor is the most useful thing the page says.
- **Filtering never lowers the ceiling.** The denominator is the whole registry; narrowing to
  one market does not mean fewer experiments were run. A ceiling that moved with a filter would
  let anyone filter their way to a green row.
- **The OOS-reuse and futures roll-gap caveats render on the page**, not only in a README.
- **The paper watchlist is ordered by promotion date, never by performance.** It is the only
  un-fitted evidence in the module; sorting it by outcome would quietly make it a second
  leaderboard. Neither the API nor the page offers that ordering.

In-sample figures appear only in the trial drawer, never on the leaderboard: IS is what the
search fitted, so beside a ranking it reads as corroboration, while beside OOS the *gap* is the
informative part.

## The terminal module (T80) — as-of is the product

`/terminal` is a point-in-time workstation, not a quote board. The one thing to preserve:

- **The as-of control lives in `TerminalFrame`, not on a page, and it is URL state.** Setting it
  re-renders every screen under it, and a board view is therefore a shareable link. A terminal
  where the board was historical and the regime strip was live would be worse than either alone.
- **`asOf` is part of every query key**, so a control that stopped being threaded through would
  break visibly rather than quietly show today.
- **A pinned past moment is flagged permanently and loudly.** The expensive mistake this screen
  can cause is reading a historical board as live, and that looks exactly like reading a live one.
- **The MSW fixtures vary by `as_of`** — fewer scored rows early, no edge estimates before the
  night they were computed. A mock that ignored the parameter would let a broken control pass
  every test.
- **A missing value is never a zero.** Unscored series are listed with their reason
  (`no_data`, `insufficient_history`); at a historical as-of most series legitimately have none,
  and a grid of zeros would render an eerily calm market.
- **The z scale is banded, not continuous**, nothing under 1 sigma is coloured, and the number is
  always printed. Colour reinforces; it never carries the value alone.
- **`expected_sign === 0` means regime-dependent, not unknown**, and the graph says so in words.

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

## Tabs, pagination and detail (T122)

- **A crowded page splits into in-section tabs** (`components/ui/Tabs`). The active tab is URL
  state via `useTabParam('tab', VALUES)`; the first value is the default and never written.
  Only the active panel mounts, so a panel that owns a query fetches when opened.
- **Long tables page** — `ScanTable`'s `pageSize` prop, or `usePagination` + `<Pagination>`
  for a hand-built table. Paging happens after sorting, the page resets when the ordering
  changes, and the pager always prints the full count. A test asserting something about *every*
  row uses `test/pagination.ts`'s `forEveryPage`, never only page 1.
- **Detail opens in `DetailDrawer`**, a modal side sheet (bottom sheet at ≤640px). Keep it
  mounted outside any tab panel so a tab switch never unmounts it mid-focus-return.

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

`src/mocks/` composes every module's handlers into the one `server` (Vitest) and `worker`
(dev) — T78 moved them up out of `modules/gex/mocks/` because the Vitest server runs with
`onUnhandledRequest: 'error'`, so an unregistered module's request fails a test with a
confusing network error rather than a useful assertion. A module's own handlers stay in its
`mocks/`; only the composition is shared.

The research fixtures deliberately mirror the real registry's current state — the top row is
*below* its ceiling — because building the page against optimistic mock data would have tuned
the UI for a state that has never occurred. One long-span row exists so the `above_ceiling`
branch is still reachable in development.

MSW handlers in `modules/gex/mocks/handlers.ts` serve the fixtures in `mocks/fixtures/`,
matched with an origin wildcard so they work whatever `VITE_API_BASE_URL` resolves to. **They
must be reprefixed in lockstep with the client**: a handler still matching `*/api/...` after
the client moved to `*/api/gex/...` matches nothing, and the symptom is an empty dashboard
rather than an error. 

Caveat carried in the handlers' own docstring: they do **not** reimplement the GEX engine.
Filter-dependent numbers there are a crude scale factor and are illustrative only — never
treat a mock figure as a correctness reference.
