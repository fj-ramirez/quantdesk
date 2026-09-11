import { useEffect, useState } from 'react';
import { UNDERLYINGS, type Underlying, type KeyLevels as KeyLevelsData } from '../api/types';
import { useGexResult } from '../api/queries';
import { useDashboardParams } from '../state/urlState';
import { KeyLevels } from '../components/KeyLevels';
import { GexByStrike } from '../components/charts/GexByStrike';
import { GammaProfile } from '../components/GammaProfile';
import { ErrorState } from '../components/ErrorState';
import { PageHeader } from '../components/ui/PageHeader';
import { MetricStrip, type MetricStripItem } from '../components/ui/MetricCard';
import { SegmentedControl, Toolbar } from '../components/ui/Toolbar';
import { formatDistance, formatDistancePct, formatGex, formatStrike } from '../lib/format';

/**
 * Cycles the URL-state symbol with `[` / `]` (previous/next in `UNDERLYINGS` order, wrapping).
 * Scoped to the Dashboard route (T16's ask), not global, and deliberately ignores the event
 * while focus is in a form control or a modifier key is held, so it never hijacks typing in
 * the TopBar's snapshot/filter `<select>`s or a browser/OS shortcut.
 */
function useSymbolCycleShortcut(symbol: Underlying, setSymbol: (next: Underlying) => void) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== '[' && event.key !== ']') return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || target?.isContentEditable) return;

      const currentIndex = UNDERLYINGS.indexOf(symbol);
      const delta = event.key === ']' ? 1 : -1;
      const next = UNDERLYINGS[(currentIndex + delta + UNDERLYINGS.length) % UNDERLYINGS.length];
      setSymbol(next);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [symbol, setSymbol]);
}

/** T69: whether the viewport is at or below `.dashboard-charts`' own side-by-side breakpoint
 * (880px, `index.css`) -- used only to decide whether the chart toggle below takes effect. At
 * 880px and up this always reports `false` and both charts render together, exactly as before
 * this task. `window.matchMedia` is stubbed in the test environment (`src/test/setup.ts`) to
 * always report `matches: false`, so existing tests that render both charts unconditionally
 * (`App.test.tsx`) are unaffected by this hook's addition. */
function useNarrowDashboard(): boolean {
  const query = '(max-width: 879px)';
  const [narrow, setNarrow] = useState(() => (typeof window !== 'undefined' ? window.matchMedia(query).matches : false));
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mql = window.matchMedia(query);
    const onChange = () => setNarrow(mql.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);
  return narrow;
}

/** The nearer of the call/put wall to spot, for the `MetricStrip`'s "Nearest wall" tile — a
 * plain distance comparison over the same two already-computed levels `KeyLevels`' own
 * distance column reads, not a new GEX calculation. `null` when neither wall exists in the
 * current profile (e.g. `ZERO_DTE` after the close), matching every other null-tolerant
 * figure on this page. */
function nearestWallMetric(levels: KeyLevelsData, spot: number): MetricStripItem {
  const candidates: { label: string; value: number }[] = [];
  if (levels.call_wall != null) candidates.push({ label: 'Call wall', value: levels.call_wall });
  if (levels.put_wall != null) candidates.push({ label: 'Put wall', value: levels.put_wall });

  if (candidates.length === 0) {
    return { metricKey: 'nearest-wall', label: 'Nearest wall', value: '—', hint: 'No wall in the current profile' };
  }
  const nearest = candidates.reduce((a, b) => (Math.abs(a.value - spot) <= Math.abs(b.value - spot) ? a : b));
  return {
    metricKey: 'nearest-wall',
    label: 'Nearest wall',
    value: formatStrike(nearest.value),
    hint: `${nearest.label} · ${formatDistance(nearest.value, spot)} (${formatDistancePct(nearest.value, spot)})`,
  };
}

/** The Dashboard's current-state summary strip (T66; plan's "Page priorities" table names
 * GEX Explorer's spot/net-GEX/nearest-wall/flip-point strip as the clearest `MetricStrip` fit
 * of the five Analyze pages). Every figure is read straight from `primary.data`, already
 * fetched below — nothing here recomputes a GEX value, it only reuses the same formatters
 * `KeyLevels` itself calls.
 *
 * **T69:** dropped the fifth "As of" tile -- confirmed first, not assumed (the same discipline
 * T67 used before removing Report's page-body selects): `/dashboard` is not in `ContextBar`'s
 * `SCAN_FAMILY_PATHS`, so the shell's own context bar already renders the snapshot/freshness
 * badge above this page for this exact route, making the tile a redundant fifth card whose only
 * effect was pushing the strip into a third wrapped row on a phone (the user's own review
 * note). Nothing about the freshness *data* changed -- it just has one fewer duplicate home. */
function keyLevelMetrics(data: NonNullable<ReturnType<typeof useGexResult>['data']>): MetricStripItem[] {
  const { levels, spot } = data;
  return [
    { metricKey: 'spot', label: 'Spot', value: formatStrike(spot) },
    {
      metricKey: 'net-gex',
      label: 'Net GEX',
      value: formatGex(levels.net_gex),
      hint:
        levels.net_gex > 0
          ? 'Dealers net long gamma — dampening'
          : levels.net_gex < 0
            ? 'Dealers net short gamma — amplifying'
            : 'Net gamma flat',
    },
    nearestWallMetric(levels, spot),
    {
      metricKey: 'flip-point',
      label: 'Flip point',
      value: formatStrike(levels.flip_point),
      hint:
        levels.flip_point == null
          ? 'No sign change in the profile grid'
          : `${formatDistance(levels.flip_point, spot)} (${formatDistancePct(levels.flip_point, spot)})`,
    },
  ];
}

/**
 * Dashboard assembly (T16). Layout, per `plans/ui-ux-refresh/README.md`'s "Analyze" reading
 * order — page question, current-state strip, GEX-by-strike, gamma profile, then the
 * semantic key-level table last (T66 moved `KeyLevels` from the top of the page to the
 * bottom to match that order; nothing about its own content or values changed, only its
 * position): `PageHeader` names the page, `MetricStrip` gives the at-a-glance numbers,
 * `GexByStrike` and `GammaProfile` sit side by side on a laptop-width viewport and stack on a
 * phone (see the `.dashboard-charts` rule in `src/index.css` for the breakpoint), and
 * `KeyLevels` closes the page as the full accessible table underneath.
 *
 * Two independent data fetches, matching the contract each chart already exercised in its
 * own `/demo/*` page (T13/T14 built and tested against exactly this shape):
 *  - `primary` follows the TopBar's filter/snapshot selection verbatim — it feeds the
 *    metric strip, `KeyLevels` and `GexByStrike`, so switching to `ZERO_DTE` after the close
 *    is the one path that legitimately shows every wall as null, and this is where that has
 *    to render cleanly.
 *  - `allProfile`/`exZeroDteProfile` are pinned to `ALL` / `EX_ZERO_DTE` regardless of the
 *    TopBar filter, because GammaProfile's two series *are* "all expiries" vs "ex-0DTE" by
 *    definition (see GammaProfileDemo, T14) — a third, independently-selected filter on top
 *    of that pair wouldn't have anywhere to plug in. Both still honor a pinned `snapshot` id,
 *    so a deep link to a historical snapshot is consistent across every chart on the page.
 *    When the TopBar filter is already ALL (the default), this reuses `primary`'s cached
 *    response instead of firing a second request — same `queryKey`, per `api/queries.ts`.
 *
 * **T69 — density pass.** Three changes, no fetch/prop/value touched:
 *  - The visible "Tip: press [ / ] to switch symbol" paragraph is gone; the shortcut itself
 *    (`useSymbolCycleShortcut`, above) is untouched and still works, it is just no longer
 *    spelled out on the page -- it is discoverable via the Ctrl/Cmd+K command palette instead
 *    (`components/layout/CommandPalette.tsx`), same as every other keyboard path this app
 *    exposes without an on-page hint.
 *  - `keyLevelMetrics` dropped its fifth "As of" tile (see that function's own docstring for
 *    why that is safe: `ContextBar` already renders freshness on this exact route), and the
 *    remaining four-tile strip gets `dashboard-metric-strip`'s narrow-width 2-column grid
 *    (`index.css`) instead of wrapping into three rows on a phone.
 *  - Below `.dashboard-charts`' own 880px side-by-side breakpoint, a `SegmentedControl` lets
 *    the reader pick one chart at a time instead of stacking both full-height ones; at 880px
 *    and up both always render together, unchanged from before. The non-selected chart is not
 *    merely CSS-hidden -- it is not mounted at all below the breakpoint, so switching back to
 *    it always mounts a fresh ECharts instance sized to its current container rather than an
 *    already-mounted one stuck at a stale (zero, while hidden) width.
 */
export function Dashboard() {
  const { symbol, filter, snapshotId, setSymbol } = useDashboardParams();
  useSymbolCycleShortcut(symbol, setSymbol);
  const isNarrow = useNarrowDashboard();
  const [narrowView, setNarrowView] = useState<'strike' | 'profile'>('strike');

  const primary = useGexResult(symbol, filter, snapshotId);
  const allProfile = useGexResult(symbol, 'ALL', snapshotId);
  const exZeroDteProfile = useGexResult(symbol, 'EX_ZERO_DTE', snapshotId);

  const showStrikeChart = !isNarrow || narrowView === 'strike';
  const showProfileChart = !isNarrow || narrowView === 'profile';

  return (
    <div className="dashboard">
      <PageHeader
        title="GEX Explorer"
        description="Single-symbol GEX state: current levels, GEX by strike, and the gamma profile."
      />

      {primary.isLoading && <p aria-live="polite">Loading {symbol} GEX…</p>}
      {primary.isError && (
        <ErrorState
          message={`Failed to load ${symbol} GEX: ${primary.error instanceof Error ? primary.error.message : 'unknown error'}`}
        />
      )}

      {primary.data && (
        <>
          <MetricStrip
            label={`${symbol} at a glance`}
            metrics={keyLevelMetrics(primary.data)}
            className="dashboard-metric-strip"
          />

          {isNarrow && (
            <Toolbar>
              <SegmentedControl
                label="Chart"
                values={['strike', 'profile'] as const}
                active={narrowView}
                render={(value) => (value === 'strike' ? 'GEX by strike' : 'Gamma profile')}
                onChange={setNarrowView}
              />
            </Toolbar>
          )}

          <div className="dashboard-charts">
            {showStrikeChart && (
              <GexByStrike
                rows={primary.data.by_strike}
                spot={primary.data.spot}
                callWall={primary.data.levels.call_wall}
                putWall={primary.data.levels.put_wall}
                flipPoint={primary.data.levels.flip_point}
                underlying={primary.data.underlying}
              />
            )}

            {showProfileChart &&
              (allProfile.isLoading || exZeroDteProfile.isLoading ? (
                <p aria-live="polite">Loading gamma profile…</p>
              ) : allProfile.isError || exZeroDteProfile.isError ? (
                <ErrorState message="Failed to load gamma profile." />
              ) : allProfile.data && exZeroDteProfile.data ? (
                <GammaProfile
                  allProfile={allProfile.data.profile}
                  exZeroDteProfile={exZeroDteProfile.data.profile}
                  spot={allProfile.data.spot}
                  flipPoint={allProfile.data.levels.flip_point}
                />
              ) : null)}
          </div>

          <KeyLevels levels={primary.data.levels} snapshot={primary.data.snapshot} />
        </>
      )}

      {/* TODO(T15): <PriceChart underlying={symbol} levels={primary.data.levels} /> — needs
          GET /api/prices/{underlying}?days=30 (TASKS.md T15), which does not exist yet.
          Mount below the chart grid, full width, once that endpoint and component ship. */}
    </div>
  );
}
