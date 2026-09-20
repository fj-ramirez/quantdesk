/**
 * The ranked leaderboard (T78).
 *
 * **Rows below their own noise ceiling are visibly discounted**, not hidden. Hiding them would
 * be dishonest in the other direction — the fact that the best result of 134,377 trials is
 * still under the noise floor is the single most useful thing this page can tell you, and it
 * can only be told by showing those rows and marking them.
 *
 * The discounting is deliberately more than a colour: the row is dimmed, its Sharpe is
 * annotated with the ceiling it failed to clear, and a screen reader hears "below noise
 * ceiling" as text. Colour alone would fail anyone who cannot see it and would vanish in a
 * screenshot pasted into a chat.
 */
import { formatPct } from '../../../lib/format';
import type { LeaderboardRow } from '../api/types';

export interface LeaderboardTableProps {
  rows: LeaderboardRow[];
  onSelect: (hash: string) => void;
}

function sharpe(value: number | null): string {
  return value == null ? '—' : value.toFixed(2);
}

export function LeaderboardTable({ rows, onSelect }: LeaderboardTableProps) {
  return (
    <div className="leaderboard-table__scroll">
      <table className="leaderboard-table">
        <thead>
          <tr>
            <th scope="col">Market</th>
            <th scope="col">Strategy</th>
            <th scope="col">Symbol</th>
            <th scope="col">TF</th>
            <th scope="col" className="is-numeric">
              OOS Sharpe
            </th>
            <th scope="col" className="is-numeric">
              Ceiling
            </th>
            <th scope="col" className="is-numeric">
              CAGR
            </th>
            <th scope="col" className="is-numeric">
              Max DD
            </th>
            <th scope="col" className="is-numeric">
              Fills
            </th>
            <th scope="col" className="is-numeric">
              Exposure
            </th>
            <th scope="col" className="is-numeric">
              OOS yrs
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.hash}
              className={
                row.above_ceiling
                  ? 'leaderboard-table__row leaderboard-table__row--above'
                  : 'leaderboard-table__row leaderboard-table__row--below'
              }
              tabIndex={0}
              role="button"
              onClick={() => onSelect(row.hash)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect(row.hash);
                }
              }}
            >
              <td>{row.market}</td>
              <td>{row.strategy}</td>
              <td>{row.symbol}</td>
              <td>{row.timeframe}</td>
              <td className="is-numeric">
                {sharpe(row.oos_sharpe)}
                {/* Text, not just a colour: this is the verdict on the row, and it has to
                    survive a screen reader and a greyscale screenshot. */}
                {!row.above_ceiling && (
                  <span className="leaderboard-table__flag"> below noise ceiling</span>
                )}
              </td>
              <td className="is-numeric">{row.noise_ceiling.toFixed(2)}</td>
              <td className="is-numeric">{formatPct(row.oos_cagr)}</td>
              <td className="is-numeric">{formatPct(row.oos_max_dd)}</td>
              <td className="is-numeric">{row.oos_fills ?? '—'}</td>
              <td className="is-numeric">{formatPct(row.oos_exposure)}</td>
              <td className="is-numeric">{row.oos_years?.toFixed(1) ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
