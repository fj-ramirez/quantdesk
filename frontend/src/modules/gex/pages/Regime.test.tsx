/**
 * T49 -- `/regime`, rendered end to end through the real query hooks against the MSW
 * handlers, the same shape as `Rotation.test.tsx`. Fixtures are live backend responses
 * recorded against `http://localhost:8001` on 2026-09-09 (see
 * `mocks/fixtures/scan/README.md`'s "Regime fixtures" section) -- the numbers asserted below
 * are the real ones the API returned, not values typed into a fixture.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { server } from '../../../mocks/server';
import { ThemeProvider } from '../../../theme/ThemeContext';
import { Regime } from './Regime';
import regimeFixture from '../mocks/fixtures/scan/regime.json';
import regimeZeroDteFixture from '../mocks/fixtures/scan/regime_zero_dte.json';
import regimeEmptyFixture from '../mocks/fixtures/scan/regime_empty.json';

function DashboardStandIn() {
  const [params] = useSearchParams();
  return <p>dashboard stand-in: {params.toString()}</p>;
}

function renderRegime(initialPath = '/regime') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/regime" element={<Regime />} />
            <Route path="/" element={<DashboardStandIn />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

async function awaitLoaded() {
  await waitFor(() => expect(screen.queryByText(/Scoring dealer positioning/)).not.toBeInTheDocument());
}

describe('Regime page -- default view (filter=ALL)', () => {
  it('renders the toolbar and one row per fixture symbol', async () => {
    renderRegime();
    await awaitLoaded();
    expect(
      within(screen.getByRole('group', { name: 'Filter' })).getByRole('button', { name: 'All' }),
    ).toHaveAttribute('aria-pressed', 'true');
    const table = await screen.findByRole('table', { name: /dealer positioning regime/i });
    expect(within(table).getAllByRole('row')).toHaveLength(regimeFixture.rows.length + 1);
  });

  it('renders T54 cross-asset strip above the board', async () => {
    // Was "renders the RegimeStrip slot as empty" while T54 did not exist. T54 has landed and
    // the supervisor wired it in, so the slot is now filled -- 07-ui.md puts the strip at the
    // top of both `/regime` and `/scan`. The "never a placeholder row of dashes" half of the
    // original intent is kept below, and is the part that actually mattered.
    renderRegime();
    await awaitLoaded();

    const strip = await screen.findByRole('region', { name: /cross-asset regime strip/i });
    expect(strip).toBeInTheDocument();

    // The strip owns its own empty states: a tile with no data reads "n/a", never a bare dash
    // standing in for a number the app does not have.
    expect(within(strip).queryByText(/^-+$/)).not.toBeInTheDocument();
  });

  it('sorts the board into continuation/mixed/fade/noise-dominated/stale group order by default', async () => {
    renderRegime();
    await awaitLoaded();
    const table = await screen.findByRole('table', { name: /dealer positioning regime/i });
    const bodyRows = within(table).getAllByRole('row').slice(1); // drop the header row
    const groups = bodyRows.map((row) => {
      const text = row.textContent ?? '';
      if (text.includes('noise-dominated')) return 'noise-dominated';
      if (text.includes('stale')) return 'stale';
      if (text.includes('continuation')) return 'continuation';
      if (text.includes('mixed')) return 'mixed';
      if (text.includes('fade')) return 'fade';
      return 'unknown';
    });
    const rank: Record<string, number> = { continuation: 0, mixed: 1, fade: 2, 'noise-dominated': 3, stale: 4 };
    const ranks = groups.map((g) => rank[g]);
    const sorted = [...ranks].sort((a, b) => a - b);
    expect(ranks).toEqual(sorted);
  });
});

describe('Regime page -- symbol links carry the page filter', () => {
  it('a symbol click lands on /?symbol=...&filter=... with the current filter', async () => {
    renderRegime('/regime?filter=ZERO_DTE');
    await awaitLoaded();
    const table = await screen.findByRole('table', { name: /dealer positioning regime/i });
    const link = within(table).getByRole('link', { name: 'SPX' });
    const href = link.getAttribute('href')!;
    const params = new URLSearchParams(href.split('?')[1]);
    expect(params.get('symbol')).toBe('SPX');
    expect(params.get('filter')).toBe('ZERO_DTE');
  });
});

describe('Regime page -- deep links', () => {
  it('/regime?filter=ZERO_DTE drives the toolbar and renders the ZERO_DTE fixture', async () => {
    renderRegime('/regime?filter=ZERO_DTE');
    await awaitLoaded();
    expect(
      within(screen.getByRole('group', { name: 'Filter' })).getByRole('button', { name: '0DTE' }),
    ).toHaveAttribute('aria-pressed', 'true');
    const table = await screen.findByRole('table', { name: /dealer positioning regime/i });
    expect(within(table).getAllByRole('row')).toHaveLength(regimeZeroDteFixture.rows.length + 1);
    // Every row in the live ZERO_DTE snapshot is noise-dominated or (for the five also-stale
    // symbols) stale -- no genuine verdict survives the post-close 0/483 case (plan 03).
    expect(within(table).queryByText('continuation')).not.toBeInTheDocument();
    expect(within(table).queryByText('mixed')).not.toBeInTheDocument();
    expect(within(table).queryByText('fade')).not.toBeInTheDocument();
  });
});

describe('Regime page -- error and empty states', () => {
  it('renders an error state on a transport failure, never a raw response body', async () => {
    server.use(http.get('*/api/gex/scan/regime', () => HttpResponse.error()));
    renderRegime();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not load the regime board/i);
  });

  it('renders an empty state when the board has no rows, never a blank page', async () => {
    server.use(http.get('*/api/gex/scan/regime', () => HttpResponse.json({ filter: 'ALL', rows: [] })));
    renderRegime();
    expect(await screen.findByRole('region', { name: /no symbols scored/i })).toBeInTheDocument();
  });
});

describe('Regime page -- a freshly deployed, empty database', () => {
  /**
   * The state of every first deployment: the schema is migrated but no 16:20 capture has run,
   * so `GET /api/gex/scan/regime` answers with a full set of rows in which every GEX-derived field
   * is null -- `RegimeRowOut.missing` in `app/modules/gex/api/scan.py`. `regime_empty.json` is that exact
   * response, recorded from the production stack against an empty database on 2026-09-19.
   *
   * This used to take the whole route down. `RegimeSymbolRow` declared `positioning`
   * non-nullable, so `toRegimeRows` read `row.positioning.ratio` and threw
   * "Cannot read properties of null (reading 'ratio')" inside the page's `useMemo` -- a blank
   * screen and a console stack, on the very first thing a new install shows you. TASKS.md T37
   * is explicit that this state "is the expected state on first run" and must not be presented
   * as a failure.
   */
  it('renders the board instead of crashing, and says why the rows are empty', async () => {
    server.use(
      http.get('*/api/gex/scan/regime', () => HttpResponse.json(regimeEmptyFixture)),
    );
    renderRegime();
    await awaitLoaded();

    // The table is present with its rows, not an error boundary or a blank page.
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'SPX' })).toBeInTheDocument();

    // And the server's own sentence is what explains the emptiness -- rendered verbatim,
    // never reworded, per the field's contract.
    expect(
      screen.getAllByText('no snapshot captured yet for this symbol').length,
    ).toBeGreaterThan(0);
  });

  it('does not label a never-measured symbol "noise-dominated"', async () => {
    // "we measured and the signal was too small to trust" and "we never measured" are
    // different facts. Reporting the first when the second is true is a lie about the data.
    server.use(
      http.get('*/api/gex/scan/regime', () => HttpResponse.json(regimeEmptyFixture)),
    );
    renderRegime();
    await awaitLoaded();

    expect(screen.queryByText('noise-dominated')).not.toBeInTheDocument();
  });
});
