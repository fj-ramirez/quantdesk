/**
 * The per-strike ladder, as a table.
 *
 * `by_strike` has been in the dashboard's payload since T09 and until now only ever reached
 * the screen as bar heights in `GexByStrike`. A chart answers "where is the gamma"; it does
 * not answer "how much is at 7,850, and how much of that is calls" — which is the question a
 * strike ladder exists for, and the reason every options analytics terminal puts one under
 * the chart. Nothing here is computed: every column is a field of `StrikeGex`, formatted with
 * the same helpers `KeyLevels` already uses.
 *
 * **Only columns that exist.** A conventional ladder also carries delta, volume and the
 * call/put open-interest split; this payload has none of them (`StrikeGex` is strike, the
 * three GEX figures, `abs_gex`, `contracts` and a single combined `open_interest`), so they
 * are absent rather than approximated. A column of plausible-looking numbers with nothing
 * behind them is the one thing a desk like this cannot ship.
 *
 * Rows that are also a named level — the call wall, the put wall, the gamma flip, and the
 * strike nearest spot — carry a tag, so the chart's five reference lines and this table agree
 * about which strikes matter without the reader matching pixel positions to numbers by eye.
 */
import type { KeyLevels, StrikeGex } from '../api/types';
import { formatCount, formatDistancePct, formatGex, formatStrike } from '../../../lib/format';

export interface StrikeTableProps {
  rows: StrikeGex[];
  levels: KeyLevels;
  spot: number;
  underlying: string;
}

type LevelTag = 'Call wall' | 'Put wall' | 'Flip' | 'Spot';

/** Which named level, if any, sits at this strike. The flip point is a computed price rather
 * than a strike, so it is attributed to the nearest strike row rather than dropped. */
function tagsFor(strike: number, levels: KeyLevels, nearestSpotStrike: number | null, nearestFlipStrike: number | null): LevelTag[] {
  const tags: LevelTag[] = [];
  if (levels.call_wall != null && strike === levels.call_wall) tags.push('Call wall');
  if (levels.put_wall != null && strike === levels.put_wall) tags.push('Put wall');
  if (nearestFlipStrike != null && strike === nearestFlipStrike) tags.push('Flip');
  if (nearestSpotStrike != null && strike === nearestSpotStrike) tags.push('Spot');
  return tags;
}

function nearestStrike(rows: StrikeGex[], target: number | null): number | null {
  if (target == null || rows.length === 0) return null;
  return rows.reduce((best, row) => (Math.abs(row.strike - target) < Math.abs(best - target) ? row.strike : best), rows[0].strike);
}

export function StrikeTable({ rows, levels, spot, underlying }: StrikeTableProps) {
  if (rows.length === 0) return null;

  const ordered = [...rows].sort((a, b) => b.strike - a.strike);
  const nearestSpotStrike = nearestStrike(rows, spot);
  const nearestFlipStrike = nearestStrike(rows, levels.flip_point);

  return (
    <div className="strike-table-scroll" tabIndex={0}>
      <table className="scan-table strike-table">
        <caption>
          {underlying} gamma exposure by strike: call, put and net GEX, open interest and
          contracts, with distance from spot. Highest strike first.
        </caption>
        <thead>
          <tr>
            <th scope="col">Strike</th>
            <th scope="col" className="num">Call GEX</th>
            <th scope="col" className="num">Put GEX</th>
            <th scope="col" className="num">Net GEX</th>
            <th scope="col" className="num">|GEX|</th>
            <th scope="col" className="num">OI</th>
            <th scope="col" className="num">Contracts</th>
            <th scope="col" className="num">Dist</th>
            <th scope="col">Level</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((row) => {
            const tags = tagsFor(row.strike, levels, nearestSpotStrike, nearestFlipStrike);
            return (
              <tr key={row.strike} className={tags.length > 0 ? 'strike-table__row--level' : undefined}>
                <th scope="row">{formatStrike(row.strike)}</th>
                <td className="num">{formatGex(row.call_gex)}</td>
                <td className="num">{formatGex(row.put_gex)}</td>
                {/* Sign carried by a class as well as the minus sign — the number is never the
                    only cue, the same rule `KeyLevels`' net-GEX dot follows. */}
                <td className={`num ${row.net_gex >= 0 ? 'strike-table__net--pos' : 'strike-table__net--neg'}`}>
                  {formatGex(row.net_gex)}
                </td>
                <td className="num">{formatGex(row.abs_gex)}</td>
                <td className="num">{formatCount(row.open_interest)}</td>
                <td className="num">{formatCount(row.contracts)}</td>
                <td className="num">{formatDistancePct(row.strike, spot)}</td>
                <td>
                  {tags.map((tag) => (
                    <span key={tag} className={`strike-table__tag strike-table__tag--${tag.split(' ')[0].toLowerCase()}`}>
                      {tag}
                    </span>
                  ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
