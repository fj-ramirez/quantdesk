import { useEffect } from 'react';
import { UNDERLYINGS, type Underlying } from '../api/types';
import { useGexResult } from '../api/queries';
import { useDashboardParams } from '../state/urlState';
import { KeyLevels } from '../components/KeyLevels';
import { GexByStrike } from '../components/charts/GexByStrike';
import { GammaProfile } from '../components/GammaProfile';
import { ErrorState } from '../components/ErrorState';

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

/**
 * Dashboard assembly (T16). Layout: KeyLevels full-width on top, GexByStrike and
 * GammaProfile side by side on a laptop-width viewport, stacked on a phone (see the
 * `.dashboard-charts` rule in `src/index.css` for the breakpoint).
 *
 * Two independent data fetches, matching the contract each chart already exercised in its
 * own `/demo/*` page (T13/T14 built and tested against exactly this shape):
 *  - `primary` follows the TopBar's filter/snapshot selection verbatim — it feeds KeyLevels
 *    and GexByStrike, so switching to `ZERO_DTE` after the close is the one path that
 *    legitimately shows every wall as null, and this is where that has to render cleanly.
 *  - `allProfile`/`exZeroDteProfile` are pinned to `ALL` / `EX_ZERO_DTE` regardless of the
 *    TopBar filter, because GammaProfile's two series *are* "all expiries" vs "ex-0DTE" by
 *    definition (see GammaProfileDemo, T14) — a third, independently-selected filter on top
 *    of that pair wouldn't have anywhere to plug in. Both still honor a pinned `snapshot` id,
 *    so a deep link to a historical snapshot is consistent across every chart on the page.
 *    When the TopBar filter is already ALL (the default), this reuses `primary`'s cached
 *    response instead of firing a second request — same `queryKey`, per `api/queries.ts`.
 */
export function Dashboard() {
  const { symbol, filter, snapshotId, setSymbol } = useDashboardParams();
  useSymbolCycleShortcut(symbol, setSymbol);

  const primary = useGexResult(symbol, filter, snapshotId);
  const allProfile = useGexResult(symbol, 'ALL', snapshotId);
  const exZeroDteProfile = useGexResult(symbol, 'EX_ZERO_DTE', snapshotId);

  return (
    <div className="dashboard">
      <p className="dashboard-hint" aria-hidden="true">
        Tip: press <kbd>[</kbd> / <kbd>]</kbd> to switch symbol.
      </p>

      {primary.isLoading && <p aria-live="polite">Loading {symbol} GEX…</p>}
      {primary.isError && (
        <ErrorState
          message={`Failed to load ${symbol} GEX: ${primary.error instanceof Error ? primary.error.message : 'unknown error'}`}
        />
      )}

      {primary.data && (
        <>
          <KeyLevels levels={primary.data.levels} snapshot={primary.data.snapshot} />

          <div className="dashboard-charts">
            <GexByStrike
              rows={primary.data.by_strike}
              spot={primary.data.spot}
              callWall={primary.data.levels.call_wall}
              putWall={primary.data.levels.put_wall}
              flipPoint={primary.data.levels.flip_point}
              underlying={primary.data.underlying}
            />

            {allProfile.isLoading || exZeroDteProfile.isLoading ? (
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
            ) : null}
          </div>
        </>
      )}

      {/* TODO(T15): <PriceChart underlying={symbol} levels={primary.data.levels} /> — needs
          GET /api/prices/{underlying}?days=30 (TASKS.md T15), which does not exist yet.
          Mount below the chart grid, full width, once that endpoint and component ship. */}
    </div>
  );
}
