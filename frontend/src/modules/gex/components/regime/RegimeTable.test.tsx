import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { RegimeTable } from './RegimeTable';
import { toRegimeRows } from './regimeRows';
import type { RegimeSymbolRow } from '../../api/types';
import { ThemeProvider } from '../../../../theme/ThemeContext';
import regimeFixture from '../../mocks/fixtures/scan/regime.json';

const rows = regimeFixture.rows as unknown as RegimeSymbolRow[];

function renderTable(apiRows: RegimeSymbolRow[] = rows, sort: string | null = null) {
  const onSort = vi.fn();
  render(
    <ThemeProvider>
      <MemoryRouter>
        <RegimeTable rows={toRegimeRows(apiRows)} filterSearch="filter=ALL" sort={sort} dir="desc" onSort={onSort} />
      </MemoryRouter>
    </ThemeProvider>,
  );
  return onSort;
}

function findRow(symbol: string): RegimeSymbolRow {
  return rows.find((r) => r.underlying === symbol)!;
}

/** Scopes a query to one symbol's `<tr>` -- several status words (`continuation`, `stale`, ...)
 * repeat across the 28-row fixture, so an unscoped `screen.getByText` on one of them throws
 * for finding more than one match. */
function rowFor(symbol: string): HTMLElement {
  const table = screen.getByRole('table');
  const row = within(table)
    .getAllByRole('row')
    .find((r) => within(r).queryByText(symbol));
  if (!row) throw new Error(`no row rendered for ${symbol}`);
  return row;
}

describe('<RegimeTable />', () => {
  it('renders one row per symbol, carrying the current filter onto the symbol link', () => {
    renderTable();
    expect(screen.getAllByRole('row')).toHaveLength(rows.length + 1); // +1 header row
    const link = within(rowFor('SPY')).getByRole('link', { name: 'SPY' });
    const href = link.getAttribute('href')!;
    const params = new URLSearchParams(href.split('?')[1]);
    expect(params.get('symbol')).toBe('SPY');
    expect(params.get('filter')).toBe('ALL');
  });

  it('renders SPY\'s continuation verdict; expanding it lists the reason verbatim', () => {
    renderTable();
    const row = rowFor('SPY');
    const chip = within(row).getByText('continuation');
    chip.closest('summary')!.click();
    expect(within(row).getByText(findRow('SPY').reasons[0])).toBeInTheDocument();
  });

  it('renders XLE\'s fade verdict; expanding it lists every reason verbatim', () => {
    renderTable();
    const xle = findRow('XLE');
    expect(xle.verdict).toBe('fade');
    const row = rowFor('XLE');
    within(row).getByText('fade').closest('summary')!.click();
    for (const reason of xle.reasons) {
      expect(within(row).getByText(reason)).toBeInTheDocument();
    }
  });

  it('renders a stale row (XLRE) with the literal "stale" chip and its suppression reason verbatim', () => {
    renderTable();
    const xlre = findRow('XLRE');
    expect(xlre.stale).toBe(true);
    expect(xlre.verdict).toBeNull();
    const row = rowFor('XLRE');
    within(row).getByText('stale').closest('summary')!.click();
    expect(within(row).getByText(xlre.reasons[0])).toBeInTheDocument();
  });

  it('renders a noise-dominated row (XLK) with the literal "noise-dominated" chip, a different word than "stale"', () => {
    renderTable();
    const xlk = findRow('XLK');
    expect(xlk.stale).toBe(false);
    expect(xlk.positioning?.noise_dominated).toBe(true);
    const row = rowFor('XLK');
    within(row).getByText('noise-dominated').closest('summary')!.click();
    expect(within(row).getByText(xlk.reasons[0])).toBeInTheDocument();
    expect(within(row).queryByText('stale')).not.toBeInTheDocument();
  });

  it('never renders the 0DTE share as 0% or a bare, unexplained dash', () => {
    renderTable();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    const dashes = screen.getAllByTitle(/not observable from an end-of-day chain/i);
    // Every one of the 28 fixture rows has zero_dte_share: null.
    expect(dashes).toHaveLength(rows.length);
  });

  it('renders a plain dash, not a fabricated wall, for XLRE\'s missing wall_below', () => {
    renderTable();
    const row = rowFor('XLRE');
    // XLRE's wall_below, room-beyond and 0DTE share are all null in the live fixture -- at
    // least one dash renders in the row.
    expect(within(row).getAllByText('—').length).toBeGreaterThanOrEqual(1);
  });

  it('calls onSort with the column key when a sortable header is activated', () => {
    const onSort = renderTable();
    screen.getByRole('button', { name: /net\/abs/i }).click();
    expect(onSort).toHaveBeenCalledWith('ratio');
  });

  it('marks the active sort column with aria-sort', () => {
    renderTable(rows, 'netGex');
    expect(screen.getByRole('columnheader', { name: /net gex/i })).toHaveAttribute('aria-sort', 'descending');
  });
});
