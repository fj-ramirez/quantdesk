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
import { SymbolCell } from './SymbolCell';
import { formatAtr, formatPrice } from '../../lib/format';
import type { OpenBreakout } from '../../api/types';

export interface OpenBreakoutsProps {
  breakouts: OpenBreakout[];
  /** The `k` this scan ran with, so "3 of 5 bars" reads as a fraction of its own window
   * rather than a bare count the reader has to remember the setting for. */
  k: number;
  onSelect?: (symbol: string) => void;
}

export function OpenBreakouts({ breakouts, k, onSelect }: OpenBreakoutsProps) {
  if (breakouts.length === 0) {
    return (
      <EmptyState heading="No open breakouts">
        No range break is currently inside its {k}-bar resolution window.
      </EmptyState>
    );
  }

  const sorted = [...breakouts].sort((a, b) => a.bars_elapsed - b.bars_elapsed);

  return (
    <ul className="open-breakouts">
      {sorted.map((event) => (
        <li className="open-breakouts__item" key={`${event.symbol}-${event.date}`}>
          <div className="open-breakouts__head">
            {onSelect ? (
              <button
                type="button"
                className="open-breakouts__symbol"
                onClick={() => onSelect(event.symbol)}
              >
                {event.symbol}
              </button>
            ) : (
              <SymbolCell symbol={event.symbol} />
            )}
            <span
              className={
                event.direction === 'up'
                  ? 'open-breakouts__dir open-breakouts__dir--up'
                  : 'open-breakouts__dir open-breakouts__dir--down'
              }
            >
              {event.direction === 'up' ? '↑ up' : '↓ down'}
            </span>
          </div>
          <dl className="open-breakouts__meta">
            <dt>Level</dt>
            <dd>{formatPrice(event.level)}</dd>
            <dt>Elapsed</dt>
            <dd>
              {event.bars_elapsed} of {k} bars
            </dd>
            <dt>Excursion</dt>
            <dd>{formatAtr(event.excursion_atr)}</dd>
          </dl>
        </li>
      ))}
    </ul>
  );
}
