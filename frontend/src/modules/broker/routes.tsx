/**
 * The broker module's route table (T159), the client-side mirror of
 * `backend/app/modules/broker/router.py`. Exported as a `<Route>` element for the same reason as
 * every other module's (`modules/research/routes.tsx`).
 */
import { Route } from 'react-router-dom';
import { BrokerFrame } from './BrokerFrame';
import { Chart } from './pages/Chart';

export const BROKER_BASE = '/broker';

export const brokerRoutes = (
  <Route path="broker" element={<BrokerFrame />}>
    <Route index element={<Chart />} />
  </Route>
);
