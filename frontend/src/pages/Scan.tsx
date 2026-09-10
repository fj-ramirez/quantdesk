/**
 * `/scan` -- the breakout ledger and the trend/chop scorer (T44, absorbing the old T46;
 * spec in `plans/continuation/07-ui.md`).
 *
 * A universe page, not a symbol page: the TopBar renders its scan toolbar rather than the
 * dashboard's symbol switcher (T55 made it route-aware), and every option here lives in the
 * URL via `useScanParams` so a view is linkable and survives a nav round trip.
 *
 * Sort defaults differ per view (`rate` desc for Breakouts, `composite` desc for Trend) and
 * are applied *without* writing to the URL, so a bare `/scan` stays a bare `/scan` while still
 * arriving sorted. `setSort` only writes once the reader actually clicks a header. The header
 * click policy -- same column flips direction, new column resets to desc -- lives here rather
 * than in `ScanTable`, per that component's docstring.
 *
 * The Trend view's query takes 3.5-5s against the live backend, because the API re-flattens 28
 * option chains per request to source `iv30` (recorded in T45's commit; a backend task owns
 * the fix). Nothing is worked around here, but the loading state is explicit and names the
 * reason, since five seconds of blank table reads as a broken page.
 */
import { useCallback, useState } from 'react';
import { useBreakouts, useTrend } from '../api/queries';
import { useScanParams, SCAN_K_VALUES, SCAN_LOOKBACK_VALUES, SCAN_N_VALUES } from '../state/urlState';
import { RegimeStrip } from '../components/regime/RegimeStrip';
import { BreakoutTable, BREAKOUT_DEFAULT_SORT } from '../components/scan/BreakoutTable';
import { BreakoutDetail } from '../components/scan/BreakoutDetail';
import { OpenBreakouts } from '../components/scan/OpenBreakouts';
import { TrendTable, TREND_DEFAULT_SORT } from '../components/scan/TrendTable';
import { TrendDetail } from '../components/scan/TrendDetail';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';

function ChoiceRow({
  label,
  values,
  active,
  onChange,
}: {
  label: string;
  values: readonly number[];
  active: number;
  onChange: (next: number) => void;
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
          {value}
        </button>
      ))}
    </div>
  );
}

export function Scan() {
  const params = useScanParams();
  const { view, n, k, lookback, sort, dir, setView, setN, setK, setLookback, setSort } = params;
  const [selected, setSelected] = useState<string | null>(null);

  const defaultSort = view === 'breakouts' ? BREAKOUT_DEFAULT_SORT : TREND_DEFAULT_SORT;
  const effectiveSort = sort ?? defaultSort;

  const onSort = useCallback(
    (key: string) => {
      // Same column flips direction; a new column starts at desc. See the module docstring.
      if (key === effectiveSort) setSort(key, dir === 'desc' ? 'asc' : 'desc');
      else setSort(key, 'desc');
    },
    [effectiveSort, dir, setSort],
  );

  const breakouts = useBreakouts({ n, k, lookback });
  const trend = useTrend();
  const active = view === 'breakouts' ? breakouts : trend;

  return (
    <div className="scan-page">
      {/* T54's cross-asset strip. 07-ui.md renders it at the top of both `/scan` and
          `/regime`: it answers "what kind of tape is this" for everything at once, which is
          the context the per-symbol rows below are read against. It owns its own query and
          its own loading/error/`n/a` states. */}
      <RegimeStrip />

      <div className="scan-toolbar">
        <div className="scan-toolbar__group" role="group" aria-label="View">
          {(['breakouts', 'trend'] as const).map((candidate) => (
            <button
              key={candidate}
              type="button"
              className={
                candidate === view
                  ? 'scan-toolbar__btn scan-toolbar__btn--active'
                  : 'scan-toolbar__btn'
              }
              aria-pressed={candidate === view}
              onClick={() => {
                setView(candidate);
                setSelected(null);
              }}
            >
              {candidate === 'breakouts' ? 'Breakouts' : 'Trend'}
            </button>
          ))}
        </div>

        {view === 'breakouts' && (
          <>
            <ChoiceRow label="N" values={SCAN_N_VALUES} active={n} onChange={setN} />
            <ChoiceRow label="k" values={SCAN_K_VALUES} active={k} onChange={setK} />
            <ChoiceRow
              label="Lookback"
              values={SCAN_LOOKBACK_VALUES}
              active={lookback}
              onChange={setLookback}
            />
          </>
        )}
      </div>

      {active.isError ? (
        <ErrorState
          message={
            view === 'breakouts'
              ? 'Could not load the breakout ledger.'
              : 'Could not load the trend scorer.'
          }
        />
      ) : active.isPending ? (
        <p className="scan-page__loading">
          {view === 'breakouts'
            ? 'Scanning the universe for range breaks…'
            : 'Scoring the universe… the trend scan reads every tracked option chain, so this takes a few seconds.'}
        </p>
      ) : view === 'breakouts' ? (
        <BreakoutsView
          data={breakouts.data}
          n={n}
          k={k}
          lookback={lookback}
          sort={effectiveSort}
          dir={dir}
          onSort={onSort}
          selected={selected}
          onSelect={setSelected}
        />
      ) : (
        <TrendView
          rows={trend.data?.rows ?? []}
          sort={effectiveSort}
          dir={dir}
          onSort={onSort}
          selected={selected}
          onSelect={setSelected}
        />
      )}
    </div>
  );
}

function BreakoutsView({
  data,
  n,
  k,
  lookback,
  sort,
  dir,
  onSort,
  selected,
  onSelect,
}: {
  data: ReturnType<typeof useBreakouts>['data'];
  n: number;
  k: number;
  lookback: number;
  sort: string;
  dir: 'asc' | 'desc';
  onSort: (key: string) => void;
  selected: string | null;
  onSelect: (symbol: string | null) => void;
}) {
  if (!data || data.summaries.length === 0) {
    return (
      <EmptyState heading="No symbols scanned">
        No bars are stored for the scan universe yet.
      </EmptyState>
    );
  }

  return (
    <>
      <div className="scan-layout">
        <div className="scan-layout__main">
          <BreakoutTable
            rows={data.summaries}
            sort={sort}
            dir={dir}
            onSort={onSort}
            onRowClick={(row) => onSelect(row.symbol)}
            selectedSymbol={selected}
          />
        </div>
        <aside className="scan-layout__side" aria-label="Open breakouts">
          <h2 className="scan-layout__side-title">Open now</h2>
          <OpenBreakouts breakouts={data.open_breakouts} k={k} onSelect={onSelect} />
        </aside>
      </div>

      {data.excluded.length > 0 && (
        <details className="scan-excluded">
          <summary>
            {data.excluded.length} symbol{data.excluded.length === 1 ? '' : 's'} excluded for
            gaps
          </summary>
          <ul>
            {data.excluded.map((entry) => (
              <li key={entry.symbol}>
                <strong>{entry.symbol}</strong> — {entry.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      {selected && (
        <BreakoutDetail
          symbol={selected}
          n={n}
          k={k}
          lookback={lookback}
          onClose={() => onSelect(null)}
        />
      )}
    </>
  );
}

function TrendView({
  rows,
  sort,
  dir,
  onSort,
  selected,
  onSelect,
}: {
  rows: NonNullable<ReturnType<typeof useTrend>['data']>['rows'];
  sort: string;
  dir: 'asc' | 'desc';
  onSort: (key: string) => void;
  selected: string | null;
  onSelect: (symbol: string | null) => void;
}) {
  if (rows.length === 0) {
    return (
      <EmptyState heading="No symbols scored">
        No bars are stored for the scan universe yet.
      </EmptyState>
    );
  }

  return (
    <>
      <TrendTable
        rows={rows}
        sort={sort}
        dir={dir}
        onSort={onSort}
        onRowClick={(row) => onSelect(row.symbol)}
        selectedSymbol={selected}
      />
      {selected && <TrendDetail symbol={selected} onClose={() => onSelect(null)} />}
    </>
  );
}
