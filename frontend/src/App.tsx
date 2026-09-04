import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { Dashboard } from './pages/Dashboard';
import { History } from './pages/History';
import { Settings } from './pages/Settings';
import { GexByStrikeDemo } from './pages/demo/GexByStrikeDemo';

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="history" element={<History />} />
        <Route path="settings" element={<Settings />} />
        {/* T13 standalone demo — see src/pages/demo/GexByStrikeDemo.tsx for why this isn't
            mounted in Dashboard yet. */}
        <Route path="demo/gex-by-strike" element={<GexByStrikeDemo />} />
      </Route>
    </Routes>
  );
}
