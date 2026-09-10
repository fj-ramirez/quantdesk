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

  it('T47: the asset dropdown gains a second, labelled group for the extended ETFs', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Symbol SPX' }));
    const core = screen.getByRole('group', { name: 'Core' });
    const extended = screen.getByRole('group', { name: 'Extended' });
    expect(within(core).getByRole('option', { name: 'GLD' })).toBeInTheDocument();
    expect(within(core).queryByRole('option', { name: 'XLK' })).not.toBeInTheDocument();
    expect(within(extended).getByRole('option', { name: 'XLK' })).toBeInTheDocument();
    expect(within(extended).queryByRole('option', { name: 'SPX' })).not.toBeInTheDocument();
  });

  it('clicking the QQQ symbol option updates the URL and reloads QQQ data', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Symbol SPX' }));
    fireEvent.click(screen.getByRole('option', { name: 'QQQ' }));

    await waitFor(() => expect(screen.getByRole('heading', { name: 'QQQ key levels' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Symbol QQQ' })).toBeInTheDocument();
  });

  it('deep link ?symbol=QQQ&filter=ZERO_DTE renders the all-null-walls case cleanly, not as zeros', async () => {
    renderApp('/?symbol=QQQ&filter=ZERO_DTE');

    expect(screen.getByRole('button', { name: 'Symbol QQQ' })).toBeInTheDocument();
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
    expect(screen.getByRole('button', { name: 'Symbol XLK' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toContain('Failed to load XLK GEX');
  });

  it('pressing "]" cycles the symbol forward (T16 keyboard shortcut) and updates the URL-driven view', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());

    fireEvent.keyDown(window, { key: ']' });

    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPY key levels' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Symbol SPY' })).toBeInTheDocument();
  });
});

// T55: the nav gains five scan-family links (07-ui.md). T44/T49/T51/T53/T56 have since
// replaced every one of the five stub routes with a real page, so the nav's shape is now
// exactly what it will stay -- nothing under it renders `NotBuiltYetPage` (removed with T56)
// any more.
describe('T55 scan-family nav and stub routes', () => {
  it('the nav has one link for each scan-family route, alongside the existing four', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());

    for (const name of ['Dashboard', 'Report', 'History', 'Scan', 'Regime', 'Rotation', 'Flows', 'Settings', 'Overview']) {
      expect(screen.getByRole('link', { name })).toBeInTheDocument();
    }
  });

  it('/overview renders the real overview page (T56), not a stub', async () => {
    renderApp('/overview');
    expect(await screen.findByRole('region', { name: 'Tape' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Overview is not built yet' })).not.toBeInTheDocument();
  });

  it('/scan renders the real scan page (T44), not a stub', async () => {
    renderApp('/scan');
    expect(await screen.findByRole('group', { name: 'View' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Scan is not built yet' })).not.toBeInTheDocument();
  });

  it('/regime renders the real regime page (T49), not a stub', async () => {
    renderApp('/regime');
    expect(await screen.findByRole('group', { name: 'Filter' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Regime is not built yet' })).not.toBeInTheDocument();
  });

  it('/rotation renders the real rotation page (T51), not a stub', async () => {
    renderApp('/rotation');
    expect(await screen.findByRole('group', { name: 'Group' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Rotation is not built yet' })).not.toBeInTheDocument();
  });

  it('/flows renders the real flows page (T53), not a stub', async () => {
    renderApp('/flows');
    expect(await screen.findByRole('group', { name: 'Window' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Flows is not built yet' })).not.toBeInTheDocument();
  });

  it('the TopBar drops the asset dropdown on /scan and shows it again after navigating back to /', async () => {
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'SPX key levels' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Symbol SPX' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: 'Scan' }));
    // Keyed on the real page's own view toggle since T44 replaced the stub. The Symbol
    // dropdown is the dashboard control that must disappear; the View group is the scan
    // page's.
    await screen.findByRole('group', { name: 'View' });
    expect(screen.queryByRole('button', { name: 'Symbol SPX' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: 'Dashboard' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Symbol SPX' })).toBeInTheDocument());
  });
});
