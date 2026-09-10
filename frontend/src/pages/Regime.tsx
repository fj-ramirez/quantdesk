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
 * The `RegimeStrip` slot belongs to T54, which does not exist yet: this page renders nothing
 * there -- never a placeholder row of dashes (07-ui.md) -- and leaves a comment marking where
 * that task adds its component.
 */
import { useCallback } from 'react';
import { useRegime } from '../api/queries';
import { useDashboardParams, useScanParams } from '../state/urlState';
import { EXPIRY_FILTER_LABELS, type ExpiryFilter } from '../api/types';
import { RegimeTable } from '../components/regime/RegimeTable';
import { toRegimeRows, REGIME_DEFAULT_SORT } from '../components/regime/regimeRows';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';

/** The board's own toolbar only offers the three filters `07-ui.md` names -- the dashboard's
 * full `EXPIRY_FILTERS` enum also has `THIS_WEEK`/`MONTHLY_ONLY`, which this board doesn't
 * expose (a hand-edited or old URL carrying one of those still round-trips through
 * `useDashboardParams`'s own validator; it just has no active button here). */
const REGIME_FILTERS: readonly ExpiryFilter[] = ['ALL', 'ZERO_DTE', 'EX_ZERO_DTE'];

function FilterToolbar({ active, onChange }: { active: ExpiryFilter; onChange: (next: ExpiryFilter) => void }) {
  return (
    <div className="scan-toolbar__group" role="group" aria-label="Filter">
      <span className="scan-toolbar__label">Filter</span>
      {REGIME_FILTERS.map((value) => (
        <button
          key={value}
          type="button"
          className={
            value === active ? 'scan-toolbar__btn scan-toolbar__btn--active' : 'scan-toolbar__btn'
          }
          aria-pressed={value === active}
          onClick={() => onChange(value)}
        >
          {EXPIRY_FILTER_LABELS[value]}
        </button>
      ))}
    </div>
  );
}

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
      {/* RegimeStrip slot (T54) -- intentionally rendered empty until that endpoint exists.
          Do not add a placeholder row of dashes here; see this file's own docstring. */}

      <div className="scan-toolbar">
        <FilterToolbar active={filter} onChange={setFilter} />
      </div>

      {regime.isError ? (
        <ErrorState message="Could not load the regime board." />
      ) : regime.isPending ? (
        <p className="scan-page__loading">Scoring dealer positioning across the universe…</p>
      ) : !regime.data || regime.data.rows.length === 0 ? (
        <EmptyState heading="No symbols scored">
          No option chains are captured for the regime board yet.
        </EmptyState>
      ) : (
        <RegimeTable
          rows={toRegimeRows(regime.data.rows)}
          filterSearch={`filter=${filter}`}
          sort={effectiveSort}
          dir={dir}
          onSort={onSort}
        />
      )}
    </div>
  );
}
