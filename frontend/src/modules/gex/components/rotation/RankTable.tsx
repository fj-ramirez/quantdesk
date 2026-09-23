/**
 * T51 -- `/rotation`'s rank table (07-ui.md: "symbol, relative return 1w, 4w, 13w (signed
 * pct), current quadrant chip; sorts by any window"). Built over T55's `ScanTable`, same as
 * every other scan-family table -- nothing here reimplements sorting, null handling or row
 * markup.
 *
 * `return_5`/`return_20`/`return_65` map to 1/4/13 weeks (5, 20 and 65 *trading days*, per
 * plan 04's "relative return... over n in {5, 20, 65} bars") -- the column headers use the
 * week count because that is what the toolbar and the chart's trail length are both counted
 * in, but the underlying field names stay the API's own bar counts.
 *
 * The row-shaping and quadrant logic (`toRankRows`, `quadrantOf`, `RANK_DEFAULT_SORT`) lives
 * in the sibling `rankRows.ts`, not here -- see that file's docstring for why (keeps this
 * file an export-only-a-component file, out of `react-refresh/only-export-components`).
 */
import type { ColumnDef, SortDir } from '../scan/ScanTable';
import { ScanTable } from '../scan/ScanTable';
import { SymbolCell } from '../scan/SymbolCell';
import { StatusChip } from '../scan/StatusChip';
import { formatSignedPct } from '../../../../lib/format';
import type { RotationSymbol } from '../../api/types';
import { QUADRANT_LABEL, toRankRows, type Quadrant, type RankRow } from './rankRows';

export interface RankTableProps {
  symbols: RotationSymbol[];
  sort: string | null;
  dir: SortDir;
  onSort: (key: string) => void;
  /** T122: rows per page, passed through to `ScanTable`; omitted shows every row. */
  pageSize?: number;
}

export function RankTable({ symbols, sort, dir, onSort, pageSize }: RankTableProps) {
  const rows = toRankRows(symbols);

  const columns: ColumnDef<RankRow>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      sortable: true,
      format: (_value, row) => <SymbolCell symbol={row.symbol} />,
    },
    {
      key: 'return_5',
      header: '1w',
      align: 'right',
      sortable: true,
      title: 'Relative return vs. the benchmark, 5 trading days',
      format: (value) => formatSignedPct(value as number | null),
    },
    {
      key: 'return_20',
      header: '4w',
      align: 'right',
      sortable: true,
      title: 'Relative return vs. the benchmark, 20 trading days',
      format: (value) => formatSignedPct(value as number | null),
    },
    {
      key: 'return_65',
      header: '13w',
      align: 'right',
      sortable: true,
      title: 'Relative return vs. the benchmark, 65 trading days',
      format: (value) => formatSignedPct(value as number | null),
    },
    {
      key: 'quadrant',
      header: 'Quadrant',
      sortable: true,
      title: 'Current RRG quadrant, from the latest trail point',
      format: (value) => {
        const quadrant = value as Quadrant | null;
        return <StatusChip status={quadrant} label={quadrant ? QUADRANT_LABEL[quadrant] : undefined} />;
      },
    },
  ];

  return (
    <ScanTable
      columns={columns}
      rows={rows}
      sort={sort}
      dir={dir}
      onSort={onSort}
      rowKey={(row) => row.symbol}
      caption="Relative return and current quadrant by symbol"
      pageSize={pageSize}
      pageNoun="symbols"
    />
  );
}
