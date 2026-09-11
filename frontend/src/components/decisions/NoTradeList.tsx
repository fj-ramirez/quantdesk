/**
 * T60 -- the symbols the engine declined to suggest anything for, each with the backend's
 * own reasons (verbatim, in a disclosure like `VerdictCell`), plus the members with no chain
 * captured at all. Rendered so an empty ranking is never a blank page: "nothing to do" is an
 * answer with a cause, not an absence.
 */
import type { SymbolDecision } from '../../api/types';
import { SymbolCell } from '../scan/SymbolCell';

export interface NoTradeListProps {
  symbols: SymbolDecision[];
  noChain: string[];
  filterSearch: string;
}

export function NoTradeList({ symbols, noChain, filterSearch }: NoTradeListProps) {
  const quiet = symbols.filter((s) => s.opportunities.length === 0);
  if (quiet.length === 0 && noChain.length === 0) return null;
  return (
    <section className="decisions-notrade" aria-label="No trade">
      <h2 className="scan-layout__side-title">No trade</h2>
      {quiet.length > 0 && (
        <ul className="decisions-notrade__list">
          {quiet.map((s) => (
            <li key={s.underlying} className="decisions-notrade__item">
              <details>
                <summary>
                  <SymbolCell symbol={s.underlying} search={filterSearch} />{' '}
                  <span className="decisions-notrade__first">{s.no_trade_reasons[0]}</span>
                </summary>
                {s.no_trade_reasons.length > 1 && (
                  <ul className="decisions-notrade__reasons">
                    {s.no_trade_reasons.slice(1).map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                )}
              </details>
            </li>
          ))}
        </ul>
      )}
      {noChain.length > 0 && (
        <p className="decisions-notrade__nochain">
          No chain captured yet: {noChain.join(', ')}.
        </p>
      )}
    </section>
  );
}
