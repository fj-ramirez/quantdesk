/**
 * Breakouts summary table (T44, plans/continuation/07-ui.md's `/scan` section).
 *
 * A column-definition set over T55's shared `ScanTable`; no table markup, sorting or null
 * handling is reimplemented here. Two columns carry the judgment calls worth reading:
 *
 *  - **Rate.** `rate` is `null` below the five-event floor, and at `lookback=40` that is 46 of
 *    47 universe symbols -- the common case, not an edge case. It renders the literal text
 *    `n<5` with the event count in the tooltip, deliberately *not* the em dash every other
 *    formatter produces: a dash reads as "no data", where the real meaning is "too few events
 *    to quote a rate from". This is the case `ScanTable`'s docstring points at when it explains
 *    why null rendering belongs to a column's `format` rather than being forced table-wide.
 *  - **Last event.** Formats from `last_event`, which is `null` for a symbol with zero events
 *    in the window, so the whole cell degrades to a dash rather than throwing on a field read.
 */
import type { ColumnDef } from './ScanTable';
import { ScanTable } from './ScanTable';
import type { SortDir } from '../../state/urlState';
import { PercentileBar } from './PercentileBar';
import { StatusChip } from './StatusChip';
import { SymbolCell } from './SymbolCell';
import { formatAtr } from '../../../../lib/format';
import type { SymbolBreakoutSummary } from '../../api/types';

const DASH = '—';

/** `13 Aug ↑` -- the plan's exact format. Day then abbreviated month, then a direction arrow.
 * Built from parts rather than a single `Intl.DateTimeFormat` call because `en-US` renders
 * "Aug 13", not the "13 Aug" the spec asks for. */
function formatLastEvent(summary: SymbolBreakoutSummary): string {
  const event = summary.last_event;
  if (!event) return DASH;
  const parsed = new Date(`${event.date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return DASH;
  const day = parsed.getUTCDate();
  const month = parsed.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' });
  const arrow = event.direction === 'up' ? '↑' : '↓';
  return `${day} ${month} ${arrow}`;
}

export const BREAKOUT_DEFAULT_SORT = 'rate';

export interface BreakoutTableProps {
  rows: SymbolBreakoutSummary[];
  sort: string | null;
  dir: SortDir;
  onSort: (key: string) => void;
  onRowClick: (row: SymbolBreakoutSummary) => void;
  selectedSymbol: string | null;
  /** T122: rows per page, passed through to `ScanTable`; omitted shows every row. */
  pageSize?: number;
}

export function BreakoutTable({
  rows,
  sort,
  dir,
  onSort,
  onRowClick,
  selectedSymbol,
  pageSize,
}: BreakoutTableProps) {
  const columns: ColumnDef<SymbolBreakoutSummary>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      sortable: true,
      format: (_value, row) => <SymbolCell symbol={row.symbol} />,
    },
    {
      key: 'events',
      header: 'Events',
      align: 'right',
      sortable: true,
      format: (value) => String(value ?? DASH),
    },
    {
      key: 'continued',
      header: 'Cont. / Fail / Pend.',
      align: 'right',
      title: 'Continued, failed and still-pending events in the lookback window',
      format: (_value, row) => `${row.continued} / ${row.failed} / ${row.pending}`,
    },
    {
      key: 'rate',
      header: 'Rate',
      align: 'right',
      sortable: true,
      title: 'Share of resolved events that continued; needs at least five events',
      format: (value, row) => {
        if (value == null) {
          return (
            <span
              className="scan-cell--muted"
              title={`Only ${row.events} event${row.events === 1 ? '' : 's'} in this window; a continuation rate needs at least five`}
            >
              n&lt;5
            </span>
          );
        }
        return <PercentileBar value={value as number} />;
      },
    },
    {
      key: 'mean_follow_through_atr',
      header: 'Follow-through',
      align: 'right',
      sortable: true,
      title: 'Mean signed follow-through of resolved events, in ATR multiples',
      format: (value) => formatAtr(value as number | null),
    },
    {
      key: 'last_event',
      header: 'Last event',
      align: 'right',
      format: (_value, row) => formatLastEvent(row),
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
      sort={sort}
      dir={dir}
      onSort={onSort}
      onRowClick={onRowClick}
      rowKey={(row) => row.symbol}
      selectedKey={selectedSymbol}
      caption="Breakout continuation by symbol"
      pageSize={pageSize}
      pageNoun="symbols"
    />
  );
}
