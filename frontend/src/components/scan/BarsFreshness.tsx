/**
 * T55 — reads `GET /api/health/capture` and renders the `bars` block's freshness summary
 * ("Bars through Mon 8 Sep · 47 symbols") for the scan-family TopBar toolbar (07-ui.md:
 * "bars freshness ... from the `bars` block of `/api/health/capture`"). The stale count
 * renders in the accent colour when non-zero — via the `--accent` CSS token, never an inline
 * hex, same rule every other coloured element in this app follows.
 *
 * Date formatting (`formatBarsThrough`) lives in `lib/time.ts` next to this app's other
 * timestamp formatters, not here — see that file for why `last_bar_date` needs its own,
 * UTC-pinned formatting path rather than reusing `formatNyTime`.
 */
import { useCaptureHealth } from '../../api/queries';
import { formatBarsThrough } from '../../lib/time';

export function BarsFreshness() {
  const { data, isLoading, isError } = useCaptureHealth();

  if (isLoading) {
    return (
      <span className="bars-freshness" aria-live="polite">
        Loading bars freshness…
      </span>
    );
  }

  if (isError || !data) {
    return (
      <span className="bars-freshness" aria-live="polite">
        Bars freshness unavailable
      </span>
    );
  }

  const { bars } = data;
  const latest = bars.symbols.reduce<string | null>((max, row) => {
    if (row.last_bar_date == null) return max;
    return max == null || row.last_bar_date > max ? row.last_bar_date : max;
  }, null);

  return (
    <span className="bars-freshness" aria-live="polite">
      {latest
        ? `Bars through ${formatBarsThrough(latest)} · ${bars.symbols.length} symbols`
        : `${bars.symbols.length} symbols, no bars yet`}
      {bars.stale_count > 0 && (
        <span className="bars-freshness__stale"> · {bars.stale_count} stale</span>
      )}
    </span>
  );
}
