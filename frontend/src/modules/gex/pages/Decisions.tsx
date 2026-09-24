/**
 * `/decisions` -- the decision engine (T60): every opportunity `app.scan.decisions` found
 * across the optioned universe, ranked, with entry / stop / target, thesis and invalidation
 * in a detail panel.
 *
 * A universe page like the rest of the scan family: the TopBar renders its scan toolbar,
 * the expiry filter reuses the dashboard's validator exactly as `/regime` does (the same
 * three persisted filters), the sort column/direction come from `useScanParams`, and the
 * score threshold is the URL's `min_score` (`useScanParams().minScore`) so a filtered view
 * is linkable. Row selection is local state, as on `/scan`.
 *
 * With no sort column active the table shows the API's own ranking -- `active` before
 * `watch` before `rejected`, then score descending -- so "unsorted" is the meaningful order,
 * not an accident of insertion.
 *
 * The backend's `generated_from` disclaimer renders verbatim as `DataTableFrame`'s source-
 * timing line, next to the table it describes. These are suggestions computed from the
 * latest captured chain and daily bars; nothing here is routed.
 *
 * T64: chosen as this task's one representative page -- it now exercises `PageHeader`,
 * `Toolbar`/`SegmentedControl` (replacing the local `ButtonGroup` this file used to define),
 * `DataTableFrame` (wrapping `OpportunityTable`'s `ScanTable`, unchanged inside), `MetricStrip`
 * (a current-state summary computed from data already fetched here, no new query), and
 * `DetailDrawer` (replacing `OpportunityDetail`'s old unmanaged-focus panel). All fetch/query
 * logic, ranking, and URL parameters (`filter`, `min_score`, `sort`, `dir`) are unchanged.
 *
 * T65: `plans/ui-ux-refresh/README.md`'s target reading order for Opportunities is "Status
 * summary -> filter/threshold -> ranked table -> selected detail -> no-trade reasons and
 * track record." T64 had `MetricStrip` (the status summary) rendered *after* the filter/
 * min-grade `Toolbar` -- this task reorders those two so the summary reads first, matching the
 * plan; nothing about either one's content, visibility condition, or computed values changed,
 * only their position in the JSX (the `MetricStrip` render is now gated on `decisions.data`
 * directly rather than nested inside the big loading/error/data conditional, which is what
 * moving it earlier requires, but it is hidden in exactly the same cases as before: no data
 * yet). Ranked table -> selected detail -> no-trade reasons -> track record was already in this
 * order (main column: table then drawer; aside: no-trade list; then track record below both) --
 * verified, not changed.
 *
 * T122: the three blocks under the toolbar -- ranked table, no-trade list, track record --
 * were stacked, and the track record's ledger alone ran to a screen and a half. They are now
 * in-section tabs (`?tab=`, URL state, so "the no-trade list" is linkable), in the same
 * reading order; the ranked table pages at fifteen rows, and the selected opportunity opens
 * in the (now overlay) `DetailDrawer`. The summary strip and toolbar stay above the tabs
 * because they govern all three.
 */
import { useCallback, useMemo, useState } from 'react';
import { useDecisions } from '../api/queries';
import { EXPIRY_FILTER_LABELS, type ExpiryFilter } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { NoTradeList } from '../components/decisions/NoTradeList';
import { OpportunityDetail } from '../components/decisions/OpportunityDetail';
import { OpportunityTable } from '../components/decisions/OpportunityTable';
import { TrackRecord } from '../components/decisions/TrackRecord';
import { setupLabel, toOpportunityRows } from '../components/decisions/opportunityRows';
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { DetailDrawer } from '../../../components/ui/DetailDrawer';
import { MetricStrip } from '../../../components/ui/MetricCard';
import { PageHeader } from '../../../components/ui/PageHeader';
import { DEFAULT_PAGE_SIZE } from '../../../components/ui/usePagination';
import { Tabs } from '../../../components/ui/Tabs';
import { useTabParam } from '../../../components/ui/useTabParam';
import { SegmentedControl, Toolbar } from '../../../components/ui/Toolbar';
import { SCAN_MIN_SCORE_VALUES, useDashboardParams, useScanParams } from '../state/urlState';

const DECISION_FILTERS: readonly ExpiryFilter[] = ['ALL', 'ZERO_DTE', 'EX_ZERO_DTE'];

/** Labels for `SCAN_MIN_SCORE_VALUES`: the engine's grade boundaries, read as thresholds. */
const MIN_SCORE_LABELS: Record<number, string> = { 0: 'All', 45: 'C+', 60: 'B+', 75: 'A' };

const DECISION_TABS = ['ranked', 'notrade', 'record'] as const;
type DecisionTab = (typeof DECISION_TABS)[number];

export function Decisions() {
  const { filter, setFilter } = useDashboardParams();
  const { sort, dir, setSort, minScore, setMinScore } = useScanParams();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useTabParam<DecisionTab>('tab', DECISION_TABS);

  const onSort = useCallback(
    (key: string) => {
      // Same column flips direction; a new column starts at desc -- Scan.tsx's policy.
      if (key === sort) setSort(key, dir === 'desc' ? 'asc' : 'desc');
      else setSort(key, 'desc');
    },
    [sort, dir, setSort],
  );

  const decisions = useDecisions(filter, minScore);
  const rows = useMemo(() => toOpportunityRows(decisions.data?.ranked ?? []), [decisions.data]);
  const selected = rows.find((row) => row.id === selectedId) ?? null;
  const filterSearch = `filter=${filter}`;

  // Current-state summary for `MetricStrip` -- every figure is derived from `decisions.data`,
  // already fetched above; no new query. `rows` is in the API's own rank order (rank 1
  // first) regardless of the table's own sort column, so `rows[0]` is always "the top-ranked
  // opportunity", not whatever the table currently happens to be sorted by.
  const activeCount = rows.filter((r) => r.status === 'active').length;
  const noTradeCount = decisions.data
    ? decisions.data.symbols.filter((s) => s.opportunities.length === 0).length + decisions.data.no_chain.length
    : 0;
  const bestRow = rows[0] ?? null;
  const drawerTitle = selected ? `${selected.opportunity.underlying} · ${setupLabel(selected.opportunity.key)}` : '';

  return (
    <div className="decisions-page">
      <PageHeader
        title="Opportunities"
        description="Ranked trade-idea opportunities across the universe, with named no-trade reasons and the track record of past suggestions."
      />

      {decisions.data && (
        <MetricStrip
          label="Opportunities at a glance"
          metrics={[
            { metricKey: 'scored', label: 'Scored', value: String(rows.length), hint: 'ranked opportunities' },
            {
              metricKey: 'active',
              label: 'Active',
              value: String(activeCount),
              status: {
                tone: activeCount > 0 ? 'positive' : 'neutral',
                label: activeCount > 0 ? 'Entry reachable now' : 'None active',
              },
            },
            {
              metricKey: 'best',
              label: 'Best grade',
              value: bestRow?.grade ?? '—',
              hint: bestRow ? `${bestRow.symbol} · ${bestRow.score}/100` : 'No ranked opportunity',
            },
            { metricKey: 'notrade', label: 'No trade', value: String(noTradeCount), hint: 'symbols with a named reason' },
          ]}
        />
      )}

      <Toolbar>
        <SegmentedControl
          label="Filter"
          values={DECISION_FILTERS}
          active={filter}
          render={(value) => EXPIRY_FILTER_LABELS[value]}
          onChange={setFilter}
        />
        <SegmentedControl
          label="Min grade"
          values={SCAN_MIN_SCORE_VALUES}
          active={minScore}
          render={(value) => MIN_SCORE_LABELS[value] ?? String(value)}
          onChange={setMinScore}
        />
      </Toolbar>

      <Tabs
        label="Opportunities sections"
        active={tab}
        onChange={setTab}
        tabs={[
          { value: 'ranked', label: 'Ranked', count: decisions.data ? rows.length : null },
          { value: 'notrade', label: 'No trade', count: decisions.data ? noTradeCount : null },
          { value: 'record', label: 'Track record' },
        ]}
      >
        {tab === 'record' ? (
          // T61: what happened to earlier suggestions. Owns its own query and states, so it
          // only fetches once this tab is opened.
          <TrackRecord filterSearch={filterSearch} />
        ) : decisions.isError ? (
          <ErrorState message="Could not load the decision engine." />
        ) : decisions.isPending ? (
          <LoadingState message="Scoring opportunities across the universe…" />
        ) : !decisions.data ? null : tab === 'notrade' ? (
          noTradeCount === 0 ? (
            <EmptyState heading="Every symbol has a suggestion">
              No symbol was declined at this filter, and every member has a captured chain.
            </EmptyState>
          ) : (
            // Already a labelled `<section>` ("No trade"), so it needs no `<aside>` wrapper now
            // that it no longer sits in a side column.
            <NoTradeList symbols={decisions.data.symbols} noChain={decisions.data.no_chain} filterSearch={filterSearch} />
          )
        ) : rows.length === 0 ? (
          <EmptyState heading="No opportunities at this threshold">
            {decisions.data.generated_from}{' '}
            {decisions.data.symbols.length === 0
              ? 'No option chain has been captured yet, so there is nothing to score.'
              : 'Every symbol with a chain is either below the score threshold or has a named reason for no trade (see the No trade tab).'}
          </EmptyState>
        ) : (
          <DataTableFrame
            title="Ranked opportunities"
            readingCue="Active entries are reachable now; watch entries need the wall to come closer; rejected entries fall below the reward/risk floor. Select a row for the thesis and levels."
            sourceTiming={decisions.data.generated_from}
          >
            <OpportunityTable
              rows={rows}
              filterSearch={filterSearch}
              sort={sort}
              dir={dir}
              onSort={onSort}
              selectedId={selectedId}
              onSelect={setSelectedId}
              pageSize={DEFAULT_PAGE_SIZE}
            />
          </DataTableFrame>
        )}
      </Tabs>
      {/* Always mounted (DetailDrawer's caller contract), outside the tab panel so switching
          tabs never unmounts it mid-focus-return. */}
      <DetailDrawer open={selected != null} onClose={() => setSelectedId(null)} title={drawerTitle}>
        {selected && <OpportunityDetail opportunity={selected.opportunity} />}
      </DetailDrawer>
    </div>
  );
}
