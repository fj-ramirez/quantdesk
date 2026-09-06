import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { useDashboardParams } from './urlState';

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
