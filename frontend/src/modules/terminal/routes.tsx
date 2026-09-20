/**
 * The terminal module's route table (T80) — the mirror of
 * `backend/app/modules/terminal/router.py`.
 *
 * Five screens under one frame, and the frame is where the as-of control lives: every route
 * below re-renders when it changes, which is what makes this a point-in-time terminal rather
 * than a board with a history feature.
 */
import { Route } from 'react-router-dom';
import { TerminalFrame } from './TerminalFrame';
import { Board } from './pages/Board';
import { Brief } from './pages/Brief';
import { Graph } from './pages/Graph';
import { Policy } from './pages/Policy';
import { Regime } from './pages/Regime';

/** Where this module is mounted. Anything building a link by hand should agree with this. */
export const TERMINAL_BASE = '/terminal';

export const terminalRoutes = (
  <Route path="terminal" element={<TerminalFrame />}>
    <Route index element={<Board />} />
    <Route path="regime" element={<Regime />} />
    <Route path="graph" element={<Graph />} />
    <Route path="policy" element={<Policy />} />
    <Route path="brief" element={<Brief />} />
  </Route>
);
