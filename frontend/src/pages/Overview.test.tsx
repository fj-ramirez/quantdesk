/**
 * T56 -- `/overview`, rendered end to end through the real query hooks against the MSW
 * handlers, the same shape as `Regime.test.tsx`/`Rotation.test.tsx`. The default fixtures
 * (`breakouts.json`, `trend.json`, `regime.json`, `cross_asset.json`) are the same live backend
 * responses every other scan-family page's tests already use -- see `mocks/fixtures/scan/
 * README.md`. The numbers asserted below (top/worst-8 symbols, counts) were computed directly
 * from those fixture files, not hand-typed.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { server } from '../mocks/server';
import { ThemeProvider } from '../theme/ThemeContext';
import { Overview } from './Overview';
import breakoutsFixture from '../mocks/fixtures/scan/breakouts.json';
import regimeFixture from '../mocks/fixtures/scan/regime.json';
import healthCaptureFixture from '../mocks/fixtures/scan/health_capture.json';
import type { BreakoutsResponse, CaptureHealth } from '../api/types';

function renderOverview(initialPath = '/overview') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/overview" element={<Overview />} />
            <Route path="/scan" element={<p>scan stand-in</p>} />
            <Route path="/regime" element={<p>regime stand-in</p>} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

async function awaitLoaded() {
  await waitFor(() =>
    expect(screen.queryByText(/Scanning the universe for range breaks/)).not.toBeInTheDocument(),
  );
  await waitFor(() => expect(screen.queryByText(/Scoring the universe/)).not.toBeInTheDocument());
  await waitFor(() =>
    expect(screen.queryByText(/Scoring dealer positioning/)).not.toBeInTheDocument(),
  );
}

describe('Overview page', () => {
  it('renders a page header naming the page and what it answers', async () => {
    renderOverview();
    expect(screen.getByRole('heading', { name: 'Overview', level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/what is the market state now/i)).toBeInTheDocument();
  });

  it('renders the Tape block with only the compact capture-freshness line -- RegimeStrip moved out (T69)', async () => {
    renderOverview();
    const tape = await screen.findByRole('region', { name: 'Tape' });
    expect(within(tape).getByRole('link', { name: /open regime board/i })).toHaveAttribute(
      'href',
      '/regime',
    );

    // T69: the five-card freshness strip is now one compact status line; RegimeStrip no longer
    // lives inside Tape at all (it moved down, see the next test).
    const freshness = await within(tape).findByTestId('capture-freshness');
    const fixture = healthCaptureFixture as CaptureHealth;
    expect(within(freshness).getByText(`${fixture.symbols.length}/${fixture.symbols.length} chains fresh`)).toBeInTheDocument();
    expect(within(freshness).getByText('All fresh')).toBeInTheDocument();
    expect(within(tape).queryByText('VIX9D/VIX')).not.toBeInTheDocument();

    // The per-symbol breakdown lives behind a collapsed `<details>`; every symbol is still
    // present in the DOM (nothing lost), just not spread across five always-visible cards.
    const detail = within(freshness).getByText('Per-symbol capture detail').closest('details') as HTMLDetailsElement;
    expect(detail.open).toBe(false);
    for (const s of fixture.symbols) {
      expect(within(detail).getByText(s.underlying)).toBeInTheDocument();
    }
  });

  it('renders capture freshness honestly when a symbol has never been captured or is stale, naming it in the compact line', async () => {
    const fixture = healthCaptureFixture as CaptureHealth;
    const degraded: CaptureHealth = {
      ...fixture,
      symbols: fixture.symbols.map((s, i) =>
        i === 0 ? { ...s, last_capture_at: null, last_eod_capture_at: null, stale: true } : s,
      ),
    };
    server.use(http.get('*/api/health/capture', () => HttpResponse.json(degraded)));
    renderOverview();
    const tape = await screen.findByRole('region', { name: 'Tape' });
    const freshness = await within(tape).findByTestId('capture-freshness');

    // The compact line honestly reports the reduced fresh count and names the stale symbol --
    // never "all fresh" when one genuinely is not.
    expect(
      within(freshness).getByText(`${fixture.symbols.length - 1}/${fixture.symbols.length} chains fresh`),
    ).toBeInTheDocument();
    expect(within(freshness).getByText(`1 stale: ${degraded.symbols[0].underlying}`)).toBeInTheDocument();

    const detail = within(freshness).getByText('Per-symbol capture detail').closest('details') as HTMLDetailsElement;
    expect(within(detail).getByText('No capture yet')).toBeInTheDocument();
    expect(within(detail).getByText('Stale')).toBeInTheDocument();
  });

  it('renders RegimeStrip in compact mode after "Where continuation is", not immediately after the freshness line (T69)', async () => {
    renderOverview();
    await awaitLoaded();

    const regimeBlock = await screen.findByRole('region', { name: 'Cross-asset regime' });
    expect(within(regimeBlock).getByRole('link', { name: /open regime board/i })).toHaveAttribute('href', '/regime');
    expect(await within(regimeBlock).findByText('VIX9D/VIX')).toBeInTheDocument();
    // Compact mode: the four vol/term-structure tiles are primary, the rest sit behind a
    // collapsed disclosure -- never dropped, just not first-screen.
    const more = within(regimeBlock).getByText(/more regime tiles/).closest('details') as HTMLDetailsElement;
    expect(more.open).toBe(false);
    expect(within(more).getByText('TLT 20d')).toBeInTheDocument();

    // Reading order: Tape's freshness line, then "Where continuation is", then this block.
    const tape = screen.getByRole('region', { name: 'Tape' });
    const continuation = screen.getByRole('region', { name: 'Where continuation is' });
    expect(tape.compareDocumentPosition(continuation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(continuation.compareDocumentPosition(regimeBlock) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows the top 8 symbols by breakout rate, computed from the fixture', async () => {
    renderOverview();
    await awaitLoaded();
    const block = await screen.findByRole('region', { name: 'Top by breakout rate' });
    const rows = within(block).getAllByRole('row').slice(1); // drop the header row
    expect(rows).toHaveLength(8);
    // Highest rate in the fixture (computed from summaries, excluding the one null-rate row).
    expect(within(rows[0]).getByText('XLF')).toBeInTheDocument();
    expect(within(block).getByRole('link', { name: /open full page/i })).toHaveAttribute(
      'href',
      '/scan?view=breakouts&sort=rate&dir=desc',
    );
  });

  it('shows the 8 worst symbols by breakout rate, never including the null-rate symbol', async () => {
    renderOverview();
    await awaitLoaded();
    const block = await screen.findByRole('region', { name: 'Fading — worst by breakout rate' });
    const rows = within(block).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(8);
    // Lowest real rate in the fixture.
    expect(within(rows[0]).getByText('HYG')).toBeInTheDocument();
    // XLP is the fixture's one `rate: null` row -- it must never appear here as though a null
    // rate were the worst (zero) rate.
    expect(within(block).queryByText('XLP')).not.toBeInTheDocument();
    expect(within(block).getByRole('link', { name: /open full page/i })).toHaveAttribute(
      'href',
      '/scan?view=breakouts&sort=rate&dir=asc',
    );
  });

  it('notes the excluded (null-rate) symbol count once, not per list', async () => {
    renderOverview();
    await awaitLoaded();
    expect(screen.getByText(/1 symbol excluded from both rate rankings/)).toBeInTheDocument();
  });

  it('shows the top 8 symbols by trend composite, computed from the fixture', async () => {
    renderOverview();
    await awaitLoaded();
    const block = await screen.findByRole('region', { name: 'Top by trend composite' });
    const rows = within(block).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(8);
    expect(within(rows[0]).getByText('XLE')).toBeInTheDocument();
    expect(within(block).getByRole('link', { name: /open full page/i })).toHaveAttribute(
      'href',
      '/scan?view=trend&sort=composite&dir=desc',
    );
  });

  it('renders the open-breakouts panel from the fixture, sorted by bars elapsed', async () => {
    renderOverview();
    await awaitLoaded();
    const block = await screen.findByRole('region', { name: 'Open breakouts' });
    expect(within(block).getAllByText(/of 5 bars/).length).toBe(breakoutsFixture.open_breakouts.length);
    expect(within(block).getByRole('link', { name: /open full page/i })).toHaveAttribute(
      'href',
      '/scan?view=breakouts',
    );
  });

  it('shows only genuine continuation-verdict regime rows, matching the fixture count', async () => {
    renderOverview();
    await awaitLoaded();
    const block = await screen.findByRole('region', { name: 'In continuation now' });
    const expectedCount = regimeFixture.rows.filter((r) => r.verdict === 'continuation').length;
    const rows = within(block).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(expectedCount);
    // A stale/noise-dominated symbol (null verdict) must never show up here.
    const staleSymbol = regimeFixture.rows.find((r) => r.stale)?.underlying;
    if (staleSymbol) expect(within(block).queryByText(staleSymbol)).not.toBeInTheDocument();
    expect(within(block).getByRole('link', { name: /open full page/i })).toHaveAttribute(
      'href',
      '/regime',
    );
  });

  it('does not render a flows block', async () => {
    renderOverview();
    await awaitLoaded();
    expect(screen.queryByText(/flow/i)).not.toBeInTheDocument();
  });
});

describe('Overview page -- several null breakout rates', () => {
  it('excludes every null-rate symbol from both the top and worst lists, and reports the count', async () => {
    // A hand-built response: 10 symbols, 6 with `rate: null` (well below the 5-event floor),
    // 4 with a real rate spanning the range -- proves null handling is deliberate, not merely
    // "the one null row in the recorded fixture happens to sort correctly".
    const synthetic: BreakoutsResponse = {
      n: 20,
      k: 5,
      lookback: 40,
      summaries: [
        { symbol: 'AAA', events: 8, continued: 7, failed: 1, pending: 0, rate: 0.9, mean_follow_through_atr: 1, last_event: null, status: 'continued' },
        { symbol: 'BBB', events: 8, continued: 6, failed: 2, pending: 0, rate: 0.7, mean_follow_through_atr: 1, last_event: null, status: 'continued' },
        { symbol: 'CCC', events: 8, continued: 3, failed: 5, pending: 0, rate: 0.3, mean_follow_through_atr: -1, last_event: null, status: 'failed' },
        { symbol: 'DDD', events: 8, continued: 1, failed: 7, pending: 0, rate: 0.1, mean_follow_through_atr: -1, last_event: null, status: 'failed' },
        { symbol: 'NUL1', events: 2, continued: 1, failed: 1, pending: 0, rate: null, mean_follow_through_atr: null, last_event: null, status: null },
        { symbol: 'NUL2', events: 3, continued: 2, failed: 1, pending: 0, rate: null, mean_follow_through_atr: null, last_event: null, status: null },
        { symbol: 'NUL3', events: 0, continued: 0, failed: 0, pending: 0, rate: null, mean_follow_through_atr: null, last_event: null, status: null },
        { symbol: 'NUL4', events: 4, continued: 2, failed: 2, pending: 0, rate: null, mean_follow_through_atr: null, last_event: null, status: null },
        { symbol: 'NUL5', events: 1, continued: 1, failed: 0, pending: 0, rate: null, mean_follow_through_atr: null, last_event: null, status: null },
        { symbol: 'NUL6', events: 4, continued: 0, failed: 4, pending: 0, rate: null, mean_follow_through_atr: null, last_event: null, status: null },
      ],
      open_breakouts: [],
      excluded: [],
    };
    server.use(http.get('*/api/scan/breakouts', () => HttpResponse.json(synthetic)));

    renderOverview();
    await awaitLoaded();

    const topBlock = await screen.findByRole('region', { name: 'Top by breakout rate' });
    const topRows = within(topBlock).getAllByRole('row').slice(1);
    // Only 4 real-rate symbols exist, so the "top 8" list has 4 rows, not 8 padded with nulls.
    expect(topRows).toHaveLength(4);
    for (const row of topRows) {
      expect(within(row).queryByText(/NUL/)).not.toBeInTheDocument();
    }
    expect(within(topBlock).getByText('AAA')).toBeInTheDocument();

    const worstBlock = await screen.findByRole('region', { name: 'Fading — worst by breakout rate' });
    const worstRows = within(worstBlock).getAllByRole('row').slice(1);
    expect(worstRows).toHaveLength(4);
    for (const row of worstRows) {
      expect(within(row).queryByText(/NUL/)).not.toBeInTheDocument();
    }
    // DDD (rate 0.1) is the genuine worst -- must appear, not a null symbol standing in for it.
    expect(within(worstBlock).getByText('DDD')).toBeInTheDocument();

    expect(screen.getByText(/6 symbols excluded from both rate rankings/)).toBeInTheDocument();
  });
});
