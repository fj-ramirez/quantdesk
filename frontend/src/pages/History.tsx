import { useLevelsHistory } from '../api/queries';
import { useDashboardParams } from '../state/urlState';

/**
 * `/history` shell. The table + line chart of flip/call wall/put wall vs. close over time
 * is T15's deliverable — this page only proves the data layer (`useLevelsHistory`) is
 * wired to the same URL state as the Dashboard, so a link to `/history?symbol=QQQ&...`
 * lands on QQQ's history, not a reset to SPX.
 */
export function History() {
  const { symbol, filter } = useDashboardParams();
  const { data, isLoading, isError } = useLevelsHistory(symbol, filter);

  if (isLoading) return <p>Loading {symbol} level history…</p>;
  if (isError) return <p role="alert">Failed to load {symbol} level history.</p>;

  return (
    <div>
      {/* TODO(T15): table + line chart of flip/call wall/put wall vs. close, from `data` */}
      <p>{data?.length ?? 0} level-history rows loaded for {symbol} ({filter}). Table/chart mounts here (T15).</p>
    </div>
  );
}
