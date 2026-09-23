/**
 * `/regime` -- the dealer-positioning regime board (T49; spec in
 * `plans/continuation/07-ui.md`'s `/regime` section, staleness rules from
 * `plans/continuation/03-regime-board.md`'s "The 0DTE share is not derivable from an EOD
 * snapshot" and "Verified facts (2026-09-09, from T47's first live 16:45 run)").
 *
 * A universe page, not a symbol page: the TopBar renders its scan toolbar rather than the
 * dashboard's symbol switcher, same "URL drives every view" contract every scan-family page
 * follows. Unlike `/scan` and `/rotation`, this page's own toolbar reuses the *dashboard's*
 * expiry-filter validator (`useDashboardParams`'s `filter`/`setFilter`) rather than minting a
 * new one in `useScanParams` -- 07-ui.md's instruction, since `ALL`/`ZERO_DTE`/`EX_ZERO_DTE`
 * is already exactly the dashboard's own enum. The sort column/direction still come from
 * `useScanParams`, the one vocabulary every scan-family page shares.
 *
 * The `RegimeStrip` at the top is T54's component, wired in here once that task landed. It
 * owns its own query and its own loading/error/`n/a` states, so this page renders it
 * unconditionally rather than gating it -- and never a placeholder row of dashes (07-ui.md).
 *
 * T66: `PageHeader` names the page and what it answers; the filter button row (this file's
 * own hand-rolled `FilterToolbar`) now renders through `ui/Toolbar`'s
 * `Toolbar`/`SegmentedControl` -- identical DOM/classes/`aria-pressed` behavior, same URL
 * state. The board is wrapped in `DataTableFrame`. No row here opens a detail panel, so
 * `DetailDrawer` does not apply to this page.
 */
import { useCallback } from 'react';
import { useRegime } from '../api/queries';
import { useDashboardParams, useScanParams } from '../state/urlState';
import { EXPIRY_FILTER_LABELS, type ExpiryFilter } from '../api/types';
import { RegimeStrip } from '../components/regime/RegimeStrip';
import { RegimeTable } from '../components/regime/RegimeTable';
import { toRegimeRows, REGIME_DEFAULT_SORT } from '../components/regime/regimeRows';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { PageHeader } from '../../../components/ui/PageHeader';
import { SegmentedControl, Toolbar } from '../../../components/ui/Toolbar';
import { DataTableFrame } from '../../../components/ui/DataTableFrame';

/** The board's own toolbar only offers the three filters `07-ui.md` names -- the dashboard's
 * full `EXPIRY_FILTERS` enum also has `THIS_WEEK`/`MONTHLY_ONLY`, which this board doesn't
 * expose (a hand-edited or old URL carrying one of those still round-trips through
 * `useDashboardParams`'s own validator; it just has no active button here). */
const REGIME_FILTERS: readonly ExpiryFilter[] = ['ALL', 'ZERO_DTE', 'EX_ZERO_DTE'];

export function Regime() {
  const { filter, setFilter } = useDashboardParams();
  const { sort, dir, setSort } = useScanParams();
  const effectiveSort = sort ?? REGIME_DEFAULT_SORT;

  const onSort = useCallback(
    (key: string) => {
      // Same column flips direction; a new column starts at desc -- Scan.tsx's policy, copied
      // verbatim here as every scan-family page does.
      if (key === effectiveSort) setSort(key, dir === 'desc' ? 'asc' : 'desc');
      else setSort(key, 'desc');
    },
    [effectiveSort, dir, setSort],
  );

  const regime = useRegime(filter);

  return (
    <div className="regime-page">
      <PageHeader title="Regime" description="Dealer-positioning regime for each symbol, right now." />

      {/* T54's cross-asset strip, wired in by the supervisor once that task landed. It owns
          its own query and its own empty/error states, so it is rendered unconditionally. */}
      <RegimeStrip />

      <Toolbar>
        <SegmentedControl
          label="Filter"
          values={REGIME_FILTERS}
          active={filter}
          render={(value) => EXPIRY_FILTER_LABELS[value]}
          onChange={setFilter}
        />
      </Toolbar>

      {regime.isError ? (
        <ErrorState message="Could not load the regime board." />
      ) : regime.isPending ? (
        <LoadingState message="Scoring dealer positioning across the universe…" />
      ) : !regime.data || regime.data.rows.length === 0 ? (
        <EmptyState heading="No symbols scored">
          No option chains are captured for the regime board yet.
        </EmptyState>
      ) : (
        <DataTableFrame
          title="Dealer positioning regime"
          readingCue="Sorted by group by default: continuation, then mixed, then fade, then noise-dominated, then stale."
        >
          <RegimeTable
            rows={toRegimeRows(regime.data.rows)}
            filterSearch={`filter=${filter}`}
            sort={effectiveSort}
            dir={dir}
            onSort={onSort}
            pageSize={20}
          />
        </DataTableFrame>
      )}
    </div>
  );
}
