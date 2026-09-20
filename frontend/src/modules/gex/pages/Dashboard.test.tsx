/**
 * T69 -- density-pass coverage for `Dashboard.tsx` (GEX Explorer): the visible keyboard-hint
 * paragraph is gone, the metric strip's "As of" tile is gone, and a narrow-width toggle shows
 * one chart at a time instead of stacking both. `App.test.tsx` already covers the page end to
 * end at the default (wide) viewport; this file adds the behaviors T69 introduced, including
 * the narrow-width branch `App.test.tsx` never exercises.
 *
 * `echarts-for-react` renders to <canvas>, unavailable in jsdom -- mocked the same way
 * `App.test.tsx`/`GexByStrike.test.tsx` already do; `GexByStrike`'s and `GammaProfile`'s own
 * wrapping elements (`role="img"` / `aria-label="Gamma profile"`) render regardless of the
 * mock, so chart presence is still verified through real markup, not the mock itself.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dashboard } from './Dashboard';
import { ThemeProvider } from '../../../theme/ThemeContext';

vi.mock('echarts-for-react', () => ({
  default: () => null,
}));

/** Replaces `window.matchMedia` for one test with a stub that reports `matches` for every
 * query, restoring the real (jsdom-stubbed, see `src/test/setup.ts`) implementation afterward
 * so other test files are unaffected. */
function stubMatchMedia(matches: boolean) {
  const original = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  return () => {
    window.matchMedia = original;
  };
}

function renderDashboard(initialPath = '/dashboard') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Dashboard />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Dashboard (GEX Explorer) density pass (T69)', () => {
  it('no longer shows the visible "[" / "]" keyboard-hint paragraph', async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByRole('img', { name: /GEX by strike/ })).toBeInTheDocument());
    expect(screen.queryByText(/Tip: press/)).not.toBeInTheDocument();
  });

  it('the metric strip no longer has an "As of" tile', async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByRole('img', { name: /GEX by strike/ })).toBeInTheDocument());
    const strip = screen.getByRole('group', { name: 'SPX at a glance' });
    expect(strip).toHaveTextContent('Spot');
    expect(strip).toHaveTextContent('Net GEX');
    expect(strip).toHaveTextContent('Nearest wall');
    expect(strip).toHaveTextContent('Flip point');
    expect(strip).not.toHaveTextContent('As of');
  });

  it('at a wide viewport, both charts render together and no chart toggle appears', async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByRole('img', { name: /GEX by strike/ })).toBeInTheDocument());
    expect(screen.getByRole('region', { name: 'Gamma profile' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Chart' })).not.toBeInTheDocument();
  });

  it('at a narrow viewport, only the GEX-by-strike chart shows by default, with a toggle to switch', async () => {
    const restore = stubMatchMedia(true);
    renderDashboard();
    await waitFor(() => expect(screen.getByRole('img', { name: /GEX by strike/ })).toBeInTheDocument());

    expect(screen.queryByRole('region', { name: 'Gamma profile' })).not.toBeInTheDocument();
    const toggle = screen.getByRole('group', { name: 'Chart' });
    expect(toggle).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Gamma profile' }));

    await waitFor(() => expect(screen.getByRole('region', { name: 'Gamma profile' })).toBeInTheDocument());
    expect(screen.queryByRole('img', { name: /GEX by strike/ })).not.toBeInTheDocument();

    restore();
  });
});
