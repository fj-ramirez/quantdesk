/**
 * T63: renamed from `TopBar.test.tsx` — `TopBar` was renamed `ContextBar` (same route-aware
 * behavior, see that file's own docstring). Every assertion below is unchanged from the old
 * file; only the import and the component name changed.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { ThemeProvider } from '../../../../theme/ThemeContext';
import { ContextBar } from './ContextBar';

function renderContextBar(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <ContextBar />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe('ContextBar route-awareness', () => {
  it('renders the dashboard controls on /dashboard', async () => {
    // The dashboard moved off `/` on 2026-09-10 when Overview became the landing page; `/` is
    // now a universe page and gets the scan toolbar (asserted in its own test below).
    renderContextBar('/gex/dashboard');
    expect(screen.getByRole('button', { name: 'Symbol SPX' })).toBeInTheDocument();
    expect(screen.getByText('Expiry')).toBeInTheDocument();
    expect(screen.getByText('Snapshot')).toBeInTheDocument();
  });

  it('opens the asset dropdown on click and lets a click select a different symbol', () => {
    renderContextBar('/gex/dashboard');
    const trigger = screen.getByRole('button', { name: 'Symbol SPX' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

    fireEvent.click(trigger);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Core' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Extended' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: 'QQQ' }));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Symbol QQQ' })).toBeInTheDocument();
  });

  it('renders the dashboard controls on /report and /history too', () => {
    renderContextBar('/gex/report');
    expect(screen.getByRole('button', { name: 'Symbol SPX' })).toBeInTheDocument();
    const { unmount } = renderContextBar('/gex/history');
    expect(screen.getAllByRole('button', { name: 'Symbol SPX' }).length).toBeGreaterThan(0);
    unmount();
  });

  it('the asset dropdown, expiry filter and snapshot selector are absent on /scan', async () => {
    renderContextBar('/gex/scan');
    expect(screen.queryByRole('button', { name: 'Symbol SPX' })).not.toBeInTheDocument();
    expect(screen.queryByText('Expiry')).not.toBeInTheDocument();
    expect(screen.queryByText('Snapshot')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Loading bars freshness/)).not.toBeInTheDocument());
  });

  it('renders the bars-freshness toolbar instead, on every scan-family route', async () => {
    // `/` leads this list: Overview became the landing page on 2026-09-10, so the root route
    // is a universe page and must render the scan toolbar rather than the symbol controls.
    for (const path of ['/gex', '/gex/scan', '/gex/regime', '/gex/rotation', '/gex/flows', '/gex/overview']) {
      const { unmount } = renderContextBar(path);
      expect(screen.queryByRole('button', { name: 'Symbol SPX' })).not.toBeInTheDocument();
      await waitFor(() => expect(screen.queryByText(/Loading bars freshness|Bars through|Bars freshness unavailable/)).toBeInTheDocument());
      unmount();
    }
  });

  it('the theme toggle is present on both a symbol route and a scan-family route', () => {
    const { unmount } = renderContextBar('/gex/dashboard');
    expect(screen.getByRole('button', { name: /Switch to (dark|light) theme/ })).toBeInTheDocument();
    unmount();
    renderContextBar('/gex/scan');
    expect(screen.getByRole('button', { name: /Switch to (dark|light) theme/ })).toBeInTheDocument();
  });
});
