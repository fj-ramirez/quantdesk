/**
 * T55 — the link-or-text rule from 07-ui.md's "Information architecture": any symbol shown
 * on a scan page that has an option chain (`CORE_UNDERLYINGS ∪ EXTENDED_UNDERLYINGS`) is a
 * link to `/dashboard?symbol=XLK`, carrying the current page's filter/expiry scope (the
 * dashboard moved off `/` on 2026-09-10 when Overview became the landing page); any other symbol
 * (most of `SCAN_UNIVERSE` — 19 of 47 today, e.g. `^VIX`, `IWM`'s benchmark peers) is plain
 * text with a tooltip explaining why it isn't clickable, not a dead-looking link.
 */
import { Link } from 'react-router-dom';
import { CORE_UNDERLYINGS, EXTENDED_UNDERLYINGS } from '../../api/types';

const CHAIN_SYMBOLS: ReadonlySet<string> = new Set<string>([...CORE_UNDERLYINGS, ...EXTENDED_UNDERLYINGS]);

export interface SymbolCellProps {
  symbol: string;
  /** The current page's query string (e.g. `location.search`), carried onto the dashboard
   * link verbatim so a symbol click preserves the filter/expiry scope the user was already
   * looking at, then overwritten with this row's own `symbol`. */
  search?: string;
}

export function SymbolCell({ symbol, search }: SymbolCellProps) {
  if (!CHAIN_SYMBOLS.has(symbol)) {
    return (
      <span className="symbol-cell symbol-cell--plain" title="no option chain tracked">
        {symbol}
      </span>
    );
  }

  const params = new URLSearchParams(search);
  params.set('symbol', symbol);

  return (
    <Link className="symbol-cell symbol-cell--link" to={{ pathname: '/dashboard', search: `?${params.toString()}` }}>
      {symbol}
    </Link>
  );
}
