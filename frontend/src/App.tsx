import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { Dashboard } from './pages/Dashboard';
import { History } from './pages/History';
import { Settings } from './pages/Settings';
import { GammaProfileDemo } from './pages/demo/GammaProfileDemo';

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="history" element={<History />} />
        <Route path="settings" element={<Settings />} />
        {/* T14 stories-style demo (not part of Dashboard assembly, which is T16's job) —
            see src/pages/demo/GammaProfileDemo.tsx for why. */}
        <Route path="demo/gamma-profile" element={<GammaProfileDemo />} />
      </Route>
    </Routes>
  );
}
