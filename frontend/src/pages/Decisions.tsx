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
 * The backend's `generated_from` disclaimer renders verbatim under the toolbar. These are
 * suggestions computed from the latest captured chain and daily bars; nothing here is routed.
 */
import { useCallback, useMemo, useState } from 'react';
import { useDecisions } from '../api/queries';
import { EXPIRY_FILTER_LABELS, type ExpiryFilter } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { NoTradeList } from '../components/decisions/NoTradeList';
import { OpportunityDetail } from '../components/decisions/OpportunityDetail';
import { OpportunityTable } from '../components/decisions/OpportunityTable';
import { TrackRecord } from '../components/decisions/TrackRecord';
import { toOpportunityRows } from '../components/decisions/opportunityRows';
import { SCAN_MIN_SCORE_VALUES, useDashboardParams, useScanParams } from '../state/urlState';

const DECISION_FILTERS: readonly ExpiryFilter[] = ['ALL', 'ZERO_DTE', 'EX_ZERO_DTE'];

/** Labels for `SCAN_MIN_SCORE_VALUES`: the engine's grade boundaries, read as thresholds. */
const MIN_SCORE_LABELS: Record<number, string> = { 0: 'All', 45: 'C+', 60: 'B+', 75: 'A' };

function ButtonGroup<T extends string | number>({
  label,
  values,
  active,
  render,
  onChange,
}: {
  label: string;
  values: readonly T[];
  active: T;
  render: (value: T) => string;
  onChange: (value: T) => void;
}) {
  return (
    <div className="scan-toolbar__group" role="group" aria-label={label}>
      <span className="scan-toolbar__label">{label}</span>
      {values.map((value) => (
        <button
          key={String(value)}
          type="button"
          className={value === active ? 'scan-toolbar__btn scan-toolbar__btn--active' : 'scan-toolbar__btn'}
          aria-pressed={value === active}
          onClick={() => onChange(value)}
        >
          {render(value)}
        </button>
      ))}
    </div>
  );
}

export function Decisions() {
  const { filter, setFilter } = useDashboardParams();
  const { sort, dir, setSort, minScore, setMinScore } = useScanParams();
  const [selectedId, setSelectedId] = useState<string | null>(null);

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

  return (
    <div className="decisions-page">
      <div className="scan-toolbar">
        <ButtonGroup
          label="Filter"
          values={DECISION_FILTERS}
          active={filter}
          render={(value) => EXPIRY_FILTER_LABELS[value]}
          onChange={setFilter}
        />
        <ButtonGroup
          label="Min grade"
          values={SCAN_MIN_SCORE_VALUES}
          active={minScore}
          render={(value) => MIN_SCORE_LABELS[value] ?? String(value)}
          onChange={setMinScore}
        />
      </div>

      {decisions.isError ? (
        <ErrorState message="Could not load the decision engine." />
      ) : decisions.isPending ? (
        <p className="scan-page__loading">Scoring opportunities across the universe…</p>
      ) : !decisions.data ? null : (
        <>
          <p className="scan-legend">{decisions.data.generated_from}</p>
          <div className="scan-layout">
            <div className="scan-layout__main">
              {rows.length === 0 ? (
                <EmptyState heading="No opportunities at this threshold">
                  {decisions.data.symbols.length === 0
                    ? 'No option chain has been captured yet, so there is nothing to score.'
                    : 'Every symbol with a chain is either below the score threshold or has a named reason for no trade (listed on the right).'}
                </EmptyState>
              ) : (
                <OpportunityTable
                  rows={rows}
                  filterSearch={filterSearch}
                  sort={sort}
                  dir={dir}
                  onSort={onSort}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                />
              )}
              {selected && (
                <OpportunityDetail opportunity={selected.opportunity} onClose={() => setSelectedId(null)} />
              )}
            </div>
            <aside className="scan-layout__side">
              <NoTradeList
                symbols={decisions.data.symbols}
                noChain={decisions.data.no_chain}
                filterSearch={filterSearch}
              />
            </aside>
          </div>
          {/* T61: what happened to earlier suggestions. Owns its own query and states. */}
          <TrackRecord filterSearch={filterSearch} />
        </>
      )}
    </div>
  );
}
