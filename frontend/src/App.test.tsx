import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { ThemeProvider } from './theme/ThemeContext';

// The Dashboard (T16) now mounts GexByStrike and GammaProfile for real, and this file
// exercises symbol switches and route navigations across several tests without a full page
// reload in between. `echarts-for-react` paints to a real <canvas>, which jsdom does not
// implement -- see the same mock + rationale in
// src/components/charts/GexByStrike.test.tsx -- and disposing a chart against jsdom's null
// canvas context on unmount (e.g. when a test's render tree is torn down mid-query) throws
// asynchronously outside any try/catch here. The option-builder and single-component
// render tests already cover chart behavior directly; this file only needs to prove the
// shell wires URL state to data.
vi.mock('echarts-for-react', () => ({
  default: () => null,
}));

// End-to-end-ish check that URL state actually drives what's on screen, through real
// TanStack Query hooks hitting the MSW handlers (src/mocks) — not just the isolated hook
// test in src/state/urlState.test.tsx.
function renderApp(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <App />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe('App', () => {
  it('renders the Dashboard with mocked SPX data by default', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());
    // GexByStrike (T13) is mounted too, not just KeyLevels.
    expect(screen.getByRole('img', { name: /GEX by strike for SPX/ })).toBeInTheDocument();
  });

  it('T47: the symbol switcher gains a second, labelled group for the extended ETFs', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());

    const core = screen.getByRole('group', { name: 'Symbol' });
    const extended = screen.getByRole('group', { name: 'Symbol (extended)' });
    expect(within(core).getByRole('button', { name: 'GLD' })).toBeInTheDocument();
    expect(within(core).queryByRole('button', { name: 'XLK' })).not.toBeInTheDocument();
    expect(within(extended).getByRole('button', { name: 'XLK' })).toBeInTheDocument();
    expect(within(extended).queryByRole('button', { name: 'SPX' })).not.toBeInTheDocument();
  });

  it('clicking the QQQ symbol button updates the URL and reloads QQQ data', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'QQQ' }));

    await waitFor(() => expect(screen.getByRole('heading', { name: 'QQQ key levels' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'QQQ' })).toBeDisabled();
  });

  it('deep link ?symbol=QQQ&filter=ZERO_DTE renders the all-null-walls case cleanly, not as zeros', async () => {
    renderApp('/?symbol=QQQ&filter=ZERO_DTE');

    expect(screen.getByRole('button', { name: 'QQQ' })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole('heading', { name: 'QQQ key levels' })).toBeInTheDocument());

    // The dedicated ZERO_DTE mock fixture (gex-qqq-zero-dte.json) has every wall null; the
    // dash formatting and the "no sign change" explanation must render instead of "0" or a
    // thrown error (this is the daily post-close case, not an edge case).
    const callWallRow = screen.getByText('Call wall').closest('tr')!;
    expect(callWallRow.textContent).toContain('—');
    expect(callWallRow.textContent).not.toMatch(/\b0\b/);
    expect(screen.getByText(/No sign change within the profile grid/)).toBeInTheDocument();
  });

  it('the QQQ fixture legitimately has a null flip point and the shell does not crash on it', async () => {
    renderApp('/?symbol=QQQ');
    await waitFor(() => expect(screen.getByText(/No sign change within the profile grid/)).toBeInTheDocument());
  });

  it('navigating to /history preserves the current symbol in the URL', async () => {
    renderApp('/?symbol=SPY');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPY key levels' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('link', { name: 'History' }));

    await waitFor(() => expect(screen.getByText(/level-history rows loaded for SPY/)).toBeInTheDocument());
  });

  it('deep link ?symbol=XLK (a T47 extended symbol) renders cleanly rather than crashing the shell', async () => {
    // T47 acceptance criterion, verbatim: "dashboard deep link ?symbol=XLK renders". The
    // extended-symbol TopBar group carries the button, and the URL-state validator (now built
    // from the full UNDERLYINGS list, core + extended) accepts the symbol; MSW has no XLK
    // fixture (out of this task's scope), so the real assertion is that the shell renders the
    // existing error path rather than throwing -- the same path a genuine, temporary API
    // failure would hit for any symbol.
    renderApp('/?symbol=XLK');
    expect(screen.getByRole('button', { name: 'XLK' })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toContain('Failed to load XLK GEX');
  });

  it('pressing "]" cycles the symbol forward (T16 keyboard shortcut) and updates the URL-driven view', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());

    fireEvent.keyDown(window, { key: ']' });

    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPY key levels' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'SPY' })).toBeDisabled();
  });
});

// T55: the nav gains five scan-family links, each landing on a one-line "not built yet"
// EmptyState today (07-ui.md: "the nav does not change shape task by task").
describe('T55 scan-family nav and stub routes', () => {
  it('the nav has one link for each scan-family route, alongside the existing four', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());

    for (const name of ['Dashboard', 'Report', 'History', 'Scan', 'Regime', 'Rotation', 'Flows', 'Settings', 'Overview']) {
      expect(screen.getByRole('link', { name })).toBeInTheDocument();
    }
  });

  // T44 replaced /scan with the real page, so it is no longer in this list. The remaining
  // four stay stubs until T49/T51/T53/T56 land.
  it.each([
    ['/regime', 'Regime'],
    ['/rotation', 'Rotation'],
    ['/flows', 'Flows'],
    ['/overview', 'Overview'],
  ])('%s renders a one-line "not built yet" empty state, not a crash', async (path, label) => {
    renderApp(path);
    expect(await screen.findByRole('region', { name: `${label} is not built yet` })).toBeInTheDocument();
  });

  it('/scan renders the real scan page (T44), not a stub', async () => {
    renderApp('/scan');
    expect(await screen.findByRole('group', { name: 'View' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Scan is not built yet' })).not.toBeInTheDocument();
  });

  it('the TopBar drops the symbol switcher on /scan and shows it again after navigating back to /', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());
    expect(screen.getByRole('group', { name: 'Symbol' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: 'Scan' }));
    // Keyed on the real page's own view toggle since T44 replaced the stub. The Symbol group
    // is the dashboard control that must disappear; the View group is the scan page's.
    await screen.findByRole('group', { name: 'View' });
    expect(screen.queryByRole('group', { name: 'Symbol' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: 'Dashboard' }));
    await waitFor(() => expect(screen.getByRole('group', { name: 'Symbol' })).toBeInTheDocument());
  });
});
