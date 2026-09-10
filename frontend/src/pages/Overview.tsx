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
 * `/api/scan/flows` is still almost entirely "no data yet" (see the T56 task brief), this page
 * ships without a flows block and says nothing about flows -- exactly the degraded case the
 * README's own sentence anticipates.
 *
 * Each of the three blocks fetches independently (`useBreakouts`, `useTrend`, `useRegime`) and
 * renders its own loading/error/empty state, so one endpoint being slow or down never blanks
 * the panels that don't depend on it -- the same "a page never gates on the slowest query"
 * discipline `Scan.tsx` already follows for its own two views.
 */
import { useMemo } from 'react';
import { useBreakouts, useRegime, useTrend } from '../api/queries';
import { RegimeStrip } from '../components/regime/RegimeStrip';
import { OpenBreakouts } from '../components/scan/OpenBreakouts';
import { EmptyState } from '../components/EmptyState';
import { ErrorState } from '../components/ErrorState';
import { OverviewBlock } from '../components/overview/OverviewBlock';
import { BreakoutMiniTable } from '../components/overview/BreakoutMiniTable';
import { TrendMiniTable } from '../components/overview/TrendMiniTable';
import { RegimeMiniTable } from '../components/overview/RegimeMiniTable';
import {
  REGIME_HREF,
  continuationRegimeRows,
  rankBreakoutsByRate,
  rankTrendByComposite,
  scanBreakoutsHref,
  scanTrendHref,
} from '../components/overview/overviewRows';

export function Overview() {
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
      <OverviewBlock heading="Tape" to={REGIME_HREF} linkLabel="Open regime board →">
        <RegimeStrip />
      </OverviewBlock>

      <section aria-label="Where continuation is" className="overview-section">
        <h2 className="overview-section__title">Where continuation is</h2>

        <div className="overview-grid">
          <OverviewBlock heading="Top by breakout rate" to={scanBreakoutsHref('rate', 'desc')}>
            <BreakoutsRankedBody query={breakouts} rows={ranked.top} caption="Top 8 symbols by breakout continuation rate" />
          </OverviewBlock>
          <OverviewBlock heading="Top by trend composite" to={scanTrendHref()}>
            <TrendRankedBody query={trend} rows={rankedTrend.top} caption="Top 8 symbols by trend/chop composite" />
          </OverviewBlock>
        </div>
        {breakouts.data && ranked.unrankedCount > 0 && (
          <p className="overview-note">
            {ranked.unrankedCount} symbol{ranked.unrankedCount === 1 ? '' : 's'} excluded from
            both rate rankings below: fewer than five resolved breakout events in this lookback,
            so no rate can be quoted -- not zero, not fabricated.
          </p>
        )}

        <OverviewBlock heading="Fading — worst by breakout rate" to={scanBreakoutsHref('rate', 'asc')}>
          <BreakoutsRankedBody query={breakouts} rows={ranked.worst} caption="8 worst symbols by breakout continuation rate" />
        </OverviewBlock>
      </section>

      <section aria-label="Open now" className="overview-section">
        <h2 className="overview-section__title">Open now</h2>

        <div className="overview-grid">
          <OverviewBlock heading="Open breakouts" to={scanBreakoutsHref(null)}>
            {breakouts.isError ? (
              <ErrorState message="Could not load the breakout ledger." />
            ) : breakouts.isPending ? (
              <p className="scan-page__loading">Scanning the universe for range breaks…</p>
            ) : (
              <OpenBreakouts breakouts={breakouts.data?.open_breakouts ?? []} k={breakouts.data?.k ?? 5} />
            )}
          </OverviewBlock>
          <OverviewBlock heading="In continuation now" to={REGIME_HREF}>
            {regime.isError ? (
              <ErrorState message="Could not load the regime board." />
            ) : regime.isPending ? (
              <p className="scan-page__loading">Scoring dealer positioning across the universe…</p>
            ) : continuationRows.length === 0 ? (
              <EmptyState heading="No continuation verdicts">
                No symbol on the regime board currently reads a `continuation` verdict.
              </EmptyState>
            ) : (
              <RegimeMiniTable rows={continuationRows} caption="Regime rows with a continuation verdict" />
            )}
          </OverviewBlock>
        </div>
      </section>
    </div>
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
  if (query.isPending) return <p className="scan-page__loading">Scanning the universe for range breaks…</p>;
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
      <p className="scan-page__loading">
        Scoring the universe… the trend scan reads every tracked option chain, so this takes a
        few seconds.
      </p>
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
