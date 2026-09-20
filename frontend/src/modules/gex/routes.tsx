/**
 * The GEX module's route table (T75) — the client-side mirror of
 * `backend/app/modules/gex/router.py`.
 *
 * Every page this module had before T75 is here, unchanged, one segment deeper: `/dashboard`
 * is now `/gex/dashboard`, `/` is now `/gex`. Nothing about any page changed; only where the
 * router mounts them.
 *
 * Exported as a `<Route>` **element**, not a component. `<Routes>` reads its children as
 * route configuration rather than rendering them, so a component that returned a `<Route>`
 * would be invisible to it. This shape lets `src/App.tsx` stay one line per module — the same
 * "composition, not discovery" rule the backend router follows, and for the same reason: with
 * three modules, three visible imports beat a mechanism.
 */
import { Route } from 'react-router-dom';
import { AppFrame } from '../../shell/AppFrame';
import { Dashboard } from './pages/Dashboard';
import { Decisions } from './pages/Decisions';
import { Flows } from './pages/Flows';
import { History } from './pages/History';
import { Overview } from './pages/Overview';
import { Regime } from './pages/Regime';
import { Report } from './pages/Report';
import { Rotation } from './pages/Rotation';
import { Scan } from './pages/Scan';
import { Settings } from './pages/Settings';
import { GammaProfileDemo } from './pages/demo/GammaProfileDemo';
import { GexByStrikeDemo } from './pages/demo/GexByStrikeDemo';

/** Where this module is mounted. Anything building a link by hand (`SymbolCell`'s deep link
 * into the dashboard, `ContextBar`'s scan-family path set) should agree with this. */
export const GEX_BASE = '/gex';

// T55's `NotBuiltYetPage` stub (07-ui.md: "Nav links to routes that do not exist yet must
// render and land on a one-line 'not built yet' EmptyState page") is gone -- T56 was the last
// scan-family page task, so every route below is a real page and nothing renders the stub
// anymore.
export const gexRoutes = (
  <Route path="gex" element={<AppFrame />}>
    {/* The user made Overview the landing page on 2026-09-10 (07-ui.md's T56 spec left this
        open: "becomes the nav's default landing when the user says so"). `/gex/overview`
        still resolves to the same page, so every link, bookmark and test that names it
        explicitly keeps working; the dashboard is `/gex/dashboard`, which is what
        `SymbolCell` and the report/history pages link a symbol into. */}
    <Route index element={<Overview />} />
    <Route path="overview" element={<Overview />} />
    <Route path="dashboard" element={<Dashboard />} />
    <Route path="history" element={<History />} />
    <Route path="report" element={<Report />} />
    <Route path="settings" element={<Settings />} />
    {/* T44, T49, T51, T53 and T56 replaced the Scan, Regime, Rotation, Flows and Overview
        stubs with real pages -- see 07-ui.md's "Pages" section. */}
    <Route path="scan" element={<Scan />} />
    <Route path="regime" element={<Regime />} />
    <Route path="rotation" element={<Rotation />} />
    <Route path="flows" element={<Flows />} />
    <Route path="decisions" element={<Decisions />} />
    {/* Standalone chart demos. Dashboard assembly is T16's job. */}
    <Route path="demo/gamma-profile" element={<GammaProfileDemo />} />
    <Route path="demo/gex-by-strike" element={<GexByStrikeDemo />} />
  </Route>
);
