/**
 * The application router: a launcher at `/`, and one mounted module (T75).
 *
 * Each module owns its own route table and exports it as a `<Route>` element
 * (`modules/gex/routes.tsx`); this file imports them by name. Same rule as the backend's
 * `app/main.py` and for the same reason -- with three modules, three visible imports beat a
 * discovery mechanism, and a module that fails to import is a build error rather than a page
 * that quietly does not exist.
 *
 * `research` (T78) did exactly that -- one import, one line. `terminal` (T80) is next.
 */
import { Route, Routes } from 'react-router-dom';
import { Launcher } from './shell/Launcher';
import { gexRoutes } from './modules/gex/routes';
import { researchRoutes } from './modules/research/routes';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Launcher />} />
      {gexRoutes}
      {researchRoutes}
    </Routes>
  );
}
