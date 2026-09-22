/**
 * Every module's MSW handlers, in one list (T78).
 *
 * The server and worker used to live in `modules/gex/mocks/` and register only that module's
 * handlers. With a second module that is actively wrong twice over: the Vitest server runs with
 * `onUnhandledRequest: 'error'`, so an unregistered `/api/research/*` call fails a test with a
 * confusing network error rather than a useful assertion; and `npm run dev` would show a
 * research page that could not load anything.
 *
 * Composition, not discovery -- the same rule `App.tsx` and `app/main.py` follow. A module whose
 * handlers are missing is a visible omission in this file.
 */
import { handlers as gexHandlers } from '../modules/gex/mocks/handlers';
import { shellHandlers } from './shellHandlers';
import { researchHandlers } from '../modules/research/mocks/handlers';
import { terminalHandlers } from '../modules/terminal/mocks/handlers';

export const handlers = [
  ...shellHandlers,
  ...gexHandlers,
  ...researchHandlers,
  ...terminalHandlers,
];
