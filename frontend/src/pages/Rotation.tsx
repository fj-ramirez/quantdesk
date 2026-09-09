/**
 * `/rotation` -- sector/industry/asset rotation against a benchmark (T51; spec in
 * `plans/continuation/07-ui.md`'s `/rotation` section, formula and honesty rules from
 * `plans/continuation/04-sector-rotation.md`'s "Design decisions"; plan 04's own T51 task
 * paragraph is superseded by 07-ui.md per that file's own note).
 *
 * A universe page, not a symbol page (07-ui.md's "Information architecture"): the TopBar
 * renders its scan toolbar here, and `group`/`benchmark`/`weeks` -- already provided,
 * validated, by `useScanParams` -- fully drive the view, same "URL drives every view"
 * contract every scan-family page follows. The rank table's sort reuses that same hook's
 * generic `sort`/`dir` (applied without writing to the URL until a header is actually
 * clicked -- `Scan.tsx`'s header-click policy, copied verbatim here as the same "same column
 * flips, new column resets to desc" rule).
 *
 * The info `<details>` beside the chart title carries plan 04's approximation formula *and*
 * the API's own `note` field verbatim -- never a paraphrase of it, per 07-ui.md's
 * non-negotiables ("do not paraphrase it into a claim of parity").
 */
import { useCallback } from 'react';
import { useRotation } from '../api/queries';
import {
  useScanParams,
  SCAN_BENCHMARKS,
  SCAN_GROUPS,
  SCAN_WEEKS_VALUES,
  type ScanBenchmark,
  type ScanGroup,
} from '../state/urlState';
import { RrgChart } from '../components/rotation/RrgChart';
import { RankTable } from '../components/rotation/RankTable';
import { RANK_DEFAULT_SORT } from '../components/rotation/rankRows';
import { Breadth } from '../components/rotation/Breadth';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import type { RotationResponse } from '../api/types';

const GROUP_LABEL: Record<ScanGroup, string> = {
  sectors: 'Sectors',
  industries: 'Industries',
  assets: 'Assets',
};

function ChoiceRow<T extends string>({
  label,
  values,
  active,
  onChange,
  optionLabel,
}: {
  label: string;
  values: readonly T[];
  active: T;
  onChange: (next: T) => void;
  optionLabel?: (value: T) => string;
}) {
  return (
    <div className="scan-toolbar__group" role="group" aria-label={label}>
      <span className="scan-toolbar__label">{label}</span>
      {values.map((value) => (
        <button
          key={value}
          type="button"
          className={
            value === active ? 'scan-toolbar__btn scan-toolbar__btn--active' : 'scan-toolbar__btn'
          }
          aria-pressed={value === active}
          onClick={() => onChange(value)}
        >
          {optionLabel ? optionLabel(value) : value}
        </button>
      ))}
    </div>
  );
}

export function Rotation() {
  const { group, benchmark, weeks, sort, dir, setGroup, setBenchmark, setWeeks, setSort } = useScanParams();
  const effectiveSort = sort ?? RANK_DEFAULT_SORT;

  const onSort = useCallback(
    (key: string) => {
      // Same column flips direction; a new column starts at desc -- Scan.tsx's policy.
      if (key === effectiveSort) setSort(key, dir === 'desc' ? 'asc' : 'desc');
      else setSort(key, 'desc');
    },
    [effectiveSort, dir, setSort],
  );

  const rotation = useRotation(group, benchmark, weeks);

  return (
    <div className="rotation-page">
      <div className="scan-toolbar">
        <ChoiceRow<ScanGroup>
          label="Group"
          values={SCAN_GROUPS}
          active={group}
          onChange={setGroup}
          optionLabel={(v) => GROUP_LABEL[v]}
        />
        <ChoiceRow<ScanBenchmark> label="Benchmark" values={SCAN_BENCHMARKS} active={benchmark} onChange={setBenchmark} />
        <div className="scan-toolbar__group" role="group" aria-label="Weeks">
          <span className="scan-toolbar__label">Weeks</span>
          {SCAN_WEEKS_VALUES.map((value) => (
            <button
              key={value}
              type="button"
              className={
                value === weeks ? 'scan-toolbar__btn scan-toolbar__btn--active' : 'scan-toolbar__btn'
              }
              aria-pressed={value === weeks}
              onClick={() => setWeeks(value)}
            >
              {value}
            </button>
          ))}
        </div>
      </div>

      {rotation.isError ? (
        <ErrorState message="Could not load sector rotation." />
      ) : rotation.isPending ? (
        <p className="scan-page__loading">Computing relative rotation…</p>
      ) : !rotation.data || rotation.data.symbols.length === 0 ? (
        <EmptyState heading="No symbols to show">
          No bars are stored for this group's symbols yet.
        </EmptyState>
      ) : (
        <RotationView data={rotation.data} sort={effectiveSort} dir={dir} onSort={onSort} />
      )}
    </div>
  );
}

function RotationView({
  data,
  sort,
  dir,
  onSort,
}: {
  data: RotationResponse;
  sort: string;
  dir: 'asc' | 'desc';
  onSort: (key: string) => void;
}) {
  return (
    <div className="rotation-layout">
      <section className="rotation-layout__chart" aria-labelledby="rotation-chart-title">
        <div className="rotation-chart-head">
          <h2 id="rotation-chart-title" className="scan-layout__side-title">
            Relative rotation
          </h2>
          <details className="rotation-info">
            <summary>About this chart</summary>
            <p>
              <code>rs = 100 · P / B</code>; <code>rs_ratio_approx = 100 + z(rs, w)</code>;{' '}
              <code>rs_momentum_approx = 100 + z(rs_ratio_t − rs_ratio_t-1, w)</code>, with a
              rolling window <code>w = {data.w}</code> weeks.
            </p>
            <p>{data.note}</p>
          </details>
        </div>
        <RrgChart symbols={data.symbols} />
      </section>
      <aside className="rotation-layout__side" aria-label="Rank table and breadth">
        <RankTable symbols={data.symbols} sort={sort} dir={dir} onSort={onSort} />
        <Breadth breadth={data.breadth} />
      </aside>
    </div>
  );
}
