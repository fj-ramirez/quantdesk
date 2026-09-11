import { Route, Routes } from 'react-router-dom';
import { AppFrame } from './components/layout/AppFrame';
import { Dashboard } from './pages/Dashboard';
import { History } from './pages/History';
import { Report } from './pages/Report';
import { Settings } from './pages/Settings';
import { GammaProfileDemo } from './pages/demo/GammaProfileDemo';
import { GexByStrikeDemo } from './pages/demo/GexByStrikeDemo';
import { Scan } from './pages/Scan';
import { Rotation } from './pages/Rotation';
import { Regime } from './pages/Regime';
import { Flows } from './pages/Flows';
import { Overview } from './pages/Overview';
import { Decisions } from './pages/Decisions';

// T55's `NotBuiltYetPage` stub (07-ui.md: "Nav links to routes that do not exist yet must
// render and land on a one-line 'not built yet' EmptyState page") is gone -- T56 was the last
// scan-family page task, so every route below is now a real page and nothing renders the stub
// anymore.

export function App() {
  return (
    <Routes>
      <Route element={<AppFrame />}>
        {/* The user made Overview the landing page on 2026-09-10 (07-ui.md's T56 spec left
            this open: "becomes the nav's default landing when the user says so"). `/overview`
            still resolves to the same page, so every link, bookmark and test that names it
            explicitly keeps working; the dashboard moved to `/dashboard`, which is what
            `SymbolCell` and the report/history pages now link a symbol into. */}
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
    </Routes>
  );
}
