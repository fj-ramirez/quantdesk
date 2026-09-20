/**
 * T53 -- `/flows`, rendered end to end through the real query hooks against the MSW handlers,
 * the same shape as `Rotation.test.tsx`/`Regime.test.tsx`. `flows_5/20/60.json` are the real
 * live backend response recorded 2026-09-10 (see `mocks/fixtures/scan/README.md`'s "Flows
 * fixtures" section) -- the numbers asserted below (all null, one short-history row, four
 * unsupported symbols) are the genuine current state, not values typed into a fixture.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { server } from '../mocks/server';
import { ThemeProvider } from '../../../theme/ThemeContext';
import { Flows } from './Flows';
import { formatBarsThrough } from '../../../lib/time';
import flows20Fixture from '../mocks/fixtures/scan/flows_20.json';
import flows60Fixture from '../mocks/fixtures/scan/flows_60.json';

// Same reasoning as `Rotation.test.tsx`: jsdom has no real <canvas>, and FlowBars/FlowSparkline
// each already have their own dedicated option-building unit tests.
vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-stub" />,
}));

function renderFlows(initialPath = '/flows') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/flows" element={<Flows />} />
            <Route path="/" element={<p>dashboard stand-in</p>} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

async function awaitLoaded() {
  await waitFor(() => expect(screen.queryByText(/Reading ETF flow data/)).not.toBeInTheDocument());
}

describe('Flows page -- the live, all-empty state is the normal case', () => {
  it('renders the honest empty chart, the no-flow-data list and the short-history row, never a zero bar', async () => {
    renderFlows();
    await awaitLoaded();

    // FlowBars: every symbol is null today, so it must render its own EmptyState, not a
    // chart -- FlowBars.test.tsx already proves this at the component level; this asserts the
    // page actually reaches that state via the real query.
    expect(
      screen.getByRole('region', { name: /No fund has a computed flow yet/i }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('echarts-stub')).not.toBeInTheDocument();

    // The four unsupported funds, each with its real reason string.
    const noFlowSection = screen.getByRole('region', { name: 'No flow data' });
    for (const row of flows20Fixture.no_flow_data) {
      const item = within(noFlowSection).getByText(row.symbol).closest('li')!;
      expect(item).toHaveTextContent(row.reason);
    }

    // XLK's real short-history row, in the compact table, not a fabricated number.
    const table = screen.getByRole('table', { name: /flow percent and trend/i });
    const xlkRow = within(table).getByText('XLK').closest('tr')!;
    expect(within(xlkRow).getByText(/history since 2026-09-08/)).toBeInTheDocument();

    // A plain "no data yet" row renders a dash-ish message too, never "0%" or "+0.00%".
    const spyRow = within(table).getByText('SPY').closest('tr')!;
    expect(within(spyRow).queryByText(/%/)).not.toBeInTheDocument();
    expect(within(spyRow).getByText(/no data yet/)).toBeInTheDocument();
  });

  it('reads the family source banner honestly, naming the lag rather than claiming today\'s freshness', async () => {
    renderFlows();
    await awaitLoaded();
    const banner = screen.getByRole('region', { name: 'Flow data sources' });
    expect(within(banner).getByText(/issuer's own as-of date, not today's session/i)).toBeInTheDocument();
    expect(within(banner).getByText('SPDR')).toBeInTheDocument();
    expect(within(banner).getByText(formatBarsThrough('2026-09-08'))).toBeInTheDocument();
    expect(within(banner).getByText('iShares')).toBeInTheDocument();
    expect(within(banner).getByText(formatBarsThrough('2026-09-09'))).toBeInTheDocument();
  });
});

describe('Flows page -- window toolbar and deep links', () => {
  it('defaults to the 20d window', async () => {
    renderFlows();
    await awaitLoaded();
    expect(
      within(screen.getByRole('group', { name: 'Window' })).getByRole('button', { name: '20d' }),
    ).toHaveAttribute('aria-pressed', 'true');
  });

  it('/flows?window=60 deep-links and round-trips', async () => {
    renderFlows('/flows?window=60');
    await awaitLoaded();
    expect(
      within(screen.getByRole('group', { name: 'Window' })).getByRole('button', { name: '60d' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/Net flow, percent of AUM \(60d\)/)).toBeInTheDocument();
    const table = screen.getByRole('table', { name: /flow percent and trend/i });
    expect(within(table).getAllByRole('row')).toHaveLength(flows60Fixture.symbols.length + 1);
  });

  it('falls back to the default window instead of throwing on an invalid ?window=', async () => {
    renderFlows('/flows?window=999');
    await awaitLoaded();
    expect(
      within(screen.getByRole('group', { name: 'Window' })).getByRole('button', { name: '20d' }),
    ).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('Flows page -- error state', () => {
  it('renders an error state on a transport failure, never a raw response body', async () => {
    server.use(http.get('*/api/gex/scan/flows', () => HttpResponse.error()));
    renderFlows();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not load ETF flow data/i);
  });
});
