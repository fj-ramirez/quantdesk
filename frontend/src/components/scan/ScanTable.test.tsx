import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ScanTable, type ColumnDef } from './ScanTable';

interface Row {
  symbol: string;
  rate: number | null;
}

const DASH = '—';

function formatRate(value: number | null): string {
  return value == null ? DASH : `${Math.round(value * 100)}%`;
}

const columns: ColumnDef<Row>[] = [
  { key: 'symbol', header: 'Symbol', sortable: true, format: (v) => String(v) },
  { key: 'rate', header: 'Rate', align: 'right', sortable: true, format: (v) => formatRate(v as number | null) },
];

const rows: Row[] = [
  { symbol: 'AAA', rate: 0.5 },
  { symbol: 'BBB', rate: null },
  { symbol: 'CCC', rate: 0.9 },
  { symbol: 'DDD', rate: 0.1 },
];

function renderTable(overrides: Partial<React.ComponentProps<typeof ScanTable<Row>>> = {}) {
  const onSort = overrides.onSort ?? vi.fn();
  const onRowClick = overrides.onRowClick;
  return render(
    <ScanTable<Row>
      columns={columns}
      rows={rows}
      sort={overrides.sort ?? null}
      dir={overrides.dir ?? 'desc'}
      onSort={onSort}
      onRowClick={onRowClick}
      rowKey={(row) => row.symbol}
      selectedKey={overrides.selectedKey}
      caption="Test table"
    />,
  );
}

function bodyRowTexts() {
  const rowsInOrder = screen.getAllByRole('row').slice(1); // drop the header row
  return rowsInOrder.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent));
}

describe('ScanTable', () => {
  it('renders every row unsorted when `sort` is null', () => {
    renderTable();
    expect(bodyRowTexts().map((cells) => cells[0])).toEqual(['AAA', 'BBB', 'CCC', 'DDD']);
  });

  it('renders a null cell as an em dash via the column\'s own formatter, never 0 or blank', () => {
    renderTable();
    const bbbRow = screen.getByText('BBB').closest('tr')!;
    expect(within(bbbRow).getByText(DASH)).toBeInTheDocument();
  });

  it('sorts descending by a numeric column with null pinned to the end', () => {
    renderTable({ sort: 'rate', dir: 'desc' });
    const symbols = bodyRowTexts().map((cells) => cells[0]);
    // 0.9, 0.5, 0.1 descending, then the null row last -- not first.
    expect(symbols).toEqual(['CCC', 'AAA', 'DDD', 'BBB']);
  });

  it('sorts ascending by the same numeric column with null STILL pinned to the end', () => {
    renderTable({ sort: 'rate', dir: 'asc' });
    const symbols = bodyRowTexts().map((cells) => cells[0]);
    // 0.1, 0.5, 0.9 ascending, null still last -- the likely first-contact failure the plan
    // calls out: a naive comparator would put null first under `asc`.
    expect(symbols).toEqual(['DDD', 'AAA', 'CCC', 'BBB']);
  });

  it('marks the active sort column with aria-sort, and no others', () => {
    renderTable({ sort: 'rate', dir: 'asc' });
    const headers = screen.getAllByRole('columnheader');
    const rateHeader = headers.find((h) => h.textContent?.includes('Rate'))!;
    const symbolHeader = headers.find((h) => h.textContent?.includes('Symbol'))!;
    expect(rateHeader).toHaveAttribute('aria-sort', 'ascending');
    expect(symbolHeader).not.toHaveAttribute('aria-sort');
  });

  it('calls onSort with the column key when a sortable header button is clicked', () => {
    const onSort = vi.fn();
    renderTable({ onSort });
    fireEvent.click(screen.getByRole('button', { name: 'Rate' }));
    expect(onSort).toHaveBeenCalledWith('rate');
  });

  it('header cells with a sortable column render as buttons (keyboard-operable)', () => {
    renderTable();
    expect(screen.getByRole('button', { name: 'Symbol' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Rate' })).toBeInTheDocument();
  });

  it('rows are focusable and Enter opens the detail when onRowClick is given', () => {
    const onRowClick = vi.fn();
    renderTable({ onRowClick });
    const row = screen.getByText('AAA').closest('tr')!;
    expect(row).toHaveAttribute('tabindex', '0');
    fireEvent.click(row);
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
    fireEvent.keyDown(row, { key: 'Enter' });
    expect(onRowClick).toHaveBeenCalledTimes(2);
  });

  it('rows are not focusable when no onRowClick is given', () => {
    renderTable();
    const row = screen.getByText('AAA').closest('tr')!;
    expect(row).not.toHaveAttribute('tabindex');
  });

  it('marks the selected row via aria-selected', () => {
    renderTable({ onRowClick: vi.fn(), selectedKey: 'CCC' });
    expect(screen.getByText('CCC').closest('tr')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('AAA').closest('tr')).toHaveAttribute('aria-selected', 'false');
  });
});
