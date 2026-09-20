/**
 * Per-symbol breakout detail (T44, 07-ui.md's `/scan` section).
 *
 * The candlestick plus the event table for one symbol. Two things worth knowing:
 *
 *  - **`start` is derived from the lookback, not hardcoded.** The chart must span the same
 *    window the summary row was computed over, or a marker sits off the left edge and the
 *    reader compares an event count against a chart that cannot show them all. Calendar days
 *    are deliberately over-requested relative to trading days (a 126-bar window spans ~180
 *    calendar days) since the API clips to what exists.
 *  - **`^VIX` reaches here unencoded** and is percent-encoded inside `api/client.ts` (T55).
 *    Nothing in this component may pre-encode it, or the caret would be double-escaped.
 *
 * T66: this used to render its own `<section>`/heading/Close button wrapper (no Escape
 * handler, no focus trap, no focus-return — `01-ux-baseline.md` flagged it explicitly). It is
 * now mounted as `ui/DetailDrawer`'s children (`Scan.tsx`), which owns that chrome and the
 * focus-management behavior neither this component nor its predecessor ever had — same
 * pattern T64 already applied to `OpportunityDetail`. This file renders only the content
 * below the drawer's own header.
 */
import { useSymbolBreakouts, useBars } from '../../api/queries';
import { EmptyState } from '../EmptyState';
import { ErrorState } from '../ErrorState';
import { EventChart } from './EventChart';
import { StatusChip } from './StatusChip';
import { formatAtr, formatPrice } from '../../../../lib/format';

const DASH = '—';

/** Trading days -> calendar days, with room to spare. See the docstring's first point. */
function startDateFor(lookback: number): string {
  const days = Math.ceil(lookback * 1.5) + 10;
  const start = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
  return start.toISOString().slice(0, 10);
}

export interface BreakoutDetailProps {
  symbol: string;
  n: number;
  k: number;
  lookback: number;
  onMarkersRendered?: (count: number) => void;
}

export function BreakoutDetail({ symbol, n, k, lookback, onMarkersRendered }: BreakoutDetailProps) {
  const events = useSymbolBreakouts(symbol, { n, k, lookback });
  const bars = useBars(symbol, { start: startDateFor(lookback) });

  return (
    <>
      {events.isError || bars.isError ? (
        <ErrorState message={`Could not load breakout detail for ${symbol}.`} />
      ) : events.isPending || bars.isPending ? (
        <p className="scan-detail__loading">Loading {symbol}…</p>
      ) : (
        <>
          <EventChart
            bars={bars.data ?? []}
            events={events.data?.events ?? []}
            onMarkersRendered={onMarkersRendered}
          />
          {(events.data?.events.length ?? 0) === 0 ? (
            <EmptyState heading="No events in this window">
              {symbol} did not break its {n}-bar range over the last {lookback} bars.
            </EmptyState>
          ) : (
            <div className="scan-table-container" tabIndex={0}>
              <table className="scan-table">
                <caption className="scan-detail__caption">
                  Breakout events for {symbol}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Dir</th>
                    <th scope="col">Level</th>
                    <th scope="col">Close</th>
                    <th scope="col">Outcome</th>
                    <th scope="col">Follow-through</th>
                    <th scope="col">MFE</th>
                    <th scope="col">MAE</th>
                  </tr>
                </thead>
                <tbody>
                  {events.data?.events.map((event) => (
                    <tr key={`${event.date}-${event.direction}`}>
                      <td>{event.date}</td>
                      <td>{event.direction === 'up' ? '↑' : '↓'}</td>
                      <td>{formatPrice(event.level)}</td>
                      <td>{formatPrice(event.close)}</td>
                      <td>
                        <StatusChip status={event.outcome} />
                      </td>
                      <td>{formatAtr(event.follow_through_atr)}</td>
                      <td>{formatAtr(event.mfe_atr)}</td>
                      <td>{formatAtr(event.mae_atr)}</td>
                    </tr>
                  )) ?? (
                    <tr>
                      <td colSpan={8}>{DASH}</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}
