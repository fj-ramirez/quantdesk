import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { Dashboard } from './pages/Dashboard';
import { History } from './pages/History';
import { Report } from './pages/Report';
import { Settings } from './pages/Settings';
import { GammaProfileDemo } from './pages/demo/GammaProfileDemo';
import { GexByStrikeDemo } from './pages/demo/GexByStrikeDemo';

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="history" element={<History />} />
        <Route path="report" element={<Report />} />
        <Route path="settings" element={<Settings />} />
        {/* Standalone chart demos. Dashboard assembly is T16's job. */}
        <Route path="demo/gamma-profile" element={<GammaProfileDemo />} />
        <Route path="demo/gex-by-strike" element={<GexByStrikeDemo />} />
      </Route>
    </Routes>
  );
}
