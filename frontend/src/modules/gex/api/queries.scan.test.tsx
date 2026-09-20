/**
 * T55 — the scan/bars/universe/capture-health query hooks, exercised end to end through the
 * real `apiClient` against the MSW handlers (`mocks/handlers.ts`), the same "hooks, not a
 * mocked `apiClient`" style `App.test.tsx`/`Report.test.tsx` already use for the existing
 * hooks. Two things these tests exist to pin down that a component-level test wouldn't
 * necessarily catch:
 *
 *  - `^VIX` survives the full `useBars`/`useSymbolBreakouts`/`useSymbolTrend` -> `apiClient`
 *    -> `fetch` -> MSW round trip without the request ever throwing on the bare `^` (07-ui.md's
 *    own "likely first-contact failure": some browsers tolerate an unencoded `^` in a path,
 *    `fetch`/Node's URL parser and MSW's router do not).
 *  - The response shapes these hooks resolve to match `api/types.ts` closely enough that
 *    reading a field off `data` (not just checking `isSuccess`) compiles and returns the
 *    expected value.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  useBars,
  useBreakouts,
  useCaptureHealth,
  useSymbolBreakouts,
  useSymbolTrend,
  useTrend,
  useUniverse,
} from './queries';

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function Probe<T>({ hook, render: renderResult }: { hook: () => { data?: T; isSuccess: boolean; isError: boolean }; render: (data: T) => string }) {
  const result = hook();
  if (result.isError) return <span data-testid="probe">error</span>;
  if (!result.isSuccess || result.data === undefined) return <span data-testid="probe">loading</span>;
  return <span data-testid="probe">{renderResult(result.data)}</span>;
}

describe('scan query hooks against MSW', () => {
  it('useBreakouts resolves the recorded universe (47 symbols, n=20/k=5/lookback=126 default)', async () => {
    renderWithClient(
      <Probe
        hook={useBreakouts}
        render={(data) => `${data.n}/${data.k}/${data.lookback}/${data.summaries.length}`}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('20/5/126/47'));
  });

  it('useTrend resolves 47 rows', async () => {
    renderWithClient(<Probe hook={useTrend} render={(data) => String(data.rows.length)} />);
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('47'));
  });

  it('useUniverse resolves the configured SCAN_UNIVERSE', async () => {
    renderWithClient(<Probe hook={useUniverse} render={(data) => String(data.symbols.length)} />);
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('47'));
  });

  it('useCaptureHealth resolves the bars block', async () => {
    renderWithClient(<Probe hook={useCaptureHealth} render={(data) => String(data.bars.symbols.length)} />);
    await waitFor(() => expect(screen.getByTestId('probe')).not.toHaveTextContent('loading'));
    expect(screen.getByTestId('probe').textContent).not.toBe('error');
  });

  it('useSymbolBreakouts("^VIX") round-trips the bare caret without throwing', async () => {
    renderWithClient(
      <Probe hook={() => useSymbolBreakouts('^VIX')} render={(data) => `${data.symbol}:${data.events.length}`} />,
    );
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('^VIX:0'));
  });

  it('useSymbolTrend("^VIX") round-trips the bare caret without throwing', async () => {
    renderWithClient(<Probe hook={() => useSymbolTrend('^VIX')} render={(data) => data.symbol} />);
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('^VIX'));
  });

  it('useBars("^VIX") round-trips the bare caret without throwing', async () => {
    renderWithClient(<Probe hook={() => useBars('^VIX')} render={(data) => `len:${data.length}`} />);
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('len:0'));
  });

  it('useBars("SPY") resolves the recorded fixture (132 rows)', async () => {
    renderWithClient(<Probe hook={() => useBars('SPY')} render={(data) => `len:${data.length}`} />);
    await waitFor(() => expect(screen.getByTestId('probe')).toHaveTextContent('len:132'));
  });
});
