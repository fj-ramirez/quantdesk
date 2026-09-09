import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { EmptyState } from './components/EmptyState';
import { Dashboard } from './pages/Dashboard';
import { History } from './pages/History';
import { Report } from './pages/Report';
import { Settings } from './pages/Settings';
import { GammaProfileDemo } from './pages/demo/GammaProfileDemo';
import { GexByStrikeDemo } from './pages/demo/GexByStrikeDemo';
import { Scan } from './pages/Scan';

/**
 * T55: a placeholder for a scan-family route whose real page hasn't landed yet (T44/T49/T51/
 * T53/T56 each replace one of these with a real page). One line, per 07-ui.md: "Nav links to
 * routes that do not exist yet must render and land on a one-line 'not built yet' EmptyState
 * page, so the nav does not change shape task by task." Not exported — App.tsx's only export
 * stays `App`, so this doesn't grow the `react-refresh/only-export-components` warning count.
 */
function NotBuiltYetPage({ page }: { page: string }) {
  return (
    <div style={{ padding: 16 }}>
      <EmptyState heading={`${page} is not built yet`} />
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="history" element={<History />} />
        <Route path="report" element={<Report />} />
        <Route path="settings" element={<Settings />} />
        {/* T44 replaced the Scan stub with the real page. The rest remain stubs -- see
            07-ui.md's "Pages" section for each (T49 Regime, T51 Rotation, T53 Flows,
            T56 Overview). */}
        <Route path="scan" element={<Scan />} />
        <Route path="regime" element={<NotBuiltYetPage page="Regime" />} />
        <Route path="rotation" element={<NotBuiltYetPage page="Rotation" />} />
        <Route path="flows" element={<NotBuiltYetPage page="Flows" />} />
        <Route path="overview" element={<NotBuiltYetPage page="Overview" />} />
        {/* Standalone chart demos. Dashboard assembly is T16's job. */}
        <Route path="demo/gamma-profile" element={<GammaProfileDemo />} />
        <Route path="demo/gex-by-strike" element={<GexByStrikeDemo />} />
      </Route>
    </Routes>
  );
}
