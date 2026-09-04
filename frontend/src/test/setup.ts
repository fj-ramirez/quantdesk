// Vitest setup (see vitest.config.ts `setupFiles`). Runs the same MSW handlers as the dev
// worker (src/mocks/browser.ts) against Node's `fetch`, so tests exercise real network
// calls through TanStack Query rather than mocking `fetch` per test.
import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from '../mocks/server';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
