import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
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

// T55's `NotBuiltYetPage` stub (07-ui.md: "Nav links to routes that do not exist yet must
// render and land on a one-line 'not built yet' EmptyState page") is gone -- T56 was the last
// scan-family page task, so every route below is now a real page and nothing renders the stub
// anymore.

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="history" element={<History />} />
        <Route path="report" element={<Report />} />
        <Route path="settings" element={<Settings />} />
        {/* T44, T49, T51, T53 and T56 replaced the Scan, Regime, Rotation, Flows and Overview
            stubs with real pages -- see 07-ui.md's "Pages" section. */}
        <Route path="scan" element={<Scan />} />
        <Route path="regime" element={<Regime />} />
        <Route path="rotation" element={<Rotation />} />
        <Route path="flows" element={<Flows />} />
        <Route path="overview" element={<Overview />} />
        {/* Standalone chart demos. Dashboard assembly is T16's job. */}
        <Route path="demo/gamma-profile" element={<GammaProfileDemo />} />
        <Route path="demo/gex-by-strike" element={<GexByStrikeDemo />} />
      </Route>
    </Routes>
  );
}
