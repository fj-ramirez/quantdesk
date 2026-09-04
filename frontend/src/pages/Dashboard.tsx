import { useGexResult } from '../api/queries';
import { useDashboardParams } from '../state/urlState';

/**
 * Dashboard shell. This intentionally renders no charts — KeyLevels (T14), GexByStrike
 * (T13), GammaProfile (T14) and PriceChart (T15) are other agents' deliverables. Each
 * mount point below is commented with the props it should receive, all already available
 * from `useDashboardParams` / `useGexResult` so a chart component only needs to consume
 * them, not re-derive symbol/filter/snapshot itself.
 *
 * `data` is fetched here (not just left for each chart to fetch separately) to prove the
 * URL state -> query hook -> GexResult path works end to end against the MSW mocks; T16
 * assembles the real layout and will likely lift this fetch up further or pass `data`
 * down instead of leaving each chart to call `useGexResult` independently.
 */
export function Dashboard() {
  const { symbol, filter, snapshotId } = useDashboardParams();
  const { data, isLoading, isError, error } = useGexResult(symbol, filter, snapshotId);

  if (isLoading) return <p>Loading {symbol} GEX…</p>;
  if (isError) return <p role="alert">Failed to load {symbol} GEX: {error instanceof Error ? error.message : 'unknown error'}</p>;
  if (!data) return null;

  return (
    <div>
      {/* TODO(T14): <KeyLevels levels={data.levels} snapshot={data.snapshot} /> */}
      <section aria-label="Key levels placeholder">
        <p>
          {data.underlying} spot {data.levels.spot} &middot; net GEX {data.levels.net_gex.toExponential(2)} &middot;{' '}
          flip {data.levels.flip_point ?? 'n/a'}
        </p>
      </section>

      <div>
        {/* TODO(T13): <GexByStrike rows={data.by_strike} spot={data.levels.spot}
             callWall={data.levels.call_wall} putWall={data.levels.put_wall}
             flipPoint={data.levels.flip_point} /> */}
        <section aria-label="GEX by strike placeholder">GexByStrike mounts here (T13).</section>

        {/* TODO(T14): <GammaProfile allProfile={data.profile} exZeroDteProfile={...}
             spot={data.levels.spot} flipPoint={data.levels.flip_point} /> */}
        <section aria-label="Gamma profile placeholder">GammaProfile mounts here (T14).</section>
      </div>

      {/* TODO(T15): <PriceChart underlying={symbol} levels={data.levels} /> */}
      <section aria-label="Price chart placeholder">PriceChart mounts here (T15).</section>
    </div>
  );
}
