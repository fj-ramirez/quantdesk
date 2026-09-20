/**
 * `/research/paper` — the forward-tracking watchlist (T78).
 *
 * **This is the honest half of the module**, and the page says so rather than assuming anyone
 * will infer it. A leaderboard row is a number found by searching 134,377 combinations; a paper
 * candidate is a commitment made at a specific timestamp, and everything that has happened to
 * it since was never fitted, never re-selected and never re-ranked.
 *
 * Ordered by promotion date, oldest first, exactly as the API returns it. Sorting this table by
 * performance would quietly convert the one un-mined record in the app into another
 * leaderboard — which is why neither the API nor this page offers that ordering at all.
 */
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { PageHeader } from '../../../components/ui/PageHeader';
import { EmptyState } from '../../gex/components/EmptyState';
import { ErrorState } from '../../gex/components/ErrorState';
import { usePaperCandidates, useResearchStatus } from '../api/queries';
import { StatusStrip } from '../components/StatusStrip';

function num(value: number | null, digits = 2): string {
  return value == null ? '—' : value.toFixed(digits);
}

export function Paper() {
  const status = useResearchStatus();
  const candidates = usePaperCandidates();

  return (
    <div className="research-page">
      <PageHeader
        title="Paper candidates"
        description="Trials promoted to forward tracking. Performance after the promotion date is the only evidence in EdgeLab that was never fitted."
        caveat={
          <p>
            A candidate is promoted only after clearing a share of its noise ceiling, staying
            profitable at <strong>doubled</strong> costs, surviving a check of its neighbouring
            parameter values, passing a walk-forward consistency gate, and not being too
            correlated with what is already on this list.
          </p>
        }
      />

      <StatusStrip status={status.data} />

      {candidates.isError ? (
        <ErrorState message="Could not load the paper candidates." />
      ) : candidates.isPending ? (
        <p className="research-loading">Loading candidates…</p>
      ) : candidates.data.length === 0 ? (
        <EmptyState heading="Nothing promoted yet">
          The search promotes a candidate only when it clears every robustness gate. An empty
          list means nothing has — which is a result, not a malfunction.
        </EmptyState>
      ) : (
        <DataTableFrame
          title="Watchlist"
          readingCue="Oldest promotion first — deliberately not ranked by performance."
          sourceTiming={`${candidates.data.length} candidate${candidates.data.length === 1 ? '' : 's'}.`}
        >
          <div className="leaderboard-table__scroll">
            <table className="leaderboard-table">
              <thead>
                <tr>
                  <th scope="col">Promoted</th>
                  <th scope="col">Market</th>
                  <th scope="col">Strategy</th>
                  <th scope="col">Symbol</th>
                  <th scope="col">TF</th>
                  <th scope="col" className="is-numeric">
                    Sharpe at promotion
                  </th>
                  <th scope="col" className="is-numeric">
                    At 2× costs
                  </th>
                  <th scope="col" className="is-numeric">
                    Neighbour median
                  </th>
                  <th scope="col" className="is-numeric">
                    Walk-forward
                  </th>
                  <th scope="col" className="is-numeric">
                    Max corr.
                  </th>
                </tr>
              </thead>
              <tbody>
                {candidates.data.map((c) => (
                  <tr key={c.hash}>
                    <td>{c.promoted_at.slice(0, 10)}</td>
                    <td>{c.market}</td>
                    <td>{c.strategy}</td>
                    <td>{c.symbol}</td>
                    <td>{c.timeframe}</td>
                    <td className="is-numeric">{num(c.promoted_oos_sharpe)}</td>
                    <td className="is-numeric">{num(c.sharpe_2x)}</td>
                    <td className="is-numeric">{num(c.neighbor_med)}</td>
                    <td className="is-numeric">
                      {c.wf_pos ?? '—'}/{c.wf_active ?? '—'}
                    </td>
                    <td className="is-numeric">{num(c.corr_max)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataTableFrame>
      )}
    </div>
  );
}
