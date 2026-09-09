import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { SymbolCell } from './SymbolCell';

function renderCell(symbol: string, search?: string) {
  return render(
    <MemoryRouter>
      <SymbolCell symbol={symbol} search={search} />
    </MemoryRouter>,
  );
}

describe('SymbolCell', () => {
  it('renders a core underlying (an option-chain symbol) as a link to the dashboard', () => {
    renderCell('SPY');
    const link = screen.getByRole('link', { name: 'SPY' });
    expect(link).toHaveAttribute('href', '/?symbol=SPY');
  });

  it('renders an extended underlying (also an option-chain symbol) as a link', () => {
    renderCell('XLK');
    expect(screen.getByRole('link', { name: 'XLK' })).toHaveAttribute('href', '/?symbol=XLK');
  });

  it('renders a non-chain symbol as plain text with an explanatory tooltip, not a link', () => {
    // RSP (the rotation benchmark) is in `SCAN_UNIVERSE` but neither `CORE_UNDERLYINGS` nor
    // `EXTENDED_UNDERLYINGS` -- one of the 19 of 47 universe symbols with no option chain.
    renderCell('RSP');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    const cell = screen.getByText('RSP');
    expect(cell).toHaveAttribute('title', 'no option chain tracked');
  });

  it('a non-chain symbol including ^VIX renders as plain text', () => {
    renderCell('^VIX');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('^VIX')).toBeInTheDocument();
  });

  it('carries the current filter search string onto the link, replacing only `symbol`', () => {
    renderCell('SPY', '?symbol=QQQ&filter=ZERO_DTE');
    const link = screen.getByRole('link', { name: 'SPY' });
    const href = link.getAttribute('href')!;
    const params = new URLSearchParams(href.split('?')[1]);
    expect(params.get('symbol')).toBe('SPY');
    expect(params.get('filter')).toBe('ZERO_DTE');
  });
});
