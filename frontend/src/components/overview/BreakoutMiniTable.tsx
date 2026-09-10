/**
 * T56 -- the "top by rate" / "fading" mini-tables on `/overview`. A column-definition set over
 * T55's shared `ScanTable`, same as every other scan-family table (`BreakoutTable.tsx`'s own
 * docstring) -- no markup, sorting or null handling reimplemented here. Unsortable: these are a
 * fixed-order top-N/worst-N cut computed by `overviewRows.ts`, not an interactive table, so
 * every column has `sortable` omitted and `onSort` is a no-op `ScanTable` never calls.
 *
 * Only three columns -- Symbol, Rate, Status -- deliberately fewer than the full
 * `BreakoutTable`'s eight; this is a summary, and the full breakdown is one click away via the
 * block's own header link.
 */
import type { ColumnDef } from '../scan/ScanTable';
import { ScanTable } from '../scan/ScanTable';
import { PercentileBar } from '../scan/PercentileBar';
import { StatusChip } from '../scan/StatusChip';
import { SymbolCell } from '../scan/SymbolCell';
import type { SymbolBreakoutSummary } from '../../api/types';

function noopSort() {
  // Unsortable: see this file's docstring.
}

export interface BreakoutMiniTableProps {
  rows: SymbolBreakoutSummary[];
  caption: string;
}

export function BreakoutMiniTable({ rows, caption }: BreakoutMiniTableProps) {
  const columns: ColumnDef<SymbolBreakoutSummary>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      format: (_value, row) => <SymbolCell symbol={row.symbol} />,
    },
    {
      key: 'rate',
      header: 'Rate',
      align: 'right',
      format: (value) => <PercentileBar value={value as number | null} />,
    },
    {
      key: 'status',
      header: 'Status',
      format: (value) => <StatusChip status={value as string | null} />,
    },
  ];

  return (
    <ScanTable
      columns={columns}
      rows={rows}
      sort={null}
      dir="desc"
      onSort={noopSort}
      rowKey={(row) => row.symbol}
      caption={caption}
    />
  );
}
