/**
 * The launcher's quote strip.
 *
 * Real numbers or nothing. This reads the same `/api/gex/scan/regime` payload the regime
 * board already renders (`useRegime`, one cached query, no new endpoint) and shows the two
 * fields on it that need no interpretation: last spot and the five-day return. It is labelled
 * "5D" and dated from the row's own `effective_at`, because an unlabelled, undated percentage
 * next to a ticker reads as *today's* change — and this desk never shows a number whose
 * meaning it hasn't stated.
 *
 * Renders nothing at all while loading, on error, or when no core symbol has a spot yet (the
 * normal state of a fresh install, before the first capture). The launcher must not turn a
 * decorative strip into a broken-looking page.
 */
import { useRegime } from '../modules/gex/api/queries';
import { CORE_UNDERLYINGS } from '../modules/gex/api/types';
import { formatPrice, formatSignedPct } from '../lib/format';
import { formatNyDateTime } from '../lib/time';

export function LauncherTape() {
  const { data } = useRegime('ALL');

  const rows = (data?.rows ?? [])
    .filter((row) => row.spot != null && (CORE_UNDERLYINGS as readonly string[]).includes(row.underlying))
    .sort(
      (a, b) =>
        CORE_UNDERLYINGS.indexOf(a.underlying as (typeof CORE_UNDERLYINGS)[number]) -
        CORE_UNDERLYINGS.indexOf(b.underlying as (typeof CORE_UNDERLYINGS)[number]),
    );

  if (rows.length === 0) return null;

  const asOf = rows.find((row) => row.effective_at)?.effective_at ?? null;

  return (
    <aside className="lx-tape" aria-label="Core symbols at the last capture">
      <table className="lx-tape__table">
        <caption className="lx-tape__caption">
          Last capture{asOf ? ` · ${formatNyDateTime(asOf)}` : ''}
        </caption>
        <thead>
          <tr>
            <th scope="col">Symbol</th>
            <th scope="col">Spot</th>
            <th scope="col">5D</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const direction = row.return_5d == null ? 'flat' : row.return_5d >= 0 ? 'up' : 'down';
            return (
              <tr key={row.underlying}>
                <th scope="row">{row.underlying}</th>
                <td>{formatPrice(row.spot)}</td>
                <td className={`lx-tape__change lx-tape__change--${direction}`}>{formatSignedPct(row.return_5d)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </aside>
  );
}
