/**
 * `/terminal/policy` — the implied policy path (T80).
 *
 * The market's expected fed funds rate at each upcoming FOMC meeting, read from the stored
 * `policy.ff.meeting_*` series rather than re-solved per request, so the screen and the brief
 * cannot disagree.
 *
 * **An empty path says why it is empty.** At a historical `as_of` the futures curve it is solved
 * from may not have been ingested yet, and a page that responded by drawing a flat line at zero
 * would be inventing a market that expected nothing. The API sends a `note` for exactly this and
 * the page shows it.
 */
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { PageHeader } from '../../../components/ui/PageHeader';
import { EmptyState } from '../../gex/components/EmptyState';
import { ErrorState } from '../../gex/components/ErrorState';
import { usePolicy } from '../api/queries';
import { useAsOf } from '../state/asOf';

export function Policy() {
  const { asOf } = useAsOf();
  const policy = usePolicy(asOf);

  const steps = policy.data?.steps ?? [];
  const maxAbsChange = Math.max(1, ...steps.map((s) => Math.abs(s.change_bp ?? 0)));

  return (
    <div className="terminal-page">
      <PageHeader
        title="Policy path"
        description="What the fed funds futures curve implies for each upcoming FOMC meeting."
        caveat={
          <p>
            Implied rates are derived from settlement prices, which are themselves point-in-time
            observations — this path is what was implied at the selected as-of, not what was
            later shown to have been right.
          </p>
        }
      />

      {policy.isError ? (
        <ErrorState message="Could not load the policy path." />
      ) : policy.isPending ? (
        <p className="terminal-loading">Loading path…</p>
      ) : steps.length === 0 ? (
        <EmptyState heading="No implied path at this as-of">
          {policy.data?.note ??
            'The futures curve this path is solved from is not available at this moment.'}
        </EmptyState>
      ) : (
        <DataTableFrame
          title="Implied path"
          readingCue="One row per meeting. The bar shows the expected change from the prevailing rate; its width is scaled to the largest move on the path."
          sourceTiming={`as of ${new Date(policy.data.as_of).toLocaleString()}`}
        >
          <div className="terminal-table__scroll">
            <table className="terminal-table">
              <thead>
                <tr>
                  <th scope="col">Meeting</th>
                  <th scope="col" className="is-numeric">
                    Implied rate
                  </th>
                  <th scope="col" className="is-numeric">
                    Change
                  </th>
                  <th scope="col">Expected move</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((step) => {
                  const change = step.change_bp;
                  const width = change == null ? 0 : (Math.abs(change) / maxAbsChange) * 100;
                  return (
                    <tr key={step.meeting_date}>
                      <td>{step.meeting_date}</td>
                      <td className="is-numeric">
                        {step.implied_rate == null ? '—' : `${step.implied_rate.toFixed(3)}%`}
                      </td>
                      <td className="is-numeric">
                        {change == null ? '—' : `${change > 0 ? '+' : ''}${change.toFixed(1)}bp`}
                      </td>
                      <td>
                        {/* A bar, not a sparkline: one value per row, and direction is the
                            whole message. Zero width for a null is correct here because the
                            numeric cell beside it already says "—". */}
                        <div className="policy-bar">
                          <div
                            className={`policy-bar__fill policy-bar__fill--${
                              change == null ? 'none' : change >= 0 ? 'hike' : 'cut'
                            }`}
                            style={{ width: `${width}%` }}
                          />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </DataTableFrame>
      )}
    </div>
  );
}
