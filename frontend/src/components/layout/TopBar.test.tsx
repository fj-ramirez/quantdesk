/**
 * T55 — TopBar becomes route-aware: the dashboard controls (symbol switcher, expiry filter,
 * snapshot selector, freshness badge) render on a symbol page and are absent on a scan-family
 * route, where a much smaller bars-freshness toolbar renders instead.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { ThemeProvider } from '../../theme/ThemeContext';
import { TopBar } from './TopBar';

function renderTopBar(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <TopBar />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe('TopBar route-awareness', () => {
  it('renders the dashboard controls on /', async () => {
    renderTopBar('/');
    expect(screen.getByRole('group', { name: 'Symbol' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Symbol (extended)' })).toBeInTheDocument();
    expect(screen.getByText('Expiry')).toBeInTheDocument();
    expect(screen.getByText('Snapshot')).toBeInTheDocument();
  });

  it('renders the dashboard controls on /report and /history too', () => {
    renderTopBar('/report');
    expect(screen.getByRole('group', { name: 'Symbol' })).toBeInTheDocument();
    const { unmount } = renderTopBar('/history');
    expect(screen.getAllByRole('group', { name: 'Symbol' }).length).toBeGreaterThan(0);
    unmount();
  });

  it('the symbol switcher, expiry filter and snapshot selector are absent on /scan', async () => {
    renderTopBar('/scan');
    expect(screen.queryByRole('group', { name: 'Symbol' })).not.toBeInTheDocument();
    expect(screen.queryByText('Expiry')).not.toBeInTheDocument();
    expect(screen.queryByText('Snapshot')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Loading bars freshness/)).not.toBeInTheDocument());
  });

  it('renders the bars-freshness toolbar instead, on every scan-family route', async () => {
    for (const path of ['/scan', '/regime', '/rotation', '/flows', '/overview']) {
      const { unmount } = renderTopBar(path);
      expect(screen.queryByRole('group', { name: 'Symbol' })).not.toBeInTheDocument();
      await waitFor(() => expect(screen.queryByText(/Loading bars freshness|Bars through|Bars freshness unavailable/)).toBeInTheDocument());
      unmount();
    }
  });

  it('the theme toggle is present on both a symbol route and a scan-family route', () => {
    const { unmount } = renderTopBar('/');
    expect(screen.getByRole('button', { name: /Switch to (dark|light) theme/ })).toBeInTheDocument();
    unmount();
    renderTopBar('/scan');
    expect(screen.getByRole('button', { name: /Switch to (dark|light) theme/ })).toBeInTheDocument();
  });
});
