/**
 * T56 -- the "top by trend composite" mini-table on `/overview`. Same pattern as
 * `BreakoutMiniTable.tsx`: a fixed-order top-N cut (`overviewRows.ts`'s `rankTrendByComposite`)
 * over T55's shared `ScanTable`, unsortable, two columns -- Symbol and Composite -- deliberately
 * fewer than the full `TrendTable`'s nine.
 */
import type { ColumnDef } from '../scan/ScanTable';
import { ScanTable } from '../scan/ScanTable';
import { PercentileBar } from '../scan/PercentileBar';
import { SymbolCell } from '../scan/SymbolCell';
import type { TrendRow } from '../../api/types';

function noopSort() {
  // Unsortable: see this file's docstring.
}

export interface TrendMiniTableProps {
  rows: TrendRow[];
  caption: string;
}

export function TrendMiniTable({ rows, caption }: TrendMiniTableProps) {
  const columns: ColumnDef<TrendRow>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      format: (_value, row) => <SymbolCell symbol={row.symbol} />,
    },
    {
      key: 'composite',
      header: 'Composite',
      align: 'right',
      format: (value) => <PercentileBar value={value as number | null} />,
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
