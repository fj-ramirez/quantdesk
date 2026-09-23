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
 *
 * T66: `PageHeader` names the page and what it answers; the view/N/k/Lookback button rows
 * (each page's own hand-rolled `ChoiceRow`) now render through `ui/Toolbar`'s
 * `Toolbar`/`SegmentedControl` -- identical DOM/classes/`aria-pressed` behavior, since that
 * primitive generalized this exact pattern from this file (see its own docstring), not a new
 * one. Both tables are wrapped in `DataTableFrame`; `BreakoutDetail`/`TrendDetail` are now
 * mounted through `ui/DetailDrawer` instead of rendering their own unmanaged header/close
 * button, fixing the same missing focus-trap/Escape/focus-return `01-ux-baseline.md` flagged
 * for every detail panel in the app and T64 already fixed for `OpportunityDetail`. Per
 * `DetailDrawer`'s own contract, it is always mounted (never `{selected && <DetailDrawer/>}`)
 * so `useOverlayDismiss`'s focus-return effect survives close.
 */
import { useCallback, useState } from 'react';
import { useBreakouts, useTrend } from '../api/queries';
import {
  useScanParams,
  SCAN_K_VALUES,
  SCAN_LOOKBACK_VALUES,
  SCAN_N_VALUES,
  SCAN_VIEWS,
  type ScanView,
} from '../state/urlState';
import { RegimeStrip } from '../components/regime/RegimeStrip';
import { BreakoutTable, BREAKOUT_DEFAULT_SORT } from '../components/scan/BreakoutTable';
import { BreakoutDetail } from '../components/scan/BreakoutDetail';
import { OpenBreakouts } from '../components/scan/OpenBreakouts';
import { TrendTable, TREND_DEFAULT_SORT } from '../components/scan/TrendTable';
import { TrendDetail } from '../components/scan/TrendDetail';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { PageHeader } from '../../../components/ui/PageHeader';
import { SegmentedControl, Toolbar } from '../../../components/ui/Toolbar';
import { Tabs } from '../../../components/ui/Tabs';
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { DetailDrawer } from '../../../components/ui/DetailDrawer';

const VIEW_LABEL: Record<ScanView, string> = { breakouts: 'Breakouts', trend: 'Trend' };

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
      <PageHeader
        title="Scan"
        description="Which symbols just broke range, and which are trending or chopping, across the tracked universe."
      />

      {/* T54's cross-asset strip. 07-ui.md renders it at the top of both `/scan` and
          `/regime`: it answers "what kind of tape is this" for everything at once, which is
          the context the per-symbol rows below are read against. It owns its own query and
          its own loading/error/`n/a` states. T122: compact (primary tiles, the rest behind a
          disclosure), as on Overview -- `/regime` keeps the full strip. */}
      <RegimeStrip compact />

      {/* T122: the Breakouts/Trend view switch is now a tab strip over the same `view` URL
          param; the breakout-only toggles moved inside the Breakouts panel they govern. */}
      <Tabs
        label="Scan view"
        tabs={SCAN_VIEWS.map((value) => ({ value, label: VIEW_LABEL[value] }))}
        active={view}
        onChange={(next) => {
          setView(next);
          setSelected(null);
        }}
      >
        {view === 'breakouts' && (
          <Toolbar>
            <SegmentedControl label="N" values={SCAN_N_VALUES} active={n} render={String} onChange={setN} />
            <SegmentedControl label="k" values={SCAN_K_VALUES} active={k} render={String} onChange={setK} />
            <SegmentedControl
              label="Lookback"
              values={SCAN_LOOKBACK_VALUES}
              active={lookback}
              render={String}
              onChange={setLookback}
            />
          </Toolbar>
        )}

        {active.isError ? (
          <ErrorState
            message={
              view === 'breakouts'
                ? 'Could not load the breakout ledger.'
                : 'Could not load the trend scorer.'
            }
          />
        ) : active.isPending ? (
          <LoadingState
            message={
              view === 'breakouts'
                ? 'Scanning the universe for range breaks…'
                : 'Scoring the universe… the trend scan reads every tracked option chain, so this takes a few seconds.'
            }
          />
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
      </Tabs>
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
  const empty = !data || data.summaries.length === 0;

  return (
    <>
      {empty ? (
        <EmptyState heading="No symbols scanned">
          No bars are stored for the scan universe yet.
        </EmptyState>
      ) : (
        <>
          <div className="scan-layout">
            <div className="scan-layout__main">
              <DataTableFrame
                title="Breakout continuation"
                readingCue="Continuation rate and follow-through for each symbol's breakout events in the current lookback window."
              >
                <BreakoutTable
                  rows={data.summaries}
                  sort={sort}
                  dir={dir}
                  onSort={onSort}
                  onRowClick={(row) => onSelect(row.symbol)}
                  selectedSymbol={selected}
                  pageSize={20}
                />
              </DataTableFrame>
            </div>
            <aside className="scan-layout__side" aria-label="Open breakouts">
              <h2 className="scan-layout__side-title">Open now</h2>
              <OpenBreakouts breakouts={data.open_breakouts} k={k} onSelect={onSelect} pageSize={10} />
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
        </>
      )}

      <DetailDrawer
        open={selected != null}
        onClose={() => onSelect(null)}
        title={selected ? `${selected} breakout detail` : ''}
      >
        {selected && <BreakoutDetail symbol={selected} n={n} k={k} lookback={lookback} />}
      </DetailDrawer>
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
  return (
    <>
      {rows.length === 0 ? (
        <EmptyState heading="No symbols scored">
          No bars are stored for the scan universe yet.
        </EmptyState>
      ) : (
        <DataTableFrame
          title="Trend and chop"
          readingCue="Composite is the mean of four cross-sectional percentile ranks (ADX, ER, CHOP, VR). IV/RV is shown but is not part of it."
        >
          <TrendTable
            rows={rows}
            sort={sort}
            dir={dir}
            onSort={onSort}
            onRowClick={(row) => onSelect(row.symbol)}
            selectedSymbol={selected}
            pageSize={20}
          />
        </DataTableFrame>
      )}

      <DetailDrawer
        open={selected != null}
        onClose={() => onSelect(null)}
        title={selected ? `${selected} trend detail` : ''}
      >
        {selected && <TrendDetail symbol={selected} />}
      </DetailDrawer>
    </>
  );
}
