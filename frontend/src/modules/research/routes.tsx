/**
 * The research module's route table (T78) — the client-side mirror of
 * `backend/app/modules/research/router.py`.
 *
 * Exported as a `<Route>` **element**, not a component, for the same reason `modules/gex/routes.tsx`
 * is: `<Routes>` reads its children as route configuration rather than rendering them, so a
 * component returning a `<Route>` would be invisible to it. `src/App.tsx` stays one line per
 * module.
 */
import { Route } from 'react-router-dom';
import { ResearchFrame } from './ResearchFrame';
import { Leaderboard } from './pages/Leaderboard';
import { Paper } from './pages/Paper';

/** Where this module is mounted. Anything building a link by hand should agree with this. */
export const RESEARCH_BASE = '/research';

export const researchRoutes = (
  <Route path="research" element={<ResearchFrame />}>
    <Route index element={<Leaderboard />} />
    <Route path="paper" element={<Paper />} />
  </Route>
);
