import { useLevelsHistory } from '../api/queries';
import { useDashboardParams } from '../state/urlState';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { PageHeader } from '../components/ui/PageHeader';
import { Surface } from '../components/ui/Surface';

/**
 * `/history` shell. The table + line chart of flip/call wall/put wall vs. close over time
 * is T15's deliverable — this page only proves the data layer (`useLevelsHistory`) is
 * wired to the same URL state as the Dashboard, so a link to `/history?symbol=QQQ&...`
 * lands on QQQ's history, not a reset to SPX.
 *
 * T67: gained the shared `PageHeader`/`LoadingState`/`ErrorState`/`EmptyState` treatment for
 * consistency with the rest of the app -- no table/chart is built here (still T15's job).
 *
 * **Gap found while doing this task, worth flagging explicitly rather than silently
 * papering over:** the brief for this task assumed History already had a documented
 * no-data -> Capture-now action mirroring Report's T37 contract ("preserve exactly"). It
 * does not, and never has -- `git log` on this file shows only its original T12 scaffold and
 * T55's unrelated shared-kit pass; `01-ux-baseline.md`'s own baseline audit for this route
 * already recorded "no empty-state distinction beyond a row-count sentence." The backend
 * endpoint this page reads (`GET /api/gex/{underlying}/levels/history`, `backend/app/api/
 * gex.py`) is a plain `gex_levels` JOIN `snapshots` query that returns `200 []` when nothing
 * is stored yet -- it never 404s the way `/api/report/{underlying}` does, so there is no
 * "no snapshot captured yet" signal to key a capture affordance off of the way `Report.tsx`'s
 * `NoDataYet` does, and zero rows here does not distinguish "this symbol has never been
 * captured" from "a snapshot exists but this filter/date range has no history rows for it."
 * Building that distinction correctly would mean querying `useSnapshots` as well and is
 * exactly the kind of new feature this task's brief says not to build (T15's job). So this
 * page's empty state below is honest about there being nothing to show, but deliberately
 * does not offer a Capture-now button.
 */
export function History() {
  const { symbol, filter } = useDashboardParams();
  const { data, isLoading, isError } = useLevelsHistory(symbol, filter);

  return (
    <div className="history-page">
      <PageHeader
        title="History"
        description="How the flip point, call wall and put wall have moved over time for the selected symbol."
      />

      {isLoading && <LoadingState message={`Loading ${symbol} level history…`} />}
      {isError && <ErrorState message={`Failed to load ${symbol} level history.`} />}
      {!isLoading && !isError && data && (
        data.length > 0 ? (
          <Surface as="section" aria-label="Level history">
            {/* TODO(T15): table + line chart of flip/call wall/put wall vs. close, from `data` */}
            <p>
              {data.length} level-history rows loaded for {symbol} ({filter}). Table/chart mounts
              here (T15).
            </p>
          </Surface>
        ) : (
          <EmptyState heading="No level history yet">
            No level-history rows are stored yet for {symbol} ({filter}). The table and chart
            (T15) will appear here once rows exist.
          </EmptyState>
        )
      )}
    </div>
  );
}
