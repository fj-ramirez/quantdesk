/**
 * T65 -- the 5-symbol (SPX/SPY/QQQ/GLD/DIA) option-chain capture freshness, the "tape/
 * freshness" half of `plans/ui-ux-refresh/README.md`'s Overview reading order. Reads
 * `GET /api/health/capture`'s `symbols` block (T29's "core five", `SymbolCaptureHealth`) --
 * the same endpoint `BarsFreshness` (components/scan/BarsFreshness.tsx) already reads for its
 * own `bars` block, so this adds no new backend call shape, only a second consumer of an
 * endpoint the app already fetches elsewhere (Flows.tsx, BarsFreshness).
 *
 * **T69 -- compacted.** This used to render one full `MetricCard` per symbol (five cards, the
 * user's own first-pass-review note: "freshness alone consumes several rows before the user
 * reaches the ranked signals"). Now it's one `density="compact"` `MetricCard` reading as a
 * sentence -- "N/5 chains fresh [status dot] <All fresh | k stale: SYM, SYM> · latest capture
 * <time>" -- with the original per-symbol `MetricStrip` (T65's own breakdown, byte-identical)
 * moved behind a native `<details>` disclosure, collapsed by default, the same pattern
 * Rotation's "About this chart" already uses. Every value is still the API's own field, read
 * once already by `BarsFreshness`/`Flows.tsx` for the same endpoint's other blocks -- nothing
 * here recomputes staleness, and the summary line never fabricates "all fresh" when a symbol is
 * actually stale or never-captured: the stale count and the affected symbols' tickers are named
 * in the summary's own status label, not hidden behind the disclosure.
 *
 * `stale` and `last_capture_at` are the API's own fields; a symbol with no capture yet (`
 * last_capture_at: null`) is excluded from the "latest capture" reduction (never a fabricated
 * timestamp) and, in the collapsed per-symbol table, still reads "No capture yet" verbatim.
 */
import { useCaptureHealth } from '../../api/queries';
import { formatNyDateTime, formatNyTime } from '../../lib/time';
import { ErrorState } from '../ErrorState';
import { LoadingState } from '../LoadingState';
import { MetricCard, MetricStrip } from '../ui/MetricCard';

export function CaptureFreshnessStrip() {
  const { data, isPending, isError } = useCaptureHealth();

  if (isError) return <ErrorState message="Could not load capture freshness." />;
  if (isPending) return <LoadingState message="Loading capture freshness…" />;
  if (!data) return null;

  const symbols = data.symbols;
  const staleSymbols = symbols.filter((s) => s.stale);
  const freshCount = symbols.length - staleSymbols.length;

  // The most recent `last_capture_at` across the five -- `null` (never captured) symbols are
  // excluded from this reduction rather than treated as "oldest", since `null` means unknown,
  // not zero (CLAUDE.md invariant #3, applied here to a timestamp rather than open interest).
  const latestCaptureIso = symbols.reduce<string | null>((latest, s) => {
    if (!s.last_capture_at) return latest;
    return !latest || s.last_capture_at > latest ? s.last_capture_at : latest;
  }, null);

  const statusLabel =
    staleSymbols.length === 0 ? 'All fresh' : `${staleSymbols.length} stale: ${staleSymbols.map((s) => s.underlying).join(', ')}`;

  const hint = latestCaptureIso ? `· latest capture ${formatNyTime(latestCaptureIso)}` : '· no capture yet for any symbol';

  return (
    <div className="capture-freshness" data-testid="capture-freshness">
      <MetricCard
        density="compact"
        label="Option-chain capture freshness"
        value={`${freshCount}/${symbols.length} chains fresh`}
        status={{ tone: staleSymbols.length > 0 ? 'caution' : 'positive', label: statusLabel }}
        hint={hint}
      />
      <details className="capture-freshness__detail">
        <summary>Per-symbol capture detail</summary>
        <MetricStrip
          label="Option-chain capture freshness by symbol"
          metrics={symbols.map((s) => ({
            metricKey: s.underlying,
            label: s.underlying,
            value: s.last_capture_at ? formatNyDateTime(s.last_capture_at) : 'No capture yet',
            status: {
              tone: s.stale ? 'caution' : 'positive',
              label: s.stale ? 'Stale' : 'Fresh',
            },
          }))}
        />
      </details>
    </div>
  );
}
