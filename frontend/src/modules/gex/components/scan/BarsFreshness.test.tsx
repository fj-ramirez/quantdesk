import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';
import { server } from '../../../../mocks/server';
import { BarsFreshness } from './BarsFreshness';
import healthCaptureFixture from '../../mocks/fixtures/scan/health_capture.json';
import healthCaptureStaleFixture from '../../mocks/fixtures/scan/health_capture_stale.json';

// `formatBarsThrough` (the date formatter this component uses) is tested directly in
// `lib/time.test.ts`, alongside this app's other timestamp formatters.

function renderBarsFreshness() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <BarsFreshness />
    </QueryClientProvider>,
  );
}

describe('BarsFreshness', () => {
  it('renders the bars-through summary and symbol count once loaded', async () => {
    renderBarsFreshness();
    await waitFor(() => expect(screen.queryByText(/Loading bars freshness/)).not.toBeInTheDocument());
    const symbolCount = (healthCaptureFixture as { bars: { symbols: unknown[] } }).bars.symbols.length;
    expect(screen.getByText(new RegExp(`${symbolCount} symbols`))).toBeInTheDocument();
  });

  it('shows no stale suffix when stale_count is 0', async () => {
    renderBarsFreshness();
    await waitFor(() => expect(screen.queryByText(/Loading bars freshness/)).not.toBeInTheDocument());
    expect(screen.queryByText(/stale/)).not.toBeInTheDocument();
  });

  it('shows the stale count when non-zero', async () => {
    server.use(http.get('*/api/gex/health/capture', () => HttpResponse.json(healthCaptureStaleFixture)));
    renderBarsFreshness();
    await waitFor(() => expect(screen.getByText(/1 stale/)).toBeInTheDocument());
  });

  it('renders an unavailable message on a transport failure, never a raw body', async () => {
    server.use(http.get('*/api/gex/health/capture', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
    renderBarsFreshness();
    await waitFor(() => expect(screen.getByText('Bars freshness unavailable')).toBeInTheDocument());
    expect(document.body.textContent).not.toMatch(/\{"detail"/);
  });
});
