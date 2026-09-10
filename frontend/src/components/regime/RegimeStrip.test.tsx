import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { server } from '../../mocks/server';
import { ThemeProvider } from '../../theme/ThemeContext';
import { RegimeStrip } from './RegimeStrip';
import crossAssetFixture from '../../mocks/fixtures/scan/cross_asset.json';
import crossAssetEmptyFixture from '../../mocks/fixtures/scan/cross_asset_empty.json';

function renderStrip() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <RegimeStrip />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

async function waitForLoaded() {
  await waitFor(() => expect(screen.queryByText(/Loading regime strip/)).not.toBeInTheDocument());
}

describe('RegimeStrip', () => {
  it('renders every one of the nine tile labels', async () => {
    renderStrip();
    await waitForLoaded();
    for (const label of [
      'VIX9D/VIX',
      'VIX/VIX3M',
      'VVIX',
      'VIX 1y pct',
      'SPY VRP',
      'Sector corr 20d',
      'UUP 20d',
      'GLD 20d',
      'TLT 20d',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it('renders the live term-structure label and both ratio values from the fixture', async () => {
    renderStrip();
    await waitForLoaded();
    // Two StatusChips share the "contango" label (VIX9D/VIX and VIX/VIX3M tiles).
    expect(screen.getAllByText('contango').length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText(crossAssetFixture.vix9d_vix_ratio.toFixed(2) + '×'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(crossAssetFixture.vix_vix3m_ratio.toFixed(2) + '×'),
    ).toBeInTheDocument();
  });

  it('renders the sector-correlation effective sample size, not just the value', async () => {
    renderStrip();
    await waitForLoaded();
    expect(
      screen.getByText(`n=${crossAssetFixture.sector_correlation_n}/20 aligned days`),
    ).toBeInTheDocument();
  });

  it('renders signed 20-day moves for UUP/GLD/TLT', async () => {
    renderStrip();
    await waitForLoaded();
    // GLD's fixture return is positive -> a leading "+".
    expect(crossAssetFixture.gld_return_20d).toBeGreaterThan(0);
    expect(screen.getAllByText(/^\+\d+\.\d{2}%$/).length).toBeGreaterThanOrEqual(1);
  });

  it('renders "n/a" tiles with a reason in the tooltip when nothing is seeded yet', async () => {
    server.use(http.get('*/api/scan/cross-asset', () => HttpResponse.json(crossAssetEmptyFixture)));
    renderStrip();
    await waitForLoaded();

    const naValues = screen.getAllByText('n/a');
    expect(naValues.length).toBeGreaterThan(0);

    // The term-structure tile's "n/a" tooltip carries the API's own reason string, not a
    // generic placeholder.
    const tsValue = naValues.find((el) =>
      el.getAttribute('title')?.includes(crossAssetEmptyFixture.term_structure_reason),
    );
    expect(tsValue).toBeTruthy();

    // No fabricated ratio/percent renders in this state.
    expect(screen.queryByText(/^\d+\.\d{2}×$/)).not.toBeInTheDocument();
  });

  it('renders a loading state before data arrives', () => {
    renderStrip();
    expect(screen.getByText(/Loading regime strip/)).toBeInTheDocument();
  });

  it('renders an unavailable message on a transport failure, never a raw body', async () => {
    server.use(http.get('*/api/scan/cross-asset', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
    renderStrip();
    await waitFor(() => expect(screen.getByText('Regime strip unavailable')).toBeInTheDocument());
    expect(document.body.textContent).not.toMatch(/\{"detail"/);
  });
});
