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
 */
import { useSymbolBreakouts, useBars } from '../../api/queries';
import { EmptyState } from '../EmptyState';
import { ErrorState } from '../ErrorState';
import { EventChart } from './EventChart';
import { StatusChip } from './StatusChip';
import { formatAtr, formatPrice } from '../../lib/format';

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
  onClose: () => void;
  onMarkersRendered?: (count: number) => void;
}

export function BreakoutDetail({
  symbol,
  n,
  k,
  lookback,
  onClose,
  onMarkersRendered,
}: BreakoutDetailProps) {
  const events = useSymbolBreakouts(symbol, { n, k, lookback });
  const bars = useBars(symbol, { start: startDateFor(lookback) });

  return (
    <section className="scan-detail" aria-label={`${symbol} breakout detail`}>
      <header className="scan-detail__head">
        <h2 className="scan-detail__title">{symbol}</h2>
        <button type="button" className="scan-detail__close" onClick={onClose}>
          Close
        </button>
      </header>

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
            <div className="scan-table-container">
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
    </section>
  );
}
