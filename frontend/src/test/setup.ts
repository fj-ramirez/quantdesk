// Vitest setup (see vitest.config.ts `setupFiles`). Runs the same MSW handlers as the dev
// worker (src/mocks/browser.ts) against Node's `fetch`, so tests exercise real network
// calls through TanStack Query rather than mocking `fetch` per test.
import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from '../mocks/server';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// jsdom implements neither `ResizeObserver` nor `matchMedia`, and both are environment gaps
// rather than anything a component should defend against: `lightweight-charts` (T44's
// `EventChart`) observes its container for resizes and queries `matchMedia` for the device
// pixel ratio. Stubbed globally here so any future chart component gets them too, instead of
// each test file re-stubbing the same two browser APIs.
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// `typeof` rather than the `in` operator: `in` narrows `window` to `never` inside the block,
// which TypeScript then refuses to assign to.
if (typeof window.matchMedia !== 'function') {
  (window as Window & typeof globalThis).matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
