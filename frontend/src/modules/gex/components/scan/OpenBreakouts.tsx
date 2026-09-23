/**
 * Open-breakouts panel (T44, 07-ui.md's `/scan` section).
 *
 * Every still-`pending` event across the universe, sorted by `bars_elapsed` ascending so the
 * freshest break is on top -- the plan's ordering, and the useful one: an event two bars into
 * its k-bar window still has time to resolve, where one at k-1 is about to be decided.
 *
 * Empty is a real, frequent answer (a quiet range-bound week produces none), so it renders the
 * shared `EmptyState` rather than an empty list, per the T37 rule.
 */
import { EmptyState } from '../EmptyState';
import { Pagination } from '../../../../components/ui/Pagination';
import { usePagination } from '../../../../components/ui/usePagination';
import { SymbolCell } from './SymbolCell';
import { formatAtr, formatPrice } from '../../../../lib/format';
import type { OpenBreakout } from '../../api/types';

export interface OpenBreakoutsProps {
  breakouts: OpenBreakout[];
  /** The `k` this scan ran with, so "3 of 5 bars" reads as a fraction of its own window
   * rather than a bare count the reader has to remember the setting for. */
  k: number;
  onSelect?: (symbol: string) => void;
  /** T122: rows per page; omitted shows every open event. */
  pageSize?: number;
}

export function OpenBreakouts({ breakouts, k, onSelect, pageSize }: OpenBreakoutsProps) {
  const sorted = [...breakouts].sort((a, b) => a.bars_elapsed - b.bars_elapsed);
  const paged = usePagination(sorted, pageSize, `${k}|${breakouts.length}`);

  if (breakouts.length === 0) {
    return (
      <EmptyState heading="No open breakouts">
        No range break is currently inside its {k}-bar resolution window.
      </EmptyState>
    );
  }

  // T122: a compact table rather than one bordered card per event -- eighteen open events
  // used to be eighteen stacked cards, the tallest thing on both Overview and Scan.
  return (
    <>
      <div className="scan-table-container" tabIndex={0}>
        <table className="scan-table open-breakouts">
          <caption className="scan-table__caption">Breakouts still inside their {k}-bar window</caption>
          <thead>
            <tr>
              <th scope="col">Symbol</th>
              <th scope="col">Dir</th>
              <th scope="col" style={{ textAlign: 'right' }}>
                Level
              </th>
              <th scope="col" style={{ textAlign: 'right' }}>
                Elapsed
              </th>
              <th scope="col" style={{ textAlign: 'right' }}>
                Excursion
              </th>
            </tr>
          </thead>
          <tbody>
            {paged.pageRows.map((event) => (
              <tr className="scan-table__row" key={`${event.symbol}-${event.date}`}>
                <td>
                  {onSelect ? (
                    <button type="button" className="open-breakouts__symbol" onClick={() => onSelect(event.symbol)}>
                      {event.symbol}
                    </button>
                  ) : (
                    <SymbolCell symbol={event.symbol} />
                  )}
                </td>
                <td
                  className={
                    event.direction === 'up'
                      ? 'open-breakouts__dir open-breakouts__dir--up'
                      : 'open-breakouts__dir open-breakouts__dir--down'
                  }
                >
                  {event.direction === 'up' ? '↑ up' : '↓ down'}
                </td>
                <td style={{ textAlign: 'right' }}>{formatPrice(event.level)}</td>
                <td style={{ textAlign: 'right' }}>
                  {event.bars_elapsed} of {k} bars
                </td>
                <td style={{ textAlign: 'right' }}>{formatAtr(event.excursion_atr)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={paged.page}
        pageCount={paged.pageCount}
        total={paged.total}
        pageSize={paged.pageSize}
        onPage={paged.setPage}
        noun="open breakouts"
      />
    </>
  );
}
