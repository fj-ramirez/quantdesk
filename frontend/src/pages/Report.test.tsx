/**
 * T40 — the report page, rendered end to end through real TanStack Query hooks against the
 * MSW handlers (src/mocks), the same way `App.test.tsx` exercises the dashboard.
 *
 * The report fixtures these run against are not approximations: each is the real
 * `GET /api/report/{underlying}` body, produced by running a real Cboe chain through the
 * backend's own engine and report module and serialized through the same Pydantic model the
 * endpoint returns. So `expect(screen.getByText('0.45'))` below is asserting the actual
 * put/call ratio of the live GLD chain, not a number someone typed into a fixture.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { server } from '../mocks/server';
import { ThemeProvider } from '../theme/ThemeContext';
import { Report } from './Report';

function renderReport(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/report" element={<Report />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

/** Wait for the report *data*, not just the page chrome.
 *
 * `Report.tsx` renders its heading and the symbol/filter selects above the `isLoading` gate,
 * so `findByRole('heading', ...)` resolves while the query is still in flight. Every
 * assertion that reads a computed figure has to wait for the loading line to clear as well,
 * or it races the fetch and reads an empty page. */
async function awaitReportLoaded(symbol: string) {
  await screen.findByRole('heading', { name: `${symbol} Analysis Results` });
  await waitFor(() => expect(screen.queryByText(/Loading the/)).not.toBeInTheDocument());
}

describe('Report page', () => {
  it('deep link ?symbol=GLD renders every section end to end', async () => {
    renderReport('/report?symbol=GLD');

    await awaitReportLoaded('GLD');

    for (const label of [
      'Current price',
      'Volatility',
      'Market sentiment',
      'Top resistance levels',
      'Top support levels',
      'Gamma exposure',
      'Premium selling screen',
      'Playbook',
      'Executive summary',
      'Full report',
    ]) {
      expect(screen.getByRole('region', { name: label })).toBeInTheDocument();
    }
  });

  it('GLD shows the real chain figures, not the example report inverted ones', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    // Spot 406.77 and max pain 400 — the example report claimed max pain 410.
    expect(within(screen.getByRole('region', { name: 'Current price' })).getByText('406.77')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Current price' }).textContent).toContain('Max pain 400');

    // Put/call ratio on OI is 0.45 — GLD carries ~2.2 calls per put. The example report
    // asserted 2.54 puts per call, the exact inversion, and called it bearish.
    const sentiment = screen.getByRole('region', { name: 'Market sentiment' });
    expect(within(sentiment).getByText('0.45')).toBeInTheDocument();

    // 42.7% of gross clears the 3% floor, so a direction is reported.
    expect(screen.getByTestId('positioning-label')).toHaveTextContent('LONG GAMMA');
  });

  it('DIA visibly shows the noise-dominated label rather than a direction', async () => {
    renderReport('/report?symbol=DIA');
    await awaitReportLoaded('DIA');

    // The acceptance criterion. DIA's net GEX is 0.9% of its gross and its sign flips under a
    // plausible carry correction (docs/validation.md section 9), so the page must decline to
    // say which way dealers are positioned.
    expect(screen.getByTestId('positioning-label')).toHaveTextContent('NOISE-DOMINATED');
    expect(screen.getByTestId('positioning-label')).not.toHaveTextContent('SHORT GAMMA');
    expect(screen.getByTestId('positioning-label')).not.toHaveTextContent('LONG GAMMA');

    const sentiment = screen.getByRole('region', { name: 'Market sentiment' });
    expect(sentiment.textContent).toMatch(/below the 3% floor/);
  });

  it('DIA labels the strikes that straddle spot instead of sorting them into support', async () => {
    renderReport('/report?symbol=DIA');
    await awaitReportLoaded('DIA');

    const straddling = screen.getByRole('region', { name: 'Levels straddling spot' });
    expect(straddling.textContent).toMatch(/concentrated on both sides of spot/);
  });

  it('resistance strikes always sort above support strikes', async () => {
    renderReport('/report?symbol=DIA');
    await awaitReportLoaded('DIA');

    const strikesIn = (label: string) =>
      within(screen.getByRole('region', { name: label }))
        .queryAllByRole('listitem')
        .map((item) => Number(item.querySelector('strong')!.textContent!.replace(/,/g, '')));

    const resistance = strikesIn('Top resistance levels');
    const support = strikesIn('Top support levels');
    if (resistance.length > 0 && support.length > 0) {
      expect(Math.min(...resistance)).toBeGreaterThan(Math.max(...support));
    }
  });

  it('renders "insufficient history" for the IV regime rather than inventing a band', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    const volatility = screen.getByRole('region', { name: 'Volatility' });
    expect(volatility.textContent).toMatch(/Insufficient history/i);
    // The number is still shown; only the label is withheld.
    expect(volatility.textContent).toMatch(/\d+\.\d+%/);
    expect(volatility.textContent).not.toMatch(/NORMAL|LOW|HIGH/);
  });

  it('labels the trade-suggestion sections as screening output and never as advice', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    const premium = screen.getByRole('region', { name: 'Premium selling screen' });
    expect(premium.textContent).toMatch(/not a recommendation/);
    expect(premium.textContent).toMatch(/never routes an order/);

    const playbook = screen.getByRole('region', { name: 'Playbook' });
    expect(playbook.textContent).toMatch(/not a recommendation/);
  });

  it('surfaces the T34 data-freshness badge', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');
    // `formatFreshness` produces either "As of ... ET" or "At <day>'s close".
    expect(screen.getByText(/As of .*ET|close \(/)).toBeInTheDocument();
  });

  it('the ZERO_DTE filter renders the empty scope cleanly rather than as zeros', async () => {
    renderReport('/report?symbol=GLD&filter=ZERO_DTE');
    await awaitReportLoaded('GLD');

    // The daily post-close case: no same-day contracts, so no levels and no positioning.
    expect(screen.getByTestId('positioning-label')).toHaveTextContent('NO DATA');
    expect(screen.getByRole('region', { name: 'Top resistance levels' }).textContent).toMatch(
      /No positive-gamma strike sits above spot/,
    );
    const price = screen.getByRole('region', { name: 'Current price' });
    expect(price.textContent).toContain('Max pain —');
  });

  it('switching the symbol select updates the URL-driven view', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    fireEvent.change(screen.getByLabelText(/Symbol/), { target: { value: 'DIA' } });

    await awaitReportLoaded('DIA');
    expect(screen.getByTestId('positioning-label')).toHaveTextContent('NOISE-DOMINATED');
  });

  it('expands the full report panel and shows the backend-rendered text', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    // Fetched lazily: nothing is requested until the panel is opened.
    expect(screen.queryByTestId('report-text')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'View full report' }));

    await waitFor(() => expect(screen.getByTestId('report-text')).toBeInTheDocument());
    expect(screen.getByTestId('report-text').textContent).toMatch(/GLD OPTIONS INTELLIGENCE/);
    expect(screen.getByRole('button', { name: 'Copy to clipboard' })).toBeInTheDocument();
  });
});

describe('Report page error and empty states (T37)', () => {
  it('a symbol with no snapshot renders the empty state with a capture affordance, not an error', async () => {
    server.use(
      http.get('*/api/report/:underlying', () =>
        HttpResponse.json({ detail: 'no snapshot captured yet for SPY' }, { status: 404 }),
      ),
    );
    renderReport('/report?symbol=SPY');

    await waitFor(() => expect(screen.getByRole('region', { name: 'No data yet' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Capture now' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    // The T37 regression: no raw JSON envelope may reach the page.
    expect(document.body.textContent).not.toMatch(/\{"detail"/);
  });

  it('a genuine server failure keeps an error presentation with the parsed message', async () => {
    server.use(
      http.get('*/api/report/:underlying', () =>
        HttpResponse.json({ detail: 'snapshot 3 is indexed but its Parquet file is missing' }, { status: 500 }),
      ),
    );
    renderReport('/report?symbol=SPY');

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toContain('Parquet file is missing');
    expect(document.body.textContent).not.toMatch(/\{"detail"/);
    expect(screen.queryByRole('button', { name: 'Capture now' })).not.toBeInTheDocument();
  });

  it('a 404 that is not "no snapshot yet" stays an error rather than becoming an empty state', async () => {
    // Matching on the status alone would hide real breakage behind a friendly empty state.
    server.use(
      http.get('*/api/report/:underlying', () =>
        HttpResponse.json({ detail: 'snapshot 7 is indexed but its Parquet file is missing' }, { status: 404 }),
      ),
    );
    renderReport('/report?symbol=SPY');

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.queryByRole('region', { name: 'No data yet' })).not.toBeInTheDocument();
  });

  it('an unparseable error body degrades to the status text, never the raw body', async () => {
    server.use(
      http.get('*/api/report/:underlying', () =>
        HttpResponse.text('<html><body>502 Bad Gateway</body></html>', { status: 502 }),
      ),
    );
    renderReport('/report?symbol=SPY');

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).not.toContain('<html>');
  });
});
