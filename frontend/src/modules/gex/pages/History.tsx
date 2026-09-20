import { useLevelsHistory } from '../api/queries';
import { useDashboardParams } from '../state/urlState';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { PageHeader } from '../../../components/ui/PageHeader';
import { Surface } from '../../../components/ui/Surface';
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { LevelHistory } from '../components/charts/LevelHistory';
import { LevelHistoryTable } from '../components/LevelHistoryTable';

/**
 * `/history`. T15's chart and table, finally: how the flip point, call wall and put wall have
 * moved against spot for the selected symbol. The page had carried a "Table/chart mounts here
 * (T15)" placeholder since T12's scaffold; the user pointed at it on 2026-09-11.
 *
 * Chart above, table below, both fed by the same `useLevelsHistory` rows — the table is not a
 * duplicate but the chart's required accessible equivalent (ECharts paints to `<canvas>`, which
 * a screen reader cannot read) and the exact-value view a line chart only approximates.
 *
 * T67: gained the shared `PageHeader`/`LoadingState`/`ErrorState`/`EmptyState` treatment for
 * consistency with the rest of the app.
 *
 * **Gap found while doing this task, worth flagging explicitly rather than silently
 * papering over:** the brief for this task assumed History already had a documented
 * no-data -> Capture-now action mirroring Report's T37 contract ("preserve exactly"). It
 * does not, and never has -- `git log` on this file shows only its original T12 scaffold and
 * T55's unrelated shared-kit pass; `01-ux-baseline.md`'s own baseline audit for this route
 * already recorded "no empty-state distinction beyond a row-count sentence." The backend
 * endpoint this page reads (`GET /api/gex/gex/{underlying}/levels/history`, `backend/app/modules/gex/api/
 * gex.py`) is a plain `gex_levels` JOIN `snapshots` query that returns `200 []` when nothing
 * is stored yet -- it never 404s the way `/api/gex/report/{underlying}` does, so there is no
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
          <>
            <Surface as="section" aria-label={`${symbol} level history chart`}>
              <LevelHistory rows={data} symbol={symbol} />
            </Surface>
            <DataTableFrame
              title="Captures"
              readingCue={`Every stored capture for ${symbol} (${filter}), newest first. A dash means the filter admitted no contracts at that capture, not a level of zero.`}
              sourceTiming={`${data.length} capture${data.length === 1 ? '' : 's'} stored.`}
            >
              <LevelHistoryTable rows={data} />
            </DataTableFrame>
          </>
        ) : (
          <EmptyState heading="No level history yet">
            No level-history rows are stored yet for {symbol} ({filter}). They accumulate as
            captures run — the chart and table appear here once the first one is stored.
          </EmptyState>
        )
      )}
    </div>
  );
}
