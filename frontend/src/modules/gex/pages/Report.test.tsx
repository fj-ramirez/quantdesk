/**
 * T40 — the report page, rendered end to end through real TanStack Query hooks against the
 * MSW handlers (src/mocks), the same way `App.test.tsx` exercises the dashboard.
 *
 * The report fixtures these run against are not approximations: each is the real
 * `GET /api/gex/report/{underlying}` body, produced by running a real Cboe chain through the
 * backend's own engine and report module and serialized through the same Pydantic model the
 * endpoint returns. So `expect(screen.getByText('0.45'))` below is asserting the actual
 * put/call ratio of the live GLD chain, not a number someone typed into a fixture.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { server } from '../../../mocks/server';
import { ThemeProvider } from '../../../theme/ThemeContext';
import { ContextBar } from '../components/layout/ContextBar';
import { Report } from './Report';

// T67: the page-body symbol/expiry `<select>`s this test used to drive directly are gone --
// `/report` isn't in `ContextBar`'s `SCAN_FAMILY_PATHS`, so it renders the same
// `AssetSelector`/expiry-select controls `/dashboard` gets, and switching symbol now happens
// through that shared control (the same pattern `App.test.tsx` already uses for `/dashboard`).
// `ContextBar` is rendered alongside `Report` here so a real, end-to-end symbol switch is
// exercised rather than assumed.
function renderReport(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <ContextBar />
          <Routes>
            <Route path="/report" element={<Report />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

/** Switches the underlying via `ContextBar`'s `AssetSelector` -- opens the dropdown from the
 * trigger button (accessible name "Symbol {current}") and picks the target option. */
function switchSymbol(current: string, next: string) {
  fireEvent.click(screen.getByRole('button', { name: `Symbol ${current}` }));
  fireEvent.click(screen.getByRole('option', { name: next }));
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
    // `formatFreshness` produces either "As of ... ET" or "At <day>'s close". Scoped to the
    // page's own freshness caveat (`data-testid="report-freshness"`, in `PageHeader`) since
    // `ContextBar`'s own freshness badge renders the same style of text from a different
    // query and would otherwise make an unscoped `getByText` ambiguous.
    expect(screen.getByTestId('report-freshness').textContent).toMatch(/As of .*ET|close \(/);
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

  it('switching the ContextBar symbol control updates the URL-driven view', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    switchSymbol('GLD', 'DIA');

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

describe('Report page density pass (T69)', () => {
  it('Gamma exposure, Premium selling screen, Playbook and Summary are collapsed <details> by default', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    for (const label of ['Gamma exposure', 'Premium selling screen', 'Playbook', 'Executive summary']) {
      const region = screen.getByRole('region', { name: label });
      const details = region.closest('details') as HTMLDetailsElement | null;
      expect(details).not.toBeNull();
      expect(details!.open).toBe(false);
    }
  });

  it('clicking a disclosure summary opens it, keyboard-operable via the native <details> element', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    const region = screen.getByRole('region', { name: 'Playbook' });
    const details = region.closest('details') as HTMLDetailsElement;
    expect(details.open).toBe(false);

    fireEvent.click(screen.getByRole('heading', { name: 'Playbook', level: 2 }));
    expect(details.open).toBe(true);
  });

  it('Risk alerts is also a collapsed <details> when the symbol has any alerts', async () => {
    renderReport('/report?symbol=DIA');
    await awaitReportLoaded('DIA');

    const region = screen.getByRole('region', { name: 'Risk alerts' });
    const details = region.closest('details') as HTMLDetailsElement;
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
  });

  it('Levels straddling spot and the three summary cards are never wrapped in a <details> -- always visible', async () => {
    renderReport('/report?symbol=DIA');
    await awaitReportLoaded('DIA');

    for (const label of ['Current price', 'Volatility', 'Market sentiment', 'Levels straddling spot']) {
      const region = screen.getByRole('region', { name: label });
      expect(region.closest('details')).toBeNull();
    }
  });

  it('the three load-bearing disclaimer strings still appear verbatim after the disclosure rewrite (T67 regression check)', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    expect(document.body.textContent).toContain('Screening output');
    expect(document.body.textContent).toMatch(/not a recommendation/);
    expect(document.body.textContent).toMatch(/never routes an order/);
  });
});

describe('CFD level translation (T41)', () => {
  it('labels the CFD-spot field from CFD_INSTRUMENTS for GLD and DIA', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');
    expect(screen.getByLabelText('XAUUSD spot')).toBeInTheDocument();

    switchSymbol('GLD', 'DIA');
    await awaitReportLoaded('DIA');
    expect(screen.getByLabelText('US30 spot')).toBeInTheDocument();
  });

  it('T47: a symbol with no CFD_INSTRUMENTS entry (an extended ETF) shows "No CFD mapping" instead of a broken "undefined spot" input', async () => {
    renderReport('/report?symbol=XLK');
    await awaitReportLoaded('XLK');

    expect(screen.getByText('No CFD mapping for XLK')).toBeInTheDocument();
    expect(screen.queryByLabelText(/spot$/)).not.toBeInTheDocument();

    // Switching back to a mapped symbol restores the normal input -- the degrade is per
    // symbol, not a permanently broken state.
    switchSymbol('XLK', 'GLD');
    await awaitReportLoaded('GLD');
    expect(screen.getByLabelText('XAUUSD spot')).toBeInTheDocument();
  });

  it('an absent ?cfd= leaves the report exactly as it is without T41 -- no converted block', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    expect(screen.getByLabelText('XAUUSD spot')).toHaveValue('');
    // None of the native sections gain a bracketed CFD figure when no spot is supplied.
    expect(screen.queryByText(/XAUUSD [\d,]+\.\d{2}/)).not.toBeInTheDocument();
  });

  it('?symbol=GLD&cfd=4412.50 deep-links the field and requests a converted report', async () => {
    renderReport('/report?symbol=GLD&cfd=4412.50');
    await awaitReportLoaded('GLD');

    expect(screen.getByLabelText('XAUUSD spot')).toHaveValue('4412.50');
    // The anchor callout: ratio and the spot pair, so the user can eyeball it against their
    // own platform.
    expect(screen.getByText(/XAUUSD 4,412\.50 \/ GLD 406\.77 = ratio/)).toBeInTheDocument();
  });

  it('shows the converted call wall alongside the native one, never in its place', async () => {
    renderReport('/report?symbol=GLD&cfd=4412.50');
    await awaitReportLoaded('GLD');

    const gamma = screen.getByRole('region', { name: 'Gamma exposure' });
    // Native call wall (415) is still present...
    expect(within(gamma).getByText('415')).toBeInTheDocument();
    // ...alongside its XAUUSD translation, shown rather than substituted.
    expect(gamma.textContent).toMatch(/XAUUSD 4,501\.78/);
  });

  it('percentage distance from spot is identical in both units (T41 invariant)', async () => {
    renderReport('/report?symbol=GLD&cfd=4412.50');
    await awaitReportLoaded('GLD');

    // GLD's 415 call wall is +2.02% from a 406.77 spot -- shown on the native chip...
    const resistance = screen.getByRole('region', { name: 'Top resistance levels' });
    expect(resistance.textContent).toMatch(/\+2\.02%/);
    // ...and its XAUUSD translation (4,501.78) sits alongside it, not a recomputed distance.
    expect(resistance.textContent).toMatch(/XAUUSD 4,501\.78/);
    // Only one "+2.02%" -- the CFD chip does not print a second, independently-derived figure.
    expect(resistance.textContent?.match(/\+2\.02%/g)).toHaveLength(1);
  });

  it('typing a non-positive CFD spot surfaces a validation message and sends no conversion', async () => {
    renderReport('/report?symbol=GLD');
    await awaitReportLoaded('GLD');

    fireEvent.change(screen.getByLabelText('XAUUSD spot'), { target: { value: '-5' } });

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/positive number/));
    // The bad value must never reach the report as a converted block.
    expect(screen.queryByText(/XAUUSD [\d,]+\.\d{2}/)).not.toBeInTheDocument();
  });

  it('the copyable full-text report carries the translated playbook', async () => {
    renderReport('/report?symbol=GLD&cfd=4412.50');
    await awaitReportLoaded('GLD');

    fireEvent.click(screen.getByRole('button', { name: 'View full report' }));
    await waitFor(() => expect(screen.getByTestId('report-text')).toBeInTheDocument());

    expect(screen.getByTestId('report-text').textContent).toMatch(/XAUUSD TRANSLATION/);
  });
});

describe('Report page error and empty states (T37)', () => {
  it('a symbol with no snapshot renders the empty state with a capture affordance, not an error', async () => {
    server.use(
      http.get('*/api/gex/report/:underlying', () =>
        HttpResponse.json({ detail: 'no snapshot captured yet for SPY' }, { status: 404 }),
      ),
    );
    renderReport('/report?symbol=SPY');

    // T67: the empty state now goes through the shared `EmptyState` primitive, whose
    // `heading` prop is both the visible heading and the region's accessible name (rather
    // than the old bespoke section's separate fixed `aria-label="No data yet"`).
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'No SPY snapshot captured yet' })).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: 'Capture now' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    // The T37 regression: no raw JSON envelope may reach the page.
    expect(document.body.textContent).not.toMatch(/\{"detail"/);
  });

  it('a genuine server failure keeps an error presentation with the parsed message', async () => {
    server.use(
      http.get('*/api/gex/report/:underlying', () =>
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
      http.get('*/api/gex/report/:underlying', () =>
        HttpResponse.json({ detail: 'snapshot 7 is indexed but its Parquet file is missing' }, { status: 404 }),
      ),
    );
    renderReport('/report?symbol=SPY');

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.queryByRole('region', { name: 'No SPY snapshot captured yet' })).not.toBeInTheDocument();
  });

  it('an unparseable error body degrades to the status text, never the raw body', async () => {
    server.use(
      http.get('*/api/gex/report/:underlying', () =>
        HttpResponse.text('<html><body>502 Bad Gateway</body></html>', { status: 502 }),
      ),
    );
    renderReport('/report?symbol=SPY');

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).not.toContain('<html>');
  });
});
