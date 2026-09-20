/**
 * T51 -- `/rotation`, rendered end to end through the real query hooks against the MSW
 * handlers, the same shape as `Scan.test.tsx`. Fixtures are live backend responses recorded
 * against `http://localhost:8001` on 2026-09-09 (see `mocks/fixtures/scan/README.md`'s
 * "Rotation fixtures" section) -- the numbers asserted below are the real ones the API
 * returned, not values typed into a fixture.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes, createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { server } from '../../../mocks/server';
import { ThemeProvider } from '../../../theme/ThemeContext';
import { Rotation } from './Rotation';
import rotationSectorsFixture from '../mocks/fixtures/scan/rotation_sectors.json';
import rotationIndustriesFixture from '../mocks/fixtures/scan/rotation_industries_rsp.json';
import rotationAssetsFixture from '../mocks/fixtures/scan/rotation_assets.json';

// Same reasoning as `GexByStrike.test.tsx`/`RrgChart.test.tsx`: jsdom has no real <canvas> 2D
// context, and echarts' own canvas painter throws trying to use the null context it gets
// back. The chart's own option-building logic already has a dedicated unit-test file
// (`RrgChart.test.tsx`); this page test only needs the chart to mount without crashing the
// surrounding page.
vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-stub" />,
}));

function renderRotation(initialPath = '/rotation') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/rotation" element={<Rotation />} />
            <Route path="/" element={<p>dashboard stand-in</p>} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

async function awaitLoaded() {
  await waitFor(() => expect(screen.queryByText(/Computing relative rotation/)).not.toBeInTheDocument());
}

describe('Rotation page -- default view (sectors/SPY)', () => {
  it('renders the chart, the rank table and the breadth block from the live fixture', async () => {
    renderRotation();
    await awaitLoaded();
    expect(screen.getByRole('img', { name: /Relative rotation graph/ })).toBeInTheDocument();
    const table = await screen.findByRole('table', { name: /relative return and current quadrant/i });
    expect(within(table).getAllByRole('row')).toHaveLength(rotationSectorsFixture.symbols.length + 1);
    expect(screen.getByText('sector-level breadth')).toBeInTheDocument();
  });

  it('states the approximation and never claims parity with the proprietary JdK indicator', async () => {
    renderRotation();
    await awaitLoaded();
    const summary = screen.getByText('About this chart');
    summary.click();
    expect(screen.getAllByText(/rs_ratio_approx/).length).toBeGreaterThan(0);
    expect(screen.getByText(/100 \+ z\(rs, w\)/)).toBeInTheDocument();
    // The API's own honesty note, rendered verbatim, not paraphrased.
    expect(screen.getByText(rotationSectorsFixture.note)).toBeInTheDocument();
  });

  it('sorts the rank table by 4-week relative return, descending, by default', async () => {
    renderRotation();
    await awaitLoaded();
    const table = await screen.findByRole('table', { name: /relative return and current quadrant/i });
    expect(within(table).getByRole('columnheader', { name: '4w' })).toHaveAttribute('aria-sort', 'descending');
  });
});

describe('Rotation page -- deep links', () => {
  it('/rotation?group=industries&benchmark=RSP&weeks=6 drives the toolbar and the query', async () => {
    renderRotation('/rotation?group=industries&benchmark=RSP&weeks=6');
    await awaitLoaded();
    expect(
      within(screen.getByRole('group', { name: 'Group' })).getByRole('button', { name: 'Industries' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      within(screen.getByRole('group', { name: 'Benchmark' })).getByRole('button', { name: 'RSP' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      within(screen.getByRole('group', { name: 'Weeks' })).getByRole('button', { name: '6' }),
    ).toHaveAttribute('aria-pressed', 'true');
    const table = await screen.findByRole('table', { name: /relative return and current quadrant/i });
    expect(within(table).getAllByRole('row')).toHaveLength(rotationIndustriesFixture.symbols.length + 1);
  });

  it('survives a nav round trip to / and back', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter(
      [
        { path: '/rotation', element: <Rotation /> },
        { path: '/', element: <p>dashboard stand-in</p> },
      ],
      { initialEntries: ['/rotation?group=industries&benchmark=RSP&weeks=6'] },
    );
    render(
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <RouterProvider router={router} />
        </ThemeProvider>
      </QueryClientProvider>,
    );
    await awaitLoaded();
    expect(
      within(screen.getByRole('group', { name: 'Group' })).getByRole('button', { name: 'Industries' }),
    ).toHaveAttribute('aria-pressed', 'true');

    await act(async () => router.navigate('/'));
    expect(await screen.findByText('dashboard stand-in')).toBeInTheDocument();

    await act(async () => router.navigate(-1));
    // `findByRole` (not `getByRole`) here: it retries until the route has actually
    // re-rendered, where `awaitLoaded`'s absence check would pass trivially on the wrong
    // page (the dashboard stand-in never shows the loading text either).
    const groupToolbar = await screen.findByRole('group', { name: 'Group' });
    await awaitLoaded();
    expect(within(groupToolbar).getByRole('button', { name: 'Industries' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(
      within(screen.getByRole('group', { name: 'Benchmark' })).getByRole('button', { name: 'RSP' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      within(screen.getByRole('group', { name: 'Weeks' })).getByRole('button', { name: '6' }),
    ).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('Rotation page -- the assets group puts SPY on the crosshair', () => {
  it('renders SPY at exactly (100, 100) when benchmarked against itself', async () => {
    renderRotation('/rotation?group=assets&benchmark=SPY&weeks=6');
    await awaitLoaded();
    const spy = rotationAssetsFixture.symbols.find((s) => s.symbol === 'SPY')!;
    expect(spy.trail.every((p) => p.rs_ratio_approx === 100 && p.rs_momentum_approx === 100)).toBe(true);
    const table = await screen.findByRole('table', { name: /relative return and current quadrant/i });
    expect(within(table).getAllByRole('row')).toHaveLength(rotationAssetsFixture.symbols.length + 1);
  });
});

describe('Rotation page -- error and empty states', () => {
  it('renders an error state on a transport failure, never a raw response body', async () => {
    server.use(http.get('*/api/gex/scan/rotation', () => HttpResponse.error()));
    renderRotation();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not load sector rotation/i);
  });

  it('renders an empty state when the group has no symbols, never a blank page', async () => {
    server.use(
      http.get('*/api/gex/scan/rotation', () =>
        HttpResponse.json({
          group: 'sectors',
          benchmark: 'SPY',
          weeks: 6,
          w: 14,
          note: 'test',
          symbols: [],
          breadth: {
            label: 'sector-level breadth',
            equal_weight_ratio: null,
            equal_weight_ratio_change_20d: null,
            above_20d: 0,
            evaluated_20d: 0,
            above_50d: 0,
            evaluated_50d: 0,
          },
        }),
      ),
    );
    renderRotation();
    expect(await screen.findByRole('region', { name: /no symbols to show/i })).toBeInTheDocument();
  });
});
