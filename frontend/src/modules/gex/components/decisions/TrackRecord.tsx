/**
 * T61 -- the decision engine's track record: what happened to the opportunities it recorded.
 *
 * Two tables over `GET /api/gex/decisions/history`: the summary (overall, then by setup, then by
 * grade -- n, pending, resolved, hit rate, win rate, average and total R) and the ledger of
 * stored rows, newest first, each with its outcome chip and its R. Results are in R, multiples
 * of the planned entry-to-stop risk; the backend's `note` explains the scoring rules and is
 * rendered verbatim above the tables so a reader never has to guess what "hit" means.
 *
 * Honesty rules, matching the rest of the scan family: a rate the backend withholds (fewer
 * than five resolved trades) renders as `n<5`, never `0%`; an unrealized mark on a pending row
 * is labelled as unrealized, never shown as a result; a bare dash is a `null`, never a zero.
 *
 * "Record now" runs the 17:45 ET job on demand (`POST /api/gex/decisions/record`): it writes
 * today's opportunities if they are not stored yet and re-scores every pending row. The
 * button reports the run's counts inline; a failure message never echoes a raw body.
 *
 * T122: the ledger pages at fifteen rows (newest first, so page 1 is the latest decisions);
 * the summary above it always covers every row, so paging never changes a rate.
 */
import { useDecisionsHistory, useRecordDecisions } from '../../api/queries';
import type { DecisionGroupStats, DecisionRecord } from '../../api/types';
import { InfoTip } from '../../../../components/ui/InfoTip';
import { Pagination } from '../../../../components/ui/Pagination';
import { DEFAULT_PAGE_SIZE, usePagination } from '../../../../components/ui/usePagination';
import { formatPrice } from '../../../../lib/format';
import { EmptyState } from '../EmptyState';
import { ErrorState } from '../ErrorState';
import { LoadingState } from '../LoadingState';
import { StatusChip } from '../scan/StatusChip';
import { SymbolCell } from '../scan/SymbolCell';
import { setupLabel } from './opportunityRows';

const DASH = '—';

function rate(value: number | null): string {
  if (value == null) return 'n<5';
  return `${Math.round(value * 100)}%`;
}

function r(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}R`;
}

function rClass(value: number | null): string {
  if (value == null) return '';
  return value >= 0 ? 'track-record__r--pos' : 'track-record__r--neg';
}

function StatsRow({ label, stats, group }: { label: string; stats: DecisionGroupStats; group?: string }) {
  return (
    <tr>
      <th scope="row">
        {group && <span className="track-record__group">{group} </span>}
        {label}
      </th>
      <td>{stats.n}</td>
      <td>{stats.pending}</td>
      <td>{stats.untriggered}</td>
      <td>{stats.resolved}</td>
      <td>{stats.targets}</td>
      <td>{stats.stops}</td>
      <td>{stats.expired}</td>
      <td>{rate(stats.hit_rate)}</td>
      <td>{rate(stats.win_rate)}</td>
      <td className={rClass(stats.avg_r)}>{r(stats.avg_r)}</td>
      <td className={rClass(stats.total_r)}>{r(stats.total_r)}</td>
    </tr>
  );
}

function ResultCell({ record }: { record: DecisionRecord }) {
  if (record.result_r != null) {
    return <span className={rClass(record.result_r)}>{r(record.result_r)}</span>;
  }
  if (record.mark_r != null) {
    return (
      <span className={rClass(record.mark_r)}>
        {r(record.mark_r)}
        <span className="track-record__unrealized">unrealized</span>
      </span>
    );
  }
  return <span>{DASH}</span>;
}

export interface TrackRecordProps {
  filterSearch: string;
}

export function TrackRecord({ filterSearch }: TrackRecordProps) {
  const history = useDecisionsHistory();
  const run = useRecordDecisions();
  const records = history.data?.records ?? [];
  const ledger = usePagination(records, DEFAULT_PAGE_SIZE, records.length);

  return (
    <section className="track-record" aria-label="Track record">
      <header className="track-record__head">
        <h2 className="scan-layout__side-title">Track record</h2>
        <div className="track-record__run">
          <button
            type="button"
            className="scan-detail__close"
            onClick={() => run.mutate()}
            disabled={run.isPending}
          >
            {run.isPending ? 'Recording…' : 'Record now'}
          </button>
          {run.isSuccess && (
            <span role="status">
              recorded {run.data.recorded}, scored {run.data.evaluated}, resolved {run.data.resolved}
              {run.data.errors.length > 0 ? `, ${run.data.errors.length} error(s)` : ''}
            </span>
          )}
          {run.isError && <span role="alert">The record run failed.</span>}
        </div>
      </header>

      {history.isError ? (
        <ErrorState message="Could not load the track record." />
      ) : history.isPending ? (
        <LoadingState message="Loading the track record…" />
      ) : !history.data ? null : (
        <>
          <p className="track-record__note">{history.data.note}</p>
          {history.data.summary.overall.n === 0 ? (
            <EmptyState heading="Nothing recorded yet">
              The 17:45 ET job records each day&apos;s opportunities and scores the pending ones
              against the bars that follow. Use Record now to write today&apos;s.
            </EmptyState>
          ) : (
            <>
              <div className="scan-table-container" tabIndex={0}>
                <table className="track-record__stats" aria-label="Track record summary">
                  <thead>
                    <tr>
                      <th>Bucket</th>
                      <th>
                        n<InfoTip label="the n column">Recorded opportunities.</InfoTip>
                      </th>
                      <th>pending</th>
                      <th>
                        untrig.
                        <InfoTip label="the untriggered column">
                          Fades whose wall was never touched.
                        </InfoTip>
                      </th>
                      <th>resolved</th>
                      <th>target</th>
                      <th>stop</th>
                      <th>expired</th>
                      <th>
                        hit
                        <InfoTip label="the hit column" align="end">
                          Targets over resolved trades.
                        </InfoTip>
                      </th>
                      <th>
                        win
                        <InfoTip label="the win column" align="end">
                          Trades closed above 0R over resolved trades.
                        </InfoTip>
                      </th>
                      <th>
                        avg R
                        <InfoTip label="the average R column" align="end">
                          Mean result of resolved trades, in R.
                        </InfoTip>
                      </th>
                      <th>total R</th>
                    </tr>
                  </thead>
                  <tbody>
                    <StatsRow label="All" stats={history.data.summary.overall} />
                    {Object.entries(history.data.summary.by_setup).map(([setup, stats]) => (
                      <StatsRow key={`setup:${setup}`} group="setup" label={setup} stats={stats} />
                    ))}
                    {Object.entries(history.data.summary.by_grade).map(([grade, stats]) => (
                      <StatsRow key={`grade:${grade}`} group="grade" label={grade} stats={stats} />
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="scan-table-container" tabIndex={0}>
                <table className="scan-table" aria-label="Recorded opportunities">
                  <thead>
                    <tr>
                      <th>Decided</th>
                      <th>Symbol</th>
                      <th>Setup</th>
                      <th>Side</th>
                      <th>Grade</th>
                      <th>Entry</th>
                      <th>Stop</th>
                      <th>Target</th>
                      <th>Outcome</th>
                      <th>Fill</th>
                      <th>R</th>
                      <th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.pageRows.map((record) => (
                      <tr key={record.id}>
                        <td>{record.decided_on}</td>
                        <td>
                          <SymbolCell symbol={record.underlying} search={filterSearch} />
                        </td>
                        <td>{setupLabel(record.key)}</td>
                        <td className={`decisions-side decisions-side--${record.side.toLowerCase()}`}>{record.side}</td>
                        <td className="decisions-grade">
                          <strong>{record.grade}</strong> <span className="scan-cell--muted">{record.score}</span>
                        </td>
                        <td>{formatPrice(record.entry)}</td>
                        <td>{formatPrice(record.stop)}</td>
                        <td>{formatPrice(record.target)}</td>
                        <td>
                          <StatusChip status={record.outcome} />
                        </td>
                        <td>{formatPrice(record.fill)}</td>
                        <td>
                          <ResultCell record={record} />
                        </td>
                        <td className="track-record__notecell">{record.outcome_note ?? DASH}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={ledger.page}
                pageCount={ledger.pageCount}
                total={ledger.total}
                pageSize={ledger.pageSize}
                onPage={ledger.setPage}
                noun="recorded opportunities"
              />
            </>
          )}
        </>
      )}
    </section>
  );
}
