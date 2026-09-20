/**
 * Per-symbol trend detail (T44, 07-ui.md's `/scan` Trend view).
 *
 * Four sparklines over `GET /api/gex/scan/trend/{symbol}`'s `history`, plus the `current` block as
 * a key-value list. `vr`, `vr_z`, `iv30` and `iv_rv_ratio` are deliberately absent from the
 * history (the API's own type says why: the variance ratio is a one-sample statistic with no
 * rolling per-day reading, and no IV history is persisted past the latest snapshot), so they
 * appear only once, under Current -- never as a flat line implying a series that was never
 * measured.
 *
 * T66: this used to render its own `<section>`/heading/Close button wrapper (no Escape
 * handler, no focus trap, no focus-return — `01-ux-baseline.md` flagged it explicitly). It is
 * now mounted as `ui/DetailDrawer`'s children (`Scan.tsx`), which owns that chrome and the
 * focus-management behavior neither this component nor its predecessor ever had — same
 * pattern T64 already applied to `OpportunityDetail`. This file renders only the content
 * below the drawer's own header.
 */
import { useSymbolTrend } from '../../api/queries';
import { EmptyState } from '../EmptyState';
import { ErrorState } from '../ErrorState';
import { Sparkline } from './Sparkline';
import { formatIv, formatRatio } from '../../../../lib/format';
import type { TrendHistoryPoint } from '../../api/types';

const DASH = '—';

const SERIES: { key: keyof Omit<TrendHistoryPoint, 'date'>; label: string }[] = [
  { key: 'adx14', label: 'ADX14' },
  { key: 'er20', label: 'ER20' },
  { key: 'chop14', label: 'CHOP14' },
  { key: 'rv20', label: 'RV20' },
];

function fixed(value: number | null | undefined, digits: number): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : DASH;
}

export interface TrendDetailProps {
  symbol: string;
}

export function TrendDetail({ symbol }: TrendDetailProps) {
  const query = useSymbolTrend(symbol);

  return (
    <>
      {query.isError ? (
        <ErrorState message={`Could not load trend detail for ${symbol}.`} />
      ) : query.isPending ? (
        <p className="scan-detail__loading">Loading {symbol}…</p>
      ) : (query.data?.history.length ?? 0) === 0 ? (
        <EmptyState heading="No history for this symbol">
          {symbol} has no stored bars yet, so no trend components could be computed.
        </EmptyState>
      ) : (
        <>
          <div className="trend-detail__sparks">
            {SERIES.map(({ key, label }) => (
              <div className="trend-detail__spark" key={key}>
                <span className="trend-detail__spark-label">{label}</span>
                <Sparkline
                  ariaLabel={`${label} history for ${symbol}`}
                  data={(query.data?.history ?? []).map((point) => ({
                    x: point.date,
                    y: point[key],
                  }))}
                />
              </div>
            ))}
          </div>
          <dl className="trend-detail__current">
            <dt>ADX14</dt>
            <dd>{fixed(query.data?.current.adx14, 1)}</dd>
            <dt>ER20</dt>
            <dd>{fixed(query.data?.current.er20, 2)}</dd>
            <dt>CHOP14</dt>
            <dd>{fixed(query.data?.current.chop14, 1)}</dd>
            <dt>RV20</dt>
            <dd>{formatIv(query.data?.current.rv20)}</dd>
            <dt>Variance ratio</dt>
            <dd>{fixed(query.data?.current.vr, 3)}</dd>
            <dt>VR z</dt>
            <dd>{fixed(query.data?.current.vr_z, 2)}</dd>
            <dt>IV30</dt>
            <dd>
              {query.data?.current.iv30 == null ? (
                <span title="No option chain tracked for this symbol">{DASH}</span>
              ) : (
                formatIv(query.data.current.iv30)
              )}
            </dd>
            <dt>IV/RV</dt>
            <dd>
              {query.data?.current.iv_rv_ratio == null ? (
                <span title="No option chain tracked for this symbol">{DASH}</span>
              ) : (
                formatRatio(query.data.current.iv_rv_ratio)
              )}
            </dd>
          </dl>
        </>
      )}
    </>
  );
}
