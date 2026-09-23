/**
 * T60 -- `/decisions`' ranked table: a column set over T55's shared `ScanTable`, like every
 * other scan-family table. No markup, sorting or null handling reimplemented here.
 *
 * Entry / stop / target are the engine's own numbers, rendered with `formatPrice` (two
 * decimals) rather than `formatStrike`: a continuation entry is spot, and a stop is a strike
 * plus an ATR buffer -- neither is a strike, so strike formatting would round away the very
 * digits a resting order needs. The `*_label` sentences behind each level live in the detail
 * panel, not in the cell; a cell carries the number and the panel carries the reason.
 */
import type { ColumnDef } from '../scan/ScanTable';
import { ScanTable } from '../scan/ScanTable';
import { StatusChip } from '../scan/StatusChip';
import { SymbolCell } from '../scan/SymbolCell';
import type { SortDir } from '../../state/urlState';
import { formatAtr, formatPrice, formatRatio } from '../../../../lib/format';
import { setupLabel, type OpportunityRow } from './opportunityRows';

const DASH = '—';

/** Unsigned ATR multiple: 0.5 -> "0.50 ATR". `formatAtr` signs its figure because breakout
 * excursions are two-sided; a risk or reward distance is a magnitude and reads wrong with a
 * leading `+`. */
function formatAtrMagnitude(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return formatAtr(value).replace(/^\+/, '');
}

export interface OpportunityTableProps {
  rows: OpportunityRow[];
  /** The page's current expiry filter as a raw query fragment (`filter=ALL`), carried onto
   * the symbol link via `SymbolCell` so a click preserves it. */
  filterSearch: string;
  sort: string | null;
  dir: SortDir;
  onSort: (key: string) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** T122: rows per page, passed through to `ScanTable`. */
  pageSize?: number;
}

export function OpportunityTable({ rows, filterSearch, sort, dir, onSort, selectedId, onSelect, pageSize }: OpportunityTableProps) {
  const columns: ColumnDef<OpportunityRow>[] = [
    { key: 'rank', header: '#', align: 'right', sortable: true, format: (v) => String(v) },
    {
      key: 'symbol',
      header: 'Symbol',
      sortable: true,
      format: (v) => <SymbolCell symbol={String(v)} search={filterSearch} />,
    },
    {
      key: 'key',
      header: 'Setup',
      sortable: true,
      title: 'Fade: rest a limit at a wall under long gamma. Continuation: follow the 5-day move under short gamma or next to the flip.',
      format: (v, row) => (
        <span className="decisions-setup">
          <StatusChip status={row.setup} label={setupLabel(String(v))} />
        </span>
      ),
    },
    {
      key: 'side',
      header: 'Side',
      sortable: true,
      format: (v) => <span className={`decisions-side decisions-side--${String(v).toLowerCase()}`}>{String(v)}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      title: 'active: entry reachable now. watch: wall 1.5-3 ATR away. rejected: reward/risk below the floor.',
      format: (v) => <StatusChip status={String(v)} />,
    },
    {
      key: 'score',
      header: 'Grade',
      align: 'right',
      sortable: true,
      title: 'Score 0-100 with its breakdown in the detail panel. A >= 75, B >= 60, C >= 45.',
      format: (v, row) => (
        <span className="decisions-grade">
          <strong>{row.grade}</strong> <span className="scan-cell--muted">{String(v)}</span>
        </span>
      ),
    },
    { key: 'entry', header: 'Entry', align: 'right', sortable: true, format: (v) => formatPrice(v as number) },
    { key: 'stop', header: 'Stop', align: 'right', sortable: true, format: (v) => formatPrice(v as number) },
    { key: 'target', header: 'Target', align: 'right', sortable: true, format: (v) => formatPrice(v as number) },
    {
      key: 'rr',
      header: 'R:R',
      align: 'right',
      sortable: true,
      title: 'Reward to the primary target over risk to the stop.',
      format: (v) => formatRatio(v as number | null),
    },
    {
      key: 'risk_atr',
      header: 'Risk',
      align: 'right',
      sortable: true,
      title: 'Entry-to-stop distance in ATR(14).',
      format: (v) => formatAtrMagnitude(v as number | null),
    },
  ];

  return (
    <ScanTable
      columns={columns}
      rows={rows}
      sort={sort}
      dir={dir}
      onSort={onSort}
      rowKey={(row) => row.id}
      selectedKey={selectedId}
      onRowClick={(row) => onSelect(row.id)}
      caption="Ranked opportunities"
      pageSize={pageSize}
      pageNoun="opportunities"
    />
  );
}
