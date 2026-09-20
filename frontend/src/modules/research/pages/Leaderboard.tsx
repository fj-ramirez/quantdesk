/**
 * `/research` — the leaderboard (T78).
 *
 * The page's job is to make one thing unmissable: **almost nothing here clears the noise
 * ceiling, and that is the finding.** The ceiling banner sits above the table, the caveats sit
 * with it, and every row carries its own verdict. A version of this page that ranked rows and
 * said nothing else would be actively misleading, which is why the ceiling is part of the same
 * API response as the rows rather than a separate call that could fail on its own.
 */
import { useMemo, useState } from 'react';
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { PageHeader } from '../../../components/ui/PageHeader';
import { EmptyState } from '../../gex/components/EmptyState';
import { ErrorState } from '../../gex/components/ErrorState';
import { useLeaderboard, useResearchStatus } from '../api/queries';
import { DEFAULT_FILTERS, FilterBar, type FilterState } from '../components/FilterBar';
import { LeaderboardTable } from '../components/LeaderboardTable';
import { NoiseCeilingBanner, ResearchCaveats } from '../components/NoiseCeilingBanner';
import { StatusStrip } from '../components/StatusStrip';
import { TrialDrawer } from '../components/TrialDrawer';

const PAGE_SIZE = 40;

export function Leaderboard() {
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);

  const status = useResearchStatus();
  const query = useMemo(
    () => ({
      market: filters.market || undefined,
      strategy: filters.strategy || undefined,
      timeframe: filters.timeframe || undefined,
      minTradesOos: filters.minTradesOos,
      minExposure: filters.minExposure,
      limit: PAGE_SIZE,
      offset,
    }),
    [filters, offset],
  );
  const leaderboard = useLeaderboard(query);

  const data = leaderboard.data;
  const rows = data?.rows ?? [];
  const aboveCount = rows.filter((r) => r.above_ceiling).length;
  const hasFutures = rows.some((r) => r.market === 'futures');

  return (
    <div className="research-page">
      <PageHeader
        title="EdgeLab leaderboard"
        description="Every strategy/parameter combination the nightly search has tested, ranked by out-of-sample Sharpe."
        caveat={<ResearchCaveats hasFutures={hasFutures} />}
      />

      <StatusStrip status={status.data} />

      <FilterBar
        value={filters}
        status={status.data}
        onChange={(next) => {
          setFilters(next);
          // A filter change makes the old offset meaningless — page 3 of the previous result
          // set is not page 3 of this one.
          setOffset(0);
        }}
      />

      {leaderboard.isError ? (
        <ErrorState
          message={
            leaderboard.error instanceof Error
              ? `Could not load the leaderboard: ${leaderboard.error.message}`
              : 'Could not load the leaderboard.'
          }
        />
      ) : leaderboard.isPending ? (
        <p className="research-loading">Loading the leaderboard…</p>
      ) : data == null || data.total_trials === 0 ? (
        <EmptyState heading="No trials recorded yet">
          The research worker records a trial for every combination it tests. Run a cycle with{' '}
          <code>python -m app.modules.research.nightly --trials 50</code>, or wait for the
          nightly search.
        </EmptyState>
      ) : (
        <>
          {/* Above the table, always — the ceiling is the context the rows are read in, and a
              reader who scrolls straight to the numbers should have passed it first. */}
          <NoiseCeilingBanner
            noiseCeiling={data.noise_ceiling}
            totalTrials={data.total_trials}
            medianOosYears={data.median_oos_years}
            aboveCount={aboveCount}
            rowCount={rows.length}
          />

          <DataTableFrame
            title="Ranked trials"
            readingCue="Ranked by out-of-sample Sharpe. Dimmed rows did not clear the noise ceiling for their own OOS span — on this evidence they are indistinguishable from luck."
            sourceTiming={`${data.total.toLocaleString()} rows match these filters; showing ${rows.length}.`}
          >
            {rows.length === 0 ? (
              <EmptyState heading="No rows match these filters">
                Every trial is still recorded — loosen the minimum fills or exposure to see more.
              </EmptyState>
            ) : (
              <LeaderboardTable rows={rows} onSelect={setSelected} />
            )}
          </DataTableFrame>

          <div className="research-pager">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </button>
            <span>
              {data.total === 0 ? 0 : offset + 1}–{offset + rows.length} of{' '}
              {data.total.toLocaleString()}
            </span>
            <button
              type="button"
              disabled={offset + rows.length >= data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </button>
          </div>
        </>
      )}

      <TrialDrawer hash={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
