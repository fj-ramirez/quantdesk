import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { useDashboardParams, useScanParams } from './urlState';

// Small harness exercising the hook the way real components (TopBar, Dashboard) do,
// without pulling in TanStack Query or MSW — this file tests URL-state wiring in
// isolation, per T12's acceptance criteria: symbol/filter/snapshot changes update the
// search params, and a deep link produces the matching state on load.
function Harness() {
  const { symbol, filter, snapshotId, cfdSpot, setSymbol, setFilter, setSnapshotId, setCfdSpot } = useDashboardParams();
  const location = useLocation();
  return (
    <div>
      <span data-testid="symbol">{symbol}</span>
      <span data-testid="filter">{filter}</span>
      <span data-testid="snapshot">{snapshotId ?? 'latest'}</span>
      <span data-testid="cfd">{cfdSpot ?? 'none'}</span>
      <span data-testid="search">{location.search}</span>
      <button onClick={() => setSymbol('QQQ')}>set-qqq</button>
      <button onClick={() => setFilter('ZERO_DTE')}>set-zero-dte</button>
      <button onClick={() => setSnapshotId('42')}>set-snap</button>
      <button onClick={() => setCfdSpot('4412.50')}>set-cfd</button>
      <button onClick={() => setCfdSpot(null)}>clear-cfd</button>
    </div>
  );
}

function renderWithRouter(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Harness />
    </MemoryRouter>,
  );
}

describe('useDashboardParams', () => {
  it('defaults to SPX / ALL / latest when the URL carries no params', () => {
    renderWithRouter('/');
    expect(screen.getByTestId('symbol')).toHaveTextContent('SPX');
    expect(screen.getByTestId('filter')).toHaveTextContent('ALL');
    expect(screen.getByTestId('snapshot')).toHaveTextContent('latest');
  });

  it('loading ?symbol=QQQ&filter=ZERO_DTE produces the matching state', () => {
    renderWithRouter('/?symbol=QQQ&filter=ZERO_DTE');
    expect(screen.getByTestId('symbol')).toHaveTextContent('QQQ');
    expect(screen.getByTestId('filter')).toHaveTextContent('ZERO_DTE');
  });

  it('falls back to defaults for an unrecognized symbol/filter rather than crashing', () => {
    renderWithRouter('/?symbol=NOPE&filter=BOGUS');
    expect(screen.getByTestId('symbol')).toHaveTextContent('SPX');
    expect(screen.getByTestId('filter')).toHaveTextContent('ALL');
  });

  it('changing the symbol updates the URL search param', () => {
    renderWithRouter('/?symbol=SPY&filter=ALL');
    fireEvent.click(screen.getByText('set-qqq'));
    expect(screen.getByTestId('symbol')).toHaveTextContent('QQQ');
    expect(screen.getByTestId('search')).toHaveTextContent('symbol=QQQ');
  });

  it('changing the filter updates the URL search param', () => {
    renderWithRouter('/?symbol=SPX');
    fireEvent.click(screen.getByText('set-zero-dte'));
    expect(screen.getByTestId('filter')).toHaveTextContent('ZERO_DTE');
    expect(screen.getByTestId('search')).toHaveTextContent('filter=ZERO_DTE');
  });

  it('switching symbol clears a pinned snapshot from the old symbol', () => {
    renderWithRouter('/?symbol=SPY&snapshot=99');
    expect(screen.getByTestId('snapshot')).toHaveTextContent('99');
    fireEvent.click(screen.getByText('set-qqq'));
    expect(screen.getByTestId('snapshot')).toHaveTextContent('latest');
    expect(screen.getByTestId('search')).not.toHaveTextContent('snapshot');
  });

  it('setting a snapshot id updates the URL search param', () => {
    renderWithRouter('/?symbol=SPX');
    fireEvent.click(screen.getByText('set-snap'));
    expect(screen.getByTestId('snapshot')).toHaveTextContent('42');
    expect(screen.getByTestId('search')).toHaveTextContent('snapshot=42');
  });

  // T41: the CFD spot deep-links the same way `filter` does.
  it('loading ?symbol=GLD&cfd=4412.50 produces the matching state', () => {
    renderWithRouter('/?symbol=GLD&cfd=4412.50');
    expect(screen.getByTestId('symbol')).toHaveTextContent('GLD');
    expect(screen.getByTestId('cfd')).toHaveTextContent('4412.50');
  });

  it('setting a CFD spot updates the URL search param', () => {
    renderWithRouter('/?symbol=GLD');
    fireEvent.click(screen.getByText('set-cfd'));
    expect(screen.getByTestId('cfd')).toHaveTextContent('4412.50');
    expect(screen.getByTestId('search')).toHaveTextContent('cfd=4412.50');
  });

  it('clearing the CFD spot removes it from the URL search param', () => {
    renderWithRouter('/?symbol=GLD&cfd=4412.50');
    fireEvent.click(screen.getByText('clear-cfd'));
    expect(screen.getByTestId('cfd')).toHaveTextContent('none');
    expect(screen.getByTestId('search')).not.toHaveTextContent('cfd');
  });

  it('switching symbol clears a CFD spot anchored to the old symbol', () => {
    // A GLD-derived XAUUSD ratio means nothing once the symbol switches to DIA -- it must
    // not silently carry over as a wrong number, the same rule already applied to `snapshot`.
    renderWithRouter('/?symbol=GLD&cfd=4412.50');
    expect(screen.getByTestId('cfd')).toHaveTextContent('4412.50');
    fireEvent.click(screen.getByText('set-qqq'));
    expect(screen.getByTestId('cfd')).toHaveTextContent('none');
    expect(screen.getByTestId('search')).not.toHaveTextContent('cfd');
  });
});

// T55 -- `useScanParams`, the scan family's URL state. Same isolated-hook-test pattern as
// `useDashboardParams` above: a small harness, no TanStack Query/MSW involved.
function ScanHarness() {
  const { view, n, k, lookback, sort, dir, group, benchmark, weeks, window, setN, setK, setLookback, setSort } =
    useScanParams();
  const location = useLocation();
  return (
    <div>
      <span data-testid="view">{view}</span>
      <span data-testid="n">{n}</span>
      <span data-testid="k">{k}</span>
      <span data-testid="lookback">{lookback}</span>
      <span data-testid="sort">{sort ?? 'none'}</span>
      <span data-testid="dir">{dir}</span>
      <span data-testid="group">{group}</span>
      <span data-testid="benchmark">{benchmark}</span>
      <span data-testid="weeks">{weeks}</span>
      <span data-testid="window">{window}</span>
      <span data-testid="search">{location.search}</span>
      <button onClick={() => setN(55)}>set-n-55</button>
      <button onClick={() => setK(10)}>set-k-10</button>
      <button onClick={() => setLookback(252)}>set-lookback-252</button>
      <button onClick={() => setSort('rate', 'asc')}>set-sort-rate-asc</button>
      <button onClick={() => setSort('composite', 'desc')}>set-sort-composite-desc</button>
    </div>
  );
}

function renderScanWithRouter(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <ScanHarness />
    </MemoryRouter>,
  );
}

describe('useScanParams', () => {
  it('defaults every param when the URL carries none', () => {
    renderScanWithRouter('/scan');
    expect(screen.getByTestId('view')).toHaveTextContent('breakouts');
    expect(screen.getByTestId('n')).toHaveTextContent('20');
    expect(screen.getByTestId('k')).toHaveTextContent('5');
    expect(screen.getByTestId('lookback')).toHaveTextContent('126');
    expect(screen.getByTestId('sort')).toHaveTextContent('none');
    expect(screen.getByTestId('dir')).toHaveTextContent('desc');
    expect(screen.getByTestId('group')).toHaveTextContent('sectors');
    expect(screen.getByTestId('benchmark')).toHaveTextContent('SPY');
    expect(screen.getByTestId('weeks')).toHaveTextContent('6');
    expect(screen.getByTestId('window')).toHaveTextContent('20');
  });

  it('loading a valid deep link produces the matching state for every param', () => {
    renderScanWithRouter(
      '/scan?view=trend&n=55&k=10&lookback=252&sort=composite&dir=asc&group=industries&benchmark=RSP&weeks=14&window=60',
    );
    expect(screen.getByTestId('view')).toHaveTextContent('trend');
    expect(screen.getByTestId('n')).toHaveTextContent('55');
    expect(screen.getByTestId('k')).toHaveTextContent('10');
    expect(screen.getByTestId('lookback')).toHaveTextContent('252');
    expect(screen.getByTestId('sort')).toHaveTextContent('composite');
    expect(screen.getByTestId('dir')).toHaveTextContent('asc');
    expect(screen.getByTestId('group')).toHaveTextContent('industries');
    expect(screen.getByTestId('benchmark')).toHaveTextContent('RSP');
    expect(screen.getByTestId('weeks')).toHaveTextContent('14');
    expect(screen.getByTestId('window')).toHaveTextContent('60');
  });

  // The acceptance test named explicitly in the T55 brief: an invalid `n` falls back to the
  // default WHILE the other params on the same URL are left exactly as given -- not reset
  // alongside it. A naive implementation that dropped the whole query string (or fell back to
  // every default) on any single invalid param would pass a "falls back to default" test but
  // fail this one.
  it('rejects an invalid n and falls back to its default while leaving every other param intact', () => {
    renderScanWithRouter('/scan?n=999&k=10&lookback=252&view=trend&sort=composite&dir=asc');
    expect(screen.getByTestId('n')).toHaveTextContent('20'); // rejected -> default
    expect(screen.getByTestId('k')).toHaveTextContent('10'); // untouched
    expect(screen.getByTestId('lookback')).toHaveTextContent('252'); // untouched
    expect(screen.getByTestId('view')).toHaveTextContent('trend'); // untouched
    expect(screen.getByTestId('sort')).toHaveTextContent('composite'); // untouched
    expect(screen.getByTestId('dir')).toHaveTextContent('asc'); // untouched
  });

  it('rejects an invalid view/group/benchmark independently, each falling back on its own', () => {
    renderScanWithRouter('/scan?view=bogus&group=bogus&benchmark=bogus&n=55');
    expect(screen.getByTestId('view')).toHaveTextContent('breakouts');
    expect(screen.getByTestId('group')).toHaveTextContent('sectors');
    expect(screen.getByTestId('benchmark')).toHaveTextContent('SPY');
    expect(screen.getByTestId('n')).toHaveTextContent('55'); // untouched
  });

  it('setting n updates the URL search param without disturbing k/lookback', () => {
    renderScanWithRouter('/scan?k=10&lookback=252');
    fireEvent.click(screen.getByText('set-n-55'));
    expect(screen.getByTestId('n')).toHaveTextContent('55');
    expect(screen.getByTestId('k')).toHaveTextContent('10');
    expect(screen.getByTestId('lookback')).toHaveTextContent('252');
    expect(screen.getByTestId('search')).toHaveTextContent('n=55');
  });

  it('setSort writes sort and dir together in one update', () => {
    renderScanWithRouter('/scan');
    fireEvent.click(screen.getByText('set-sort-rate-asc'));
    expect(screen.getByTestId('sort')).toHaveTextContent('rate');
    expect(screen.getByTestId('dir')).toHaveTextContent('asc');
    fireEvent.click(screen.getByText('set-sort-composite-desc'));
    expect(screen.getByTestId('sort')).toHaveTextContent('composite');
    expect(screen.getByTestId('dir')).toHaveTextContent('desc');
  });
});
