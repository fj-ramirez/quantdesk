/**
 * T159: the broker chart page. `PriceChart` is replaced by a stub that prints what it was
 * given (lightweight-charts draws to canvas, which jsdom lacks; its builders are tested in
 * `chartData.test.ts`). What is tested here is the page's job: URL state, and that a replay is
 * really drawn as of `end` and says so.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { ChartResponse } from '../api/types';
import type { Layer } from '../state/chartParams';

vi.mock('../components/PriceChart', () => ({
  PriceChart: ({ data, layers }: { data: ChartResponse; layers: ReadonlySet<Layer> }) => (
    <div data-testid="price-chart">
      <span data-testid="bars">{data.bars.length}</span>
      <span data-testid="last-bar">{data.bars[data.bars.length - 1]?.time}</span>
      <span data-testid="forming">{String(data.bars[data.bars.length - 1]?.forming)}</span>
      <span data-testid="layers">{[...layers].sort().join(',')}</span>
    </div>
  ),
}));

const { Chart } = await import('./Chart');

function LocationProbe() {
  return <span data-testid="search">{useLocation().search}</span>;
}

const search = () => screen.getByTestId('search').textContent ?? '';

function renderPage(path = '/broker') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/broker" element={<><Chart /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Chart page', () => {
  it('renders the live chart with the default symbol, layers and a live badge', async () => {
    renderPage();
    expect(await screen.findByTestId('price-chart')).toBeInTheDocument();
    expect(screen.getByTestId('bars')).toHaveTextContent('120');
    expect(screen.getByTestId('layers')).toHaveTextContent('breaks,levels,structure,sweeps,zones');
    expect(screen.getByText(/Live, refreshed every minute/)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'GEX levels' })).toHaveTextContent('4,140.00');
  });

  it('draws a replay as of `end`, flags the cut bar as forming, and says it is not live', async () => {
    renderPage('/broker?end=2026-10-13T18:07:00.000Z');
    await screen.findByTestId('price-chart');
    expect(screen.getByTestId('last-bar')).toHaveTextContent('2026-10-13T18:00:00.000Z');
    expect(screen.getByTestId('forming')).toHaveTextContent('true');
    expect(screen.getByText(/Replay: only what was knowable/)).toBeInTheDocument();
  });

  it('steps one bar back from the newest closed bar, and back to live', async () => {
    renderPage('/broker?end=2026-10-13T18:07:00.000Z');
    await screen.findByTestId('price-chart');
    // Newest closed bar is 17:45; ending there leaves 17:30 as the newest closed bar.
    fireEvent.click(screen.getByRole('button', { name: 'One bar back' }));
    await waitFor(() => expect(search()).toContain('end=2026-10-13T17%3A45%3A00.000Z'));
    fireEvent.click(screen.getByRole('button', { name: 'Back to live' }));
    await waitFor(() => expect(search()).not.toContain('end='));
  });

  it('keeps timeframe and layers in the URL, and leaves defaults out', async () => {
    renderPage();
    await screen.findByTestId('price-chart');
    fireEvent.click(screen.getByRole('button', { name: 'H1' }));
    await waitFor(() => expect(search()).toContain('tf=H1'));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Sweeps' }));
    await waitFor(() => expect(search()).toContain('layers=levels%2Czones%2Cstructure%2Cbreaks'));
    expect(screen.getByTestId('layers')).toHaveTextContent('breaks,levels,structure,zones');
  });

  it('flags a proxy pair beside its levels', async () => {
    renderPage('/broker?symbol=NAS100.fs');
    await screen.findByTestId('price-chart');
    expect(screen.getByText(/Proxy: NAS100.fs tracks a different index than QQQ/)).toBeInTheDocument();
  });

  it('says a symbol with no desk pair has no levels, instead of drawing none silently', async () => {
    renderPage('/broker?symbol=EURUSD');
    await screen.findByTestId('price-chart');
    expect(screen.getByText(/No desk pair/)).toBeInTheDocument();
  });

  it('renders an unknown symbol as a message, never a raw error body', async () => {
    renderPage('/broker?symbol=NOPE');
    expect(await screen.findByText('NOPE is not a configured broker symbol.')).toBeInTheDocument();
    expect(screen.queryByText(/detail/)).not.toBeInTheDocument();
  });
});
