/**
 * Trend/chop table (T44, absorbing the old T46; 07-ui.md's `/scan` Trend view).
 *
 * Column definitions over T55's `ScanTable`. The one rule this file exists to hold:
 * **`iv30` and `iv_rv_ratio` are `null` for 19 of the 47 universe symbols** -- the bars
 * universe is wider than the option universe, and those 19 have no chain at all. That is a
 * permanent, correct answer, not a loading state and not a zero, so the cell shows an em dash
 * with "no option chain tracked" in the tooltip. The legend above the table states the other
 * half of the rule: IV/RV is displayed but is *not* part of the composite, so a reader never
 * has to guess whether those 19 symbols are being ranked on fewer components than the rest.
 */
import type { ColumnDef } from './ScanTable';
import { ScanTable } from './ScanTable';
import type { SortDir } from '../../state/urlState';
import { PercentileBar } from './PercentileBar';
import { SymbolCell } from './SymbolCell';
import { formatIv, formatRatio } from '../../lib/format';
import type { TrendRow } from '../../api/types';

const DASH = '—';

function fixed(value: unknown, digits: number): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : DASH;
}

function signedFixed(value: unknown, digits: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DASH;
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
}

/** "63rd pct" for a 0..1 percentile, or a plain note when the component is missing. */
function pctTooltip(label: string, pct: number | null): string {
  if (pct == null) return `${label}: not ranked (insufficient history)`;
  return `${label}: ${Math.round(pct * 100)}th percentile across the universe`;
}

export const TREND_DEFAULT_SORT = 'composite';

export interface TrendTableProps {
  rows: TrendRow[];
  sort: string | null;
  dir: SortDir;
  onSort: (key: string) => void;
  onRowClick: (row: TrendRow) => void;
  selectedSymbol: string | null;
}

export function TrendTable({
  rows,
  sort,
  dir,
  onSort,
  onRowClick,
  selectedSymbol,
}: TrendTableProps) {
  const columns: ColumnDef<TrendRow>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      sortable: true,
      format: (_value, row) => <SymbolCell symbol={row.symbol} />,
    },
    {
      key: 'composite',
      header: 'Composite',
      align: 'right',
      sortable: true,
      title: 'Mean of the four cross-sectional percentile ranks',
      format: (value) => <PercentileBar value={value as number | null} />,
    },
    {
      key: 'adx14',
      header: 'ADX14',
      align: 'right',
      sortable: true,
      format: (value, row) => (
        <span title={pctTooltip('ADX14', row.adx_pct)}>{fixed(value, 1)}</span>
      ),
    },
    {
      key: 'er20',
      header: 'ER20',
      align: 'right',
      sortable: true,
      format: (value, row) => (
        <span title={pctTooltip('ER20', row.er_pct)}>{fixed(value, 2)}</span>
      ),
    },
    {
      key: 'chop14',
      header: 'CHOP14',
      align: 'right',
      sortable: true,
      format: (value, row) => (
        <span title={pctTooltip('CHOP14', row.chop_pct)}>{fixed(value, 1)}</span>
      ),
    },
    {
      key: 'vr_z',
      header: 'VR z',
      align: 'right',
      sortable: true,
      format: (value, row) => (
        <span
          title={
            row.vr == null
              ? 'Variance ratio: not computed (insufficient history)'
              : `Variance ratio ${row.vr.toFixed(3)} (1.0 = random walk)`
          }
        >
          {signedFixed(value, 2)}
        </span>
      ),
    },
    {
      key: 'rv20',
      header: 'RV20',
      align: 'right',
      sortable: true,
      title: 'Annualized realized volatility over 20 bars',
      format: (value) => formatIv(value as number | null),
    },
    {
      key: 'iv30',
      header: 'IV30',
      align: 'right',
      sortable: true,
      format: (value) =>
        value == null ? (
          <span className="scan-cell--muted" title="No option chain tracked for this symbol">
            {DASH}
          </span>
        ) : (
          formatIv(value as number)
        ),
    },
    {
      key: 'iv_rv_ratio',
      header: 'IV/RV',
      align: 'right',
      sortable: true,
      title: 'Implied over realized volatility; shown but not part of the composite',
      format: (value) =>
        value == null ? (
          <span className="scan-cell--muted" title="No option chain tracked for this symbol">
            {DASH}
          </span>
        ) : (
          formatRatio(value as number)
        ),
    },
  ];

  return (
    <>
      <p className="scan-legend">
        Composite is the mean of four cross-sectional percentile ranks (ADX, ER, CHOP, VR).
        IV/RV is shown but is not part of it, so symbols with no option chain are ranked on the
        same four components as everything else.
      </p>
      <ScanTable
        columns={columns}
        rows={rows}
        sort={sort}
        dir={dir}
        onSort={onSort}
        onRowClick={onRowClick}
        rowKey={(row) => row.symbol}
        selectedKey={selectedSymbol}
        caption="Trend and chop by symbol"
      />
    </>
  );
}
