// Node MSW server for Vitest — see src/test/setup.ts, which starts/stops/resets this
// around every test file so component tests exercise real `fetch` calls against the same
// fixtures the dev server uses, instead of a hand-rolled fetch mock per test.
import { setupServer } from 'msw/node';
import { handlers } from './handlers';

export const server = setupServer(...handlers);
