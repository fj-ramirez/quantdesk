/**
 * T44 -- `/scan`, both views, rendered end to end through the real query hooks against the MSW
 * handlers (src/mocks), the same shape as `Report.test.tsx`.
 *
 * The fixtures are live backend responses recorded by T55, so the numbers asserted below are
 * the real ones the API returned on 2026-09-09, not values typed into a fixture. Two of the
 * null cases needed no fabrication at all: `breakouts.json` genuinely carries a `rate: null`
 * row and `trend.json` genuinely carries 19 `iv30: null` rows -- the common case, per
 * 07-ui.md. The empty-`open_breakouts` and non-empty-`excluded` variants are the hand-edited
 * copies (see `mocks/fixtures/scan/README.md`), because the live universe currently exhibits
 * neither.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { server } from '../../../mocks/server';
import { ThemeProvider } from '../../../theme/ThemeContext';
import { Scan } from './Scan';
import breakoutsFixture from '../mocks/fixtures/scan/breakouts.json';
import trendFixture from '../mocks/fixtures/scan/trend.json';
import openEmptyFixture from '../mocks/fixtures/scan/breakouts_open_empty.json';
import excludedFixture from '../mocks/fixtures/scan/breakouts_excluded.json';
import { expectPagedRows, forEveryPage } from '../../../test/pagination';

function renderScan(initialPath = '/scan') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/scan" element={<Scan />} />
            <Route path="/" element={<p>dashboard stand-in</p>} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

async function awaitBreakoutsLoaded() {
  await waitFor(() =>
    expect(screen.queryByText(/Scanning the universe/)).not.toBeInTheDocument(),
  );
}

async function awaitTrendLoaded() {
  await waitFor(() => expect(screen.queryByText(/Scoring the universe/)).not.toBeInTheDocument(), {
    timeout: 5000,
  });
}

describe('Scan page -- breakouts view', () => {
  it('renders a row per universe symbol from the live fixture', async () => {
    renderScan();
    await awaitBreakoutsLoaded();
    const table = await screen.findByRole('table', { name: /breakout continuation/i });
    // Header row plus one per summary.
    expectPagedRows(table, breakoutsFixture.summaries.length, 20, 'symbols');
  });

  it('renders a null rate as "n<5" rather than a dash or a zero', async () => {
    const nullRateRows = breakoutsFixture.summaries.filter((row) => row.rate === null);
    // Guards the fixture itself: if a re-record ever removes the null case, this test would
    // otherwise pass vacuously while covering nothing.
    expect(nullRateRows.length).toBeGreaterThan(0);

    renderScan();
    await awaitBreakoutsLoaded();
    // Counted across every page: the null-rate rows sort last, so they live on the final page.
    let seen = 0;
    forEveryPage(/breakout continuation by symbol/i, 'symbols', (page) => {
      seen += within(page).queryAllByText('n<5').length;
    });
    expect(seen).toBe(nullRateRows.length);
  });

  it('renders the empty open-breakouts panel as an empty state, not a blank column', async () => {
    server.use(
      http.get('*/api/gex/scan/breakouts', () => HttpResponse.json(openEmptyFixture)),
    );
    renderScan();
    await awaitBreakoutsLoaded();
    expect(await screen.findByText(/No open breakouts/i)).toBeInTheDocument();
    expect(screen.getByText(/is currently inside its/i)).toBeInTheDocument();
  });

  it('surfaces excluded symbols with their reason', async () => {
    server.use(http.get('*/api/gex/scan/breakouts', () => HttpResponse.json(excludedFixture)));
    renderScan();
    await awaitBreakoutsLoaded();
    const summary = await screen.findByText(/excluded for\s+gaps/i);
    fireEvent.click(summary);
    // Scoped to the disclosure: an excluded symbol also has a row in the summary table, so an
    // unscoped query matches twice and proves nothing about where it was rendered.
    const disclosure = summary.closest('details');
    expect(disclosure).not.toBeNull();
    const entry = within(disclosure as HTMLElement).getByText(
      excludedFixture.excluded[0].symbol,
    );
    expect(entry).toBeInTheDocument();
    expect(disclosure as HTMLElement).toHaveTextContent(excludedFixture.excluded[0].reason);
  });

  it('opens the detail drawer on a row click, focus-managed', async () => {
    renderScan();
    await awaitBreakoutsLoaded();
    const table = await screen.findByRole('table', { name: /breakout continuation/i });
    const firstDataRow = within(table).getAllByRole('row')[1];
    firstDataRow.focus();
    fireEvent.click(firstDataRow);
    // T66: BreakoutDetail is now mounted through ui/DetailDrawer, which owns the panel's
    // dialog chrome and moves focus inside it on open -- not the old unmanaged `region`.
    const dialog = await screen.findByRole('dialog', { name: /breakout detail/i });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(firstDataRow).toHaveFocus();
  });

  it('opens the detail drawer from the keyboard with Enter', async () => {
    renderScan();
    await awaitBreakoutsLoaded();
    const table = await screen.findByRole('table', { name: /breakout continuation/i });
    const firstDataRow = within(table).getAllByRole('row')[1];
    // Focusable, per 07-ui.md's keyboard acceptance criterion -- not merely clickable.
    expect(firstDataRow).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(firstDataRow, { key: 'Enter' });
    expect(await screen.findByRole('dialog', { name: /breakout detail/i })).toBeInTheDocument();
  });

  it('exposes sortable headers as buttons and toggles direction in the URL', async () => {
    renderScan();
    await awaitBreakoutsLoaded();
    const table = await screen.findByRole('table', { name: /breakout continuation/i });
    const rateHeader = within(table).getByRole('button', { name: /rate/i });
    // Default: rate desc, applied without writing to the URL (see Scan.tsx's docstring).
    expect(within(table).getByRole('columnheader', { name: /rate/i })).toHaveAttribute(
      'aria-sort',
      'descending',
    );
    fireEvent.click(rateHeader);
    await waitFor(() =>
      expect(within(table).getByRole('columnheader', { name: /rate/i })).toHaveAttribute(
        'aria-sort',
        'ascending',
      ),
    );
  });
});

describe('Scan page -- trend view', () => {
  it('deep-links to the trend view via ?view=trend', async () => {
    renderScan('/scan?view=trend');
    await awaitTrendLoaded();
    expect(await screen.findByRole('table', { name: /trend and chop/i })).toBeInTheDocument();
  });

  it('renders a null iv30 as a dash with an explanation, never a zero', async () => {
    const nullIvRows = trendFixture.rows.filter((row) => row.iv30 === null);
    expect(nullIvRows.length).toBeGreaterThan(0);

    renderScan('/scan?view=trend');
    await awaitTrendLoaded();
    const dashes = screen.getAllByTitle('No option chain tracked for this symbol');
    // One per null cell: iv30 and iv_rv_ratio are null together for a chainless symbol.
    expect(dashes.length).toBeGreaterThanOrEqual(nullIvRows.length);
    expect(dashes[0]).toHaveTextContent('—');
  });

  it('states that IV/RV is not part of the composite', async () => {
    renderScan('/scan?view=trend');
    await awaitTrendLoaded();
    expect(screen.getByText(/IV\/RV is shown but is not part of it/i)).toBeInTheDocument();
  });

  it('shows a loading line that explains why the trend scan is slow', async () => {
    renderScan('/scan?view=trend');
    // Asserted before the await: five seconds of blank table reads as a broken page, so the
    // reason has to be on screen while the request is in flight.
    expect(screen.getByText(/reads every tracked option chain/i)).toBeInTheDocument();
    await awaitTrendLoaded();
  });
});

describe('Scan page -- URL state', () => {
  it('restores every breakout parameter from a deep link', async () => {
    renderScan('/scan?n=55&k=10&lookback=252&sort=rate&dir=asc');
    await awaitBreakoutsLoaded();
    const toolbar = screen.getByRole('group', { name: 'N' });
    expect(within(toolbar).getByRole('button', { name: '55' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(
      within(screen.getByRole('group', { name: 'k' })).getByRole('button', { name: '10' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      within(screen.getByRole('group', { name: 'Lookback' })).getByRole('button', {
        name: '252',
      }),
    ).toHaveAttribute('aria-pressed', 'true');
    const table = await screen.findByRole('table', { name: /breakout continuation/i });
    expect(within(table).getByRole('columnheader', { name: /rate/i })).toHaveAttribute(
      'aria-sort',
      'ascending',
    );
  });

  it('falls back to the default for an invalid n while leaving the other params intact', async () => {
    renderScan('/scan?n=999&k=10&lookback=252');
    await awaitBreakoutsLoaded();
    expect(
      within(screen.getByRole('group', { name: 'N' })).getByRole('button', { name: '20' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      within(screen.getByRole('group', { name: 'k' })).getByRole('button', { name: '10' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(
      within(screen.getByRole('group', { name: 'Lookback' })).getByRole('button', {
        name: '252',
      }),
    ).toHaveAttribute('aria-pressed', 'true');
  });

  it('hides the breakout-only controls on the trend view', async () => {
    renderScan('/scan?view=trend');
    await awaitTrendLoaded();
    expect(screen.queryByRole('group', { name: 'N' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Lookback' })).not.toBeInTheDocument();
  });
});
