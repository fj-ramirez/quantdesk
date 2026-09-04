/**
 * Standalone demo route for the T13 `GexByStrike` chart — deliberately NOT mounted into
 * `src/pages/Dashboard.tsx` (T16 owns dashboard assembly, and other agents are editing
 * sibling components against the same Dashboard file right now). Reuses the same URL-state
 * + query hooks Dashboard uses, so this exercises the exact data path the real dashboard
 * will use once T16 wires it in — nothing here is mock-only scaffolding.
 */
import { UNDERLYINGS, type Underlying } from '../../api/types';
import { useGexResult } from '../../api/queries';
import { useDashboardParams } from '../../state/urlState';
import { GexByStrike } from '../../components/charts/GexByStrike';

export function GexByStrikeDemo() {
  const { symbol, filter, snapshotId, setSymbol } = useDashboardParams();
  const { data, isLoading, isError, error } = useGexResult(symbol, filter, snapshotId);

  return (
    <div>
      <h2>GexByStrike demo (T13)</h2>
      <div role="group" aria-label="Symbol">
        {UNDERLYINGS.map((sym: Underlying) => (
          <button key={sym} type="button" aria-pressed={sym === symbol} disabled={sym === symbol} onClick={() => setSymbol(sym)}>
            {sym}
          </button>
        ))}
      </div>

      {isLoading && <p>Loading {symbol} GEX…</p>}
      {isError && (
        <p role="alert">Failed to load {symbol} GEX: {error instanceof Error ? error.message : 'unknown error'}</p>
      )}
      {data && (
        <GexByStrike
          rows={data.by_strike}
          spot={data.levels.spot}
          callWall={data.levels.call_wall}
          putWall={data.levels.put_wall}
          flipPoint={data.levels.flip_point}
          underlying={data.underlying}
        />
      )}
    </div>
  );
}
