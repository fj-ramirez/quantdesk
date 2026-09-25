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
import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { server } from '../../../mocks/server';
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

describe('Dashboard (GEX Explorer) live 0DTE (T126)', () => {
  it('reads the live pull for 0DTE when nothing is pinned, and says so', async () => {
    renderDashboard('/dashboard?symbol=SPY&filter=ZERO_DTE');
    expect(await screen.findByText(/Live 0DTE · thetadata quotes as of/)).toBeInTheDocument();
    expect(await screen.findByRole('img', { name: /GEX by strike/i })).toBeInTheDocument();
  });

  it('falls back to the stored snapshot and names the reason when the live pull fails', async () => {
    server.use(
      http.get('*/api/gex/gex/:underlying/live', () =>
        HttpResponse.json({ detail: '2026-09-26 is not a trading day: no 0DTE session' }, { status: 409 }),
      ),
    );
    renderDashboard('/dashboard?symbol=SPY&filter=ZERO_DTE');
    expect(
      await screen.findByText(/Live 0DTE unavailable: 2026-09-26 is not a trading day: no 0DTE session\. Showing the latest stored snapshot\./),
    ).toBeInTheDocument();
    expect(await screen.findByRole('img', { name: /GEX by strike/i })).toBeInTheDocument();
  });

  it('does not pull live for any other filter or for a pinned snapshot', async () => {
    let liveCalls = 0;
    server.use(
      http.get('*/api/gex/gex/:underlying/live', () => {
        liveCalls += 1;
        return HttpResponse.json({}, { status: 500 });
      }),
    );
    renderDashboard('/dashboard?symbol=SPY&filter=ALL');
    await screen.findByRole('img', { name: /GEX by strike/i });
    renderDashboard('/dashboard?symbol=SPY&filter=ZERO_DTE&snapshot=91');
    await waitFor(() => expect(screen.getAllByRole('img', { name: /GEX by strike/i })).toHaveLength(2));
    expect(liveCalls).toBe(0);
    expect(screen.queryByText(/Live 0DTE/)).not.toBeInTheDocument();
  });
});

