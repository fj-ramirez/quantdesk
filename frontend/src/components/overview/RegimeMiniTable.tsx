/**
 * T56 -- the "in continuation now" mini-table on `/overview`: the regime rows
 * `overviewRows.ts`'s `continuationRegimeRows` picked out (verdict is a genuine `continuation`
 * call, never a `stale`/`noise-dominated` null). Same pattern as `BreakoutMiniTable.tsx`: T55's
 * shared `ScanTable`, unsortable, a deliberately short column set -- Symbol, net/abs, Trend pct
 * -- compared to the full `RegimeTable`'s eleven; the Verdict chip is included anyway since
 * every row here shares the same value and a reader landing on this panel alone should still
 * see the word "continuation" attached to each row, not just infer it from the panel's own
 * heading.
 */
import type { ColumnDef } from '../scan/ScanTable';
import { ScanTable } from '../scan/ScanTable';
import { PercentileBar } from '../scan/PercentileBar';
import { StatusChip } from '../scan/StatusChip';
import { SymbolCell } from '../scan/SymbolCell';
import { formatPct } from '../../lib/format';
import type { RegimeRow } from '../regime/regimeRows';

function noopSort() {
  // Unsortable: see this file's docstring.
}

export interface RegimeMiniTableProps {
  rows: RegimeRow[];
  caption: string;
}

export function RegimeMiniTable({ rows, caption }: RegimeMiniTableProps) {
  const columns: ColumnDef<RegimeRow>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      format: (_value, row) => <SymbolCell symbol={row.symbol} />,
    },
    {
      key: 'verdict',
      header: 'Verdict',
      format: (value) => <StatusChip status={value as string | null} />,
    },
    {
      key: 'ratio',
      header: 'net/abs',
      align: 'right',
      format: (value) => formatPct(value as number | null),
    },
    {
      key: 'trendPct',
      header: 'Trend pct',
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
