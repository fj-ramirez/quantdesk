/**
 * `/overview` -- the continuation initiative's own answer to the question that started it
 * (T56, last task; spec in `plans/continuation/07-ui.md`'s "`/overview`" section and
 * `plans/continuation/README.md`'s "Problem" paragraph: the user fades breakouts on SPX, SPY,
 * QQQ, GLD and DIA and wants to see *which* markets currently have continuation and where money
 * is rotating). Three blocks, every number read from an endpoint that already backs another
 * page -- no new backend work, nothing fetched here that `/scan`, `/regime` or the strip
 * doesn't already fetch:
 *
 *  1. **Tape** -- `RegimeStrip`, imported the same way `Regime.tsx` does. It owns its own
 *     query and loading/error/`n/a` states, so it is rendered unconditionally.
 *  2. **Where continuation is** -- top 8 by breakout rate (with status) and top 8 by trend
 *     composite side by side, then the 8 worst by rate ("fading") below both.
 *  3. **Open now** -- the open-breakouts panel from `/scan` (the exact `OpenBreakouts`
 *     component, not reimplemented) and the regime rows whose verdict is `continuation`.
 *
 * **Nav position.** Shipped as the *last* nav tab (`AppShell.tsx`'s `NavBar`, already wired by
 * T55), not the landing page -- `plans/continuation/README.md`'s open-decision #3 is still open,
 * so the index route (`/`, `Dashboard`) is untouched here.
 *
 * **Null ranking rule.** `rate` (breakouts) and `composite` (trend) are `null` below their
 * respective data floors -- a real "not enough resolved events/history" state, not a zero and
 * not a low score. `overviewRows.ts`'s `rankBreakoutsByRate`/`rankTrendByComposite` exclude
 * null-valued symbols from the ranked pool entirely (not merely pin them to the end of a sort),
 * so neither the top-8 nor the worst-8 list can ever present "no data yet" as though it were
 * "measured and it's bad" -- see that module's own docstring for why pinning-to-the-end alone
 * would not be enough here. The excluded count renders as a plain note under each pair of lists
 * so the reader knows the ranking is over a subset, not silently missing symbols.
 *
 * **Flows.** Not surfaced here. 07-ui.md's `/overview` spec names exactly three blocks -- Tape,
 * "Where continuation is", "Open now" -- and none of them is flows; `plans/continuation/
 * README.md`'s dependency graph line for T56 ("T53 is not a dependency; the flows block is
 * added to the overview only if T53 has landed, otherwise the page ships without it") describes
 * an optional fourth block that 07-ui.md's own page spec never actually adds. Per that file's
 * own rule ("When a plan file and this file disagree, this file wins") and the fact that
 * `/api/gex/scan/flows` is still almost entirely "no data yet" (see the T56 task brief), this page
 * ships without a flows block and says nothing about flows -- exactly the degraded case the
 * README's own sentence anticipates.
 *
 * Each of the three blocks fetches independently (`useBreakouts`, `useTrend`, `useRegime`) and
 * renders its own loading/error/empty state, so one endpoint being slow or down never blanks
 * the panels that don't depend on it -- the same "a page never gates on the slowest query"
 * discipline `Scan.tsx` already follows for its own two views.
 *
 * **T65.** Applies `plans/ui-ux-refresh/README.md`'s reading order ("Tape/freshness -> concise
 * market read -> ranked signals -> links to evidence") and its shared primitives, without
 * touching any fetch, ranking, or link target:
 *  - A `PageHeader` names the page and states what it answers (the plan's own framing: "what
 *    is the market state now?"), where before there was no page-level heading at all.
 *  - The Tape block now opens with `CaptureFreshnessStrip` (the 5-symbol SPX/SPY/QQQ/GLD/DIA
 *    option-chain capture freshness, `components/overview/CaptureFreshnessStrip.tsx`) above
 *    the unmodified `RegimeStrip`, so "tape" now reads as *both* halves 07-ui.md's own
 *    reading-order step names (freshness, then cross-asset regime) instead of only the
 *    latter. This is a new, first-time surfacing of `useCaptureHealth`'s `symbols` block (no
 *    page rendered it before) -- not a recomputation of anything: every value is the API's own
 *    field, read once already by `BarsFreshness`/`Flows.tsx` for the same endpoint's other
 *    blocks.
 *  - `OverviewBlock` (T65, that file) now renders each panel on a `Surface` card; nothing
 *    about a block's heading, link, or content changed.
 *  - Every bare `<p className="scan-page__loading">` loading paragraph below is now
 *    `LoadingState` (same wording, `aria-live="polite"` added) -- the one thing T64 already
 *    extracted this shared triad member for.
 *
 * **T69 -- density pass.** The user's own review (informed by a ChatGPT critique,
 * `plans/ui-ux-refresh/02-first-pass-review`) found the mobile Overview made the reader scroll
 * past several rows of freshness cards and a nine-tile regime strip before reaching the ranked
 * signals. Two changes, no fetch/ranking/link-target touched:
 *  - `Tape` now holds only `CaptureFreshnessStrip`, itself compacted from five `MetricCard`s to
 *    one `density="compact"` status line with the per-symbol breakdown behind a `<details>`
 *    (see that component's own docstring).
 *  - `RegimeStrip` moved out of `Tape` and now renders, in `compact` mode (primary vol/term-
 *    structure tiles up front, the rest behind a disclosure -- see `RegimeStrip`'s own
 *    docstring for the split and why), *after* "Where continuation is" instead of immediately
 *    after the freshness line -- the plan's own instruction ("so it reads before the full
 *    RegimeStrip, not after it"). `/regime` itself is untouched and still renders the complete,
 *    unabbreviated strip via a bare `<RegimeStrip />`.
 *
 * **T122 -- tabs.** Even after T69 the page stacked six blocks and ran past four screens,
 * most of it the open-breakouts card list. Below the Tape line the blocks now sit behind
 * in-section tabs (`?tab=`, URL state) in the same reading order -- Continuation, Fading,
 * Open now, Regime -- and open breakouts are a compact table paging at ten. Every block,
 * fetch, ranking and link target is unchanged; the unranked-count note travels with both
 * rate tabs, since both rankings are over the same subset.
 */
import { useMemo } from 'react';
import { useBreakouts, useRegime, useTrend } from '../api/queries';
import { RegimeStrip } from '../components/regime/RegimeStrip';
import { OpenBreakouts } from '../components/scan/OpenBreakouts';
import { CaptureFreshnessStrip } from '../components/overview/CaptureFreshnessStrip';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { LoadingState } from '../components/LoadingState';
import { OverviewBlock } from '../components/overview/OverviewBlock';
import { BreakoutMiniTable } from '../components/overview/BreakoutMiniTable';
import { TrendMiniTable } from '../components/overview/TrendMiniTable';
import { RegimeMiniTable } from '../components/overview/RegimeMiniTable';
import { PageHeader } from '../../../components/ui/PageHeader';
import { Tabs } from '../../../components/ui/Tabs';
import { useTabParam } from '../../../components/ui/useTabParam';
import {
  REGIME_HREF,
  continuationRegimeRows,
  rankBreakoutsByRate,
  rankTrendByComposite,
  scanBreakoutsHref,
  scanTrendHref,
} from '../components/overview/overviewRows';

const OVERVIEW_TABS = ['continuation', 'fading', 'open', 'regime'] as const;
type OverviewTab = (typeof OVERVIEW_TABS)[number];

export function Overview() {
  const [tab, setTab] = useTabParam<OverviewTab>('tab', OVERVIEW_TABS);
  const breakouts = useBreakouts();
  const trend = useTrend();
  const regime = useRegime('ALL');

  const ranked = useMemo(
    () => rankBreakoutsByRate(breakouts.data?.summaries ?? []),
    [breakouts.data],
  );
  const rankedTrend = useMemo(() => rankTrendByComposite(trend.data?.rows ?? []), [trend.data]);
  const continuationRows = useMemo(
    () => continuationRegimeRows(regime.data?.rows ?? []),
    [regime.data],
  );

  return (
    <div className="overview-page">
      <PageHeader
        title="Overview"
        description="What is the market state now, and where is continuation happening across the tracked universe?"
      />

      <OverviewBlock heading="Tape" to={REGIME_HREF} linkLabel="Open regime board →" headingLevel="h2">
        <CaptureFreshnessStrip />
      </OverviewBlock>

      <Tabs
        label="Overview sections"
        active={tab}
        onChange={setTab}
        tabs={[
          { value: 'continuation', label: 'Where continuation is' },
          { value: 'fading', label: 'Fading' },
          {
            value: 'open',
            label: 'Open now',
            count: breakouts.data ? breakouts.data.open_breakouts.length : null,
          },
          { value: 'regime', label: 'Cross-asset regime' },
        ]}
      >
        {tab === 'continuation' && (
          <>
            <div className="overview-grid">
              <OverviewBlock heading="Top by breakout rate" to={scanBreakoutsHref('rate', 'desc')}>
                <BreakoutsRankedBody query={breakouts} rows={ranked.top} caption="Top 8 symbols by breakout continuation rate" />
              </OverviewBlock>
              <OverviewBlock heading="Top by trend composite" to={scanTrendHref()}>
                <TrendRankedBody query={trend} rows={rankedTrend.top} caption="Top 8 symbols by trend/chop composite" />
              </OverviewBlock>
            </div>
            <UnrankedNote count={breakouts.data ? ranked.unrankedCount : 0} />
          </>
        )}

        {tab === 'fading' && (
          <>
            <OverviewBlock heading="Fading — worst by breakout rate" to={scanBreakoutsHref('rate', 'asc')}>
              <BreakoutsRankedBody query={breakouts} rows={ranked.worst} caption="8 worst symbols by breakout continuation rate" />
            </OverviewBlock>
            <UnrankedNote count={breakouts.data ? ranked.unrankedCount : 0} />
          </>
        )}

        {tab === 'open' && (
          <div className="overview-grid">
            <OverviewBlock heading="Open breakouts" to={scanBreakoutsHref(null)}>
              {breakouts.isError ? (
                <ErrorState message="Could not load the breakout ledger." />
              ) : breakouts.isPending ? (
                <LoadingState message="Scanning the universe for range breaks…" />
              ) : (
                <OpenBreakouts breakouts={breakouts.data?.open_breakouts ?? []} k={breakouts.data?.k ?? 5} pageSize={10} />
              )}
            </OverviewBlock>
            <OverviewBlock heading="In continuation now" to={REGIME_HREF}>
              {regime.isError ? (
                <ErrorState message="Could not load the regime board." />
              ) : regime.isPending ? (
                <LoadingState message="Scoring dealer positioning across the universe…" />
              ) : continuationRows.length === 0 ? (
                <EmptyState heading="No continuation verdicts">
                  No symbol on the regime board currently reads a `continuation` verdict.
                </EmptyState>
              ) : (
                <RegimeMiniTable rows={continuationRows} caption="Regime rows with a continuation verdict" />
              )}
            </OverviewBlock>
          </div>
        )}

        {/* T69's compact strip; `/regime` still renders the complete one. */}
        {tab === 'regime' && (
          <OverviewBlock heading="Cross-asset regime" to={REGIME_HREF} linkLabel="Open regime board →">
            <RegimeStrip compact />
          </OverviewBlock>
        )}
      </Tabs>
    </div>
  );
}

function UnrankedNote({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <p className="overview-note">
      {count} symbol{count === 1 ? '' : 's'} excluded from both rate rankings: fewer than five
      resolved breakout events in this lookback, so no rate can be quoted -- not zero, not
      fabricated.
    </p>
  );
}

function BreakoutsRankedBody({
  query,
  rows,
  caption,
}: {
  query: ReturnType<typeof useBreakouts>;
  rows: ReturnType<typeof rankBreakoutsByRate>['top'];
  caption: string;
}) {
  if (query.isError) return <ErrorState message="Could not load the breakout ledger." />;
  if (query.isPending) return <LoadingState message="Scanning the universe for range breaks…" />;
  if (!query.data || query.data.summaries.length === 0) {
    return (
      <EmptyState heading="No symbols scanned">No bars are stored for the scan universe yet.</EmptyState>
    );
  }
  if (rows.length === 0) {
    return (
      <EmptyState heading="No symbols have a quoted rate">
        Every symbol in this window has fewer than five resolved breakout events -- see the note
        below.
      </EmptyState>
    );
  }
  return <BreakoutMiniTable rows={rows} caption={caption} />;
}

function TrendRankedBody({
  query,
  rows,
  caption,
}: {
  query: ReturnType<typeof useTrend>;
  rows: ReturnType<typeof rankTrendByComposite>['top'];
  caption: string;
}) {
  if (query.isError) return <ErrorState message="Could not load the trend scorer." />;
  if (query.isPending) {
    return (
      <LoadingState message="Scoring the universe… the trend scan reads every tracked option chain, so this takes a few seconds." />
    );
  }
  if (!query.data || query.data.rows.length === 0) {
    return <EmptyState heading="No symbols scored">No bars are stored for the scan universe yet.</EmptyState>;
  }
  if (rows.length === 0) {
    return (
      <EmptyState heading="No symbols have a composite score">
        Every symbol lacks enough history for at least one of the four trend components.
      </EmptyState>
    );
  }
  return <TrendMiniTable rows={rows} caption={caption} />;
}
