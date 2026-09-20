# 04 · Launcher and module shell — T81

## Goal

The page you land on. One screen that shows what each module currently knows and lets you
into it, plus the frame that makes switching modules feel like one application rather than
three sites sharing a domain.

## What the user sees

`/` — a card per module, each answering *is this module healthy and what is it telling me
right now* without being clicked:

- **GEX** — last capture time and freshness, the flip point and nearest wall for the primary
  symbol, today's decision count.
- **Research** — trials in the registry, cycles since the last restart, how many survivors
  currently clear the noise ceiling, last cycle time.
- **Terminal** — last ingest, the current regime label, and the count of active sign
  conflicts in the transmission graph.

A card whose module is unhealthy says so on the card — a stale capture, a worker that has not
run, a database that is unreachable. The launcher is the one screen where "something stopped
running" has to be visible, because it is the screen that gets opened first.

Inside a module, a persistent switcher in the frame moves between modules without going back
to the launcher, and the module's own nav sits below it.

## Design decisions

**The launcher reads one endpoint per module, not a joined summary endpoint.**
`GET /api/gex/summary`, `/api/research/summary`, `/api/terminal/summary`, each owned by its
module and each independently failable. A single `/api/summary` would put three modules'
freshness behind one query and one failure — and the whole point of the card is to show which
one is broken.

**Every card degrades to a useful failure.** A module whose summary errors renders the card
with the error, not an empty state and not a spinner that never resolves. Reuse the existing
`ErrorState` and `EmptyState` components rather than inventing launcher-specific ones.

**Two levels of navigation, not three.** Module switcher in the frame, module nav below it.
No global search, no command palette, no breadcrumb. Those are worth building once there is
more here than three modules.

**The module registry is one file.** `frontend/src/shell/modules.ts` lists id, label, route,
icon and summary endpoint. Adding a fourth module later is an entry there plus a router entry
plus a backend router — visible, boring and easy to get right.

**Theme, formatting and time helpers stay shared.** `theme/`, `lib/format.ts` and
`lib/time.ts` do not fork per module. A percentage renders the same in every module or the
merge has not actually happened.

## Tasks

### T81 · Sonnet · T78, T80

Build the launcher, the module switcher and the three summary endpoints.

- `GET /api/<module>/summary` ×3, each cheap enough to run on every page load. The GEX one
  reuses the existing freshness logic; do not re-derive it, and do not call
  `/api/gex/health/capture`, which sweeps the ~125-symbol scan universe and is far too heavy
  for this.
- `frontend/src/shell/` — `Launcher.tsx`, `ModuleSwitcher.tsx`, `modules.ts`, and an
  `AppFrame` that takes the active module.
- `/` renders the launcher; each module's index renders its own landing page. GEX's landing
  stays Overview.
- MSW fixtures including the failure cases: each card must be demonstrably renderable in its
  error state.
- Tests for the failure paths specifically. The success path will be exercised constantly by
  hand; the "Postgres is down" card will not be, and that is the one that matters.

May be pulled forward and built against GEX alone if a working launcher is wanted before the
other modules land — the registry file makes the remaining two additive.

## Verified facts

- The frontend already has `EmptyState`, `ErrorState` and `LoadingState` components with
  tests, plus `lib/format.ts` and `lib/time.ts`.
- `AppFrame` in `components/layout/` is the current single layout route wrapping all thirteen
  GEX routes.
- `GET /api/health/capture` queries every symbol in the scan universe and is explicitly
  documented as too heavy for a 30-second healthcheck. The summary endpoints must not reuse
  it.
- MSW is already wired for both browser (`mocks/browser.ts`) and tests (`mocks/server.ts`).

## Acceptance

- `/` renders three cards with live values from a populated lab database.
- Stopping the Postgres container leaves the launcher rendering, with all three cards showing
  their error state and no unhandled rejection in the console.
- Stopping a single worker makes that module's card report staleness within one refresh, while
  the other two are unaffected.
- The switcher moves between modules without a full page reload and preserves each module's
  own URL state.
- Launcher load issues no more than one request per module.

## Likely first-contact failures

- **A summary endpoint quietly becomes expensive.** The research card wants "how many clear
  the noise ceiling", which is a computation over 134,377 rows. Precompute it in the worker
  and store it, or cache it; do not recompute per page load.
- **The switcher remounting the module tree** on every switch, throwing away React Query cache
  and refetching everything.
- **One slow module blocking the launcher.** Fetch the three summaries independently and
  render each card as it arrives.
- Stale-while-loading: a card that shows the previous module's numbers for a frame after a
  switch.

## Out of scope

- Global search and a command palette.
- Cross-module panels — a GEX level drawn on a terminal chart. That needs the shared `core/`
  and belongs to the successor initiative.
- Auth, per-user preferences, multi-user anything.
- Mobile layout beyond what the existing frame already does.
