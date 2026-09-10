/**
 * T55 — TopBar becomes route-aware: the dashboard controls (asset dropdown, expiry filter,
 * snapshot selector, freshness badge) render on a symbol page and are absent on a scan-family
 * route, where a much smaller bars-freshness toolbar renders instead.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    expect(screen.getByRole('button', { name: 'Symbol SPX' })).toBeInTheDocument();
    expect(screen.getByText('Expiry')).toBeInTheDocument();
    expect(screen.getByText('Snapshot')).toBeInTheDocument();
  });

  it('opens the asset dropdown on click and lets a click select a different symbol', () => {
    renderTopBar('/');
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
    renderTopBar('/report');
    expect(screen.getByRole('button', { name: 'Symbol SPX' })).toBeInTheDocument();
    const { unmount } = renderTopBar('/history');
    expect(screen.getAllByRole('button', { name: 'Symbol SPX' }).length).toBeGreaterThan(0);
    unmount();
  });

  it('the asset dropdown, expiry filter and snapshot selector are absent on /scan', async () => {
    renderTopBar('/scan');
    expect(screen.queryByRole('button', { name: 'Symbol SPX' })).not.toBeInTheDocument();
    expect(screen.queryByText('Expiry')).not.toBeInTheDocument();
    expect(screen.queryByText('Snapshot')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Loading bars freshness/)).not.toBeInTheDocument());
  });

  it('renders the bars-freshness toolbar instead, on every scan-family route', async () => {
    for (const path of ['/scan', '/regime', '/rotation', '/flows', '/overview']) {
      const { unmount } = renderTopBar(path);
      expect(screen.queryByRole('button', { name: 'Symbol SPX' })).not.toBeInTheDocument();
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
