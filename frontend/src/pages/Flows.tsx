/**
 * `/flows` -- ETF creation/redemption flow board (T53; spec in `plans/continuation/07-ui.md`'s
 * "`/flows` -- ETF flows (T53, needs T52)" section, source survey and freshness rules from
 * `docs/etf-flows-sources.md`). This task is **frontend only** -- nothing under `backend/` was
 * touched, and nothing here reads any endpoint beyond `GET /api/scan/flows?window=` and the
 * `flows` block of `GET /api/health/capture`, both already live.
 *
 * A universe page, not a symbol page (07-ui.md's "Information architecture"): the TopBar
 * renders its scan toolbar here, and `window` -- already provided, validated against
 * `5 | 20 | 60`, by `useScanParams` -- drives the chart, same "URL drives every view" contract
 * every scan-family page follows.
 *
 * **The empty and short-history presentations are the main event, not an edge case.** The
 * shares-outstanding table only started accumulating the day T52's job first ran, and issuer
 * files publish one day at a time with no backfill path (docs/etf-flows-sources.md). As of
 * this task, 22 of 23 supported funds have zero stored rows ("no data yet") and the 23rd
 * (`XLK`) has exactly one ("history since <date>", not enough for even a one-day flow) --
 * `FlowBars` below renders that honestly (an `EmptyState`, not a blank chart) rather than
 * inventing a number, per the plan's explicit "never draw a symbol with no usable data at
 * zero" rule. This is expected to look "empty" today and fill in as history accumulates.
 *
 * **The compact table's Trend column is three real numbers, not a fabricated daily curve.**
 * See `FlowSparkline.tsx`'s own docstring for why: there is no daily-history endpoint, only
 * `flow_pct` at each of the three supported windows, so this page fetches all three
 * (`useFlows(5|20|60)`) regardless of which one the toolbar has active, and plots exactly
 * those three real points.
 *
 * **T66.** `PageHeader` names the page and what it answers. Per the plan's own example
 * ("Flows' source-lag banner content" as the caveat-slot fit), `FlowsBanner` -- the 4th state
 * beyond the usual error/pending/empty triad -- now renders inside `PageHeader`'s `caveat`
 * slot rather than as a freestanding section between the toolbar and the chart: same
 * component, same props, same conditional content (families lag / unsupported-fund count),
 * only moved earlier in the reading order (page question -> caveat/freshness -> toolbar ->
 * chart/table), per the plan's own template for this page group. The Window toggle now
 * renders through `ui/Toolbar`'s `SegmentedControl`; the compact per-fund table is wrapped in
 * `DataTableFrame`. Nothing about the banner's, the "no flow data" list's, or the table's own
 * fetch/render logic changed -- the 4-state model (loading, error, empty, and this banner-plus
 * -no_flow_data state) is otherwise untouched.
 */
import { useCallback } from 'react';
import { useFlows, useCaptureHealth } from '../api/queries';
import { useScanParams, SCAN_WINDOW_VALUES } from '../state/urlState';
import type { FlowsResponse, FlowSymbol } from '../api/types';
import type { ColumnDef } from '../components/scan/ScanTable';
import { ScanTable } from '../components/scan/ScanTable';
import { SymbolCell } from '../components/scan/SymbolCell';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { FlowBars } from '../components/flows/FlowBars';
import { FlowSparkline, type FlowSparklinePoint } from '../components/flows/FlowSparkline';
import { PageHeader } from '../components/ui/PageHeader';
import { SegmentedControl, Toolbar } from '../components/ui/Toolbar';
import { DataTableFrame } from '../components/ui/DataTableFrame';
import { formatBarsThrough } from '../lib/time';
import { formatSignedPct } from '../lib/format';

const FAMILY_LABEL: Record<string, string> = {
  spdr: 'SPDR',
  ishares: 'iShares',
};

const FLOWS_DEFAULT_SORT = 'flow_pct';

/** Reads one symbol's `flow_pct` out of a (possibly still-loading) window response. `null`
 * both when the symbol genuinely has no computed flow and while the query hasn't resolved yet
 * -- `FlowSparkline` treats both as "no point here", which is the honest rendering either way
 * (a viewer never sees a value flash in from nothing). */
function pctFor(data: FlowsResponse | undefined, symbol: string): number | null {
  return data?.symbols.find((s) => s.symbol === symbol)?.flow_pct ?? null;
}

interface FlowRow {
  symbol: string;
  flow_pct: number | null;
  message: string | null;
  points: FlowSparklinePoint[];
}

export function Flows() {
  const { window: activeWindow, setWindow, sort, dir, setSort } = useScanParams();
  const effectiveSort = sort ?? FLOWS_DEFAULT_SORT;

  const onSort = useCallback(
    (key: string) => {
      // Same column flips direction; a new column starts at desc -- every scan-family page's
      // policy, copied verbatim here (Scan.tsx / Regime.tsx / Rotation.tsx).
      if (key === effectiveSort) setSort(key, dir === 'desc' ? 'asc' : 'desc');
      else setSort(key, 'desc');
    },
    [effectiveSort, dir, setSort],
  );

  const flows5 = useFlows(5);
  const flows20 = useFlows(20);
  const flows60 = useFlows(60);
  const byWindow = { 5: flows5, 20: flows20, 60: flows60 } as const;
  const active = byWindow[activeWindow as 5 | 20 | 60] ?? flows20;

  const captureHealth = useCaptureHealth();

  const columns: ColumnDef<FlowRow>[] = [
    {
      key: 'symbol',
      header: 'Symbol',
      sortable: true,
      // No dashboard param is worth carrying across from this page's own `window` toolbar
      // (the dashboard has no matching control), unlike Regime.tsx's `filterSearch` -- so
      // this omits `search` and lets `SymbolCell` build a fresh `?symbol=...` link.
      format: (_v, row) => <SymbolCell symbol={row.symbol} />,
    },
    {
      key: 'flow_pct',
      header: `Flow % (${activeWindow}d)`,
      align: 'right',
      sortable: true,
      format: (v, row) => (v == null ? (row.message ?? '—') : formatSignedPct(v as number | null)),
    },
    {
      key: 'points',
      header: 'Trend (5d · 20d · 60d)',
      sortable: false,
      format: (_v, row) => (
        <FlowSparkline points={row.points} ariaLabel={`${row.symbol} flow trend, 5, 20 and 60 days`} />
      ),
    },
  ];

  function toRows(symbols: FlowSymbol[]): FlowRow[] {
    return symbols.map((s) => ({
      symbol: s.symbol,
      flow_pct: s.flow_pct,
      message: s.message,
      points: [
        { label: '5d', value: pctFor(flows5.data, s.symbol) },
        { label: '20d', value: pctFor(flows20.data, s.symbol) },
        { label: '60d', value: pctFor(flows60.data, s.symbol) },
      ],
    }));
  }

  return (
    <div className="flows-page">
      <PageHeader
        title="Flows"
        description="ETF creation/redemption flow per fund, relative to AUM."
        caveat={
          <FlowsBanner
            families={captureHealth.data?.flows.families}
            fallbackSources={active.data?.sources}
            noFlowData={active.data?.no_flow_data}
          />
        }
      />

      <Toolbar>
        <SegmentedControl label="Window" values={SCAN_WINDOW_VALUES} active={activeWindow} render={(v) => `${v}d`} onChange={setWindow} />
      </Toolbar>

      {active.isError ? (
        <ErrorState message="Could not load ETF flow data." />
      ) : active.isPending ? (
        <LoadingState message="Reading ETF flow data…" />
      ) : !active.data ? (
        <EmptyState heading="No flow data available" />
      ) : (
        <>
          <section aria-labelledby="flows-chart-title" className="flows-chart-section">
            <h2 id="flows-chart-title" className="scan-layout__side-title">
              Net flow, percent of AUM ({activeWindow}d)
            </h2>
            <FlowBars symbols={active.data.symbols} />
          </section>

          {active.data.no_flow_data.length > 0 && (
            <section aria-label="No flow data" className="flows-no-data">
              <h3 className="scan-layout__side-title">No flow data</h3>
              <ul>
                {active.data.no_flow_data.map((row) => (
                  <li key={row.symbol}>
                    <strong>{row.symbol}</strong> — {row.reason}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <DataTableFrame
            title="Flow percent and trend"
            readingCue="Per-fund flow percent and trend across the three supported windows."
          >
            <ScanTable<FlowRow>
              columns={columns}
              rows={toRows(active.data.symbols)}
              sort={effectiveSort}
              dir={dir}
              onSort={onSort}
              rowKey={(row) => row.symbol}
              caption="Per-fund flow percent and trend across the three supported windows"
            />
          </DataTableFrame>
        </>
      )}
    </div>
  );
}

/** The data-source banner: which family, and its newest stored row's as-of date -- prefers
 * `/api/health/capture`'s `flows` block (07-ui.md's instruction: "from the flows health
 * block"), falling back to the current `/api/scan/flows` response's own `sources` field
 * (field-for-field identical, per `FlowsFamilySourceOut`'s own backend docstring) while
 * capture health is still loading or its query fails, so the banner never goes blank just
 * because a second, independent query hasn't resolved yet. */
function FlowsBanner({
  families,
  fallbackSources,
  noFlowData,
}: {
  families: { family: string; last_as_of_date: string | null }[] | undefined;
  fallbackSources: { family: string; last_as_of_date: string | null }[] | undefined;
  noFlowData: { symbol: string }[] | undefined;
}) {
  const rows = families ?? fallbackSources;

  return (
    <section aria-label="Flow data sources" className="flows-banner">
      <p className="flows-banner__lag">
        These are each issuer's own as-of date, not today's session — the numbers below
        describe the last day the issuer published, which routinely lags the current trading
        day by about one day (docs/etf-flows-sources.md).
      </p>
      {rows && (
        <dl className="flows-banner__list">
          {rows.map((f) => (
            <div key={f.family} className="flows-banner__entry">
              <dt>{FAMILY_LABEL[f.family] ?? f.family}</dt>
              <dd>{f.last_as_of_date ? formatBarsThrough(f.last_as_of_date) : 'no data yet'}</dd>
            </div>
          ))}
        </dl>
      )}
      {noFlowData && noFlowData.length > 0 && (
        <p className="flows-banner__unsupported">
          {noFlowData.length} fund{noFlowData.length === 1 ? '' : 's'} unsupported — no working
          shares-outstanding source (see the "No flow data" list below).
        </p>
      )}
    </section>
  );
}
