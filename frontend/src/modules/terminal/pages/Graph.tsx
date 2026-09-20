/**
 * `/terminal/graph` — the transmission graph (T80).
 *
 * **Sign conflicts are pulled to the top and stated in words**, because spec 4 names
 * `sign_conflict` and `corr_percentile` the two highest-value outputs here and a plain
 * correlation table buries both. A conflict means the empirical relationship currently runs
 * opposite to the direction theory predicts — the market telling you the usual channel is not
 * operating, which is exactly when you want to be told.
 *
 * `expected_sign === 0` renders as "regime-dependent", never as "unknown" or a blank. It is a
 * deliberate statement from the edge definition: the sign genuinely flips with the regime, and
 * asserting one would mislead precisely when it matters.
 */
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { InfoTip } from '../../../components/ui/InfoTip';
import { PageHeader } from '../../../components/ui/PageHeader';
import { EmptyState } from '../../gex/components/EmptyState';
import { ErrorState } from '../../gex/components/ErrorState';
import { useEdges } from '../api/queries';
import { useAsOf } from '../state/asOf';

function sign(expected: number): string {
  if (expected > 0) return '+1';
  if (expected < 0) return '−1';
  return 'regime-dependent';
}

function num(value: number | null, digits = 2): string {
  return value == null ? '—' : value.toFixed(digits);
}

export function Graph() {
  const { asOf } = useAsOf();
  const edges = useEdges(asOf);

  const estimated = (edges.data?.edges ?? []).filter((e) => e.beta != null);
  const conflicts = estimated.filter((e) => e.sign_conflict);

  return (
    <div className="terminal-page">
      <PageHeader
        title="Transmission graph"
        description="Which series move which, what theory expects, and what the data currently says."
        caveat={
          <p>
            Every beta and correlation is a trailing-window estimate, and the window is shown{' '}
            <span className="no-break">
              beside it.
              <InfoTip label="trailing-window estimates">
                A fixed-window correlation is an average over regimes, not a fact about today.
                The percentile says where the current correlation sits in its own history, which
                is the only way to know whether 0.4 is high or low for this pair.
              </InfoTip>
            </span>
          </p>
        }
      />

      {edges.isError ? (
        <ErrorState message="Could not load the transmission graph." />
      ) : edges.isPending ? (
        <p className="terminal-loading">Loading edges…</p>
      ) : (
        <>
          <div className={`conflict-banner${conflicts.length ? ' conflict-banner--active' : ''}`}>
            {conflicts.length === 0 ? (
              <p>
                No sign conflicts at this as-of: every estimated edge currently moves in the
                direction theory expects.
              </p>
            ) : (
              <>
                <p>
                  <strong>
                    {conflicts.length} sign conflict{conflicts.length === 1 ? '' : 's'}.
                  </strong>{' '}
                  These relationships currently run opposite to what theory expects — the usual
                  channel is not operating.
                </p>
                <ul>
                  {conflicts.map((e) => (
                    <li key={`${e.from_series}->${e.to_series}`}>
                      <code>
                        {e.from_series} → {e.to_series}
                      </code>{' '}
                      expects {sign(e.expected_sign)}, measures β {num(e.beta)} (corr{' '}
                      {num(e.corr)})
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <DataTableFrame
            title="Edges"
            readingCue="Theory's expected sign beside the measured relationship. An edge with no estimate is listed below rather than hidden."
            sourceTiming={`as of ${new Date(edges.data.as_of).toLocaleString()}`}
          >
            {estimated.length === 0 ? (
              <EmptyState heading="No edges estimated at this as-of">
                The nightly graph step had not run by this moment. The definitions exist; their
                empirical half does not yet.
              </EmptyState>
            ) : (
              <div className="terminal-table__scroll">
                <table className="terminal-table">
                  <thead>
                    <tr>
                      <th scope="col">From</th>
                      <th scope="col">To</th>
                      <th scope="col">Expects</th>
                      <th scope="col" className="is-numeric">
                        β
                      </th>
                      <th scope="col" className="is-numeric">
                        t
                      </th>
                      <th scope="col" className="is-numeric">
                        R²
                      </th>
                      <th scope="col" className="is-numeric">
                        corr
                      </th>
                      <th scope="col" className="is-numeric">
                        corr %ile
                      </th>
                      <th scope="col" className="is-numeric">
                        window
                      </th>
                      <th scope="col">Flags</th>
                    </tr>
                  </thead>
                  <tbody>
                    {estimated.map((e) => (
                      <tr
                        key={`${e.from_series}->${e.to_series}`}
                        className={e.sign_conflict ? 'terminal-table__row--conflict' : undefined}
                      >
                        <td className="terminal-table__id">{e.from_series}</td>
                        <td className="terminal-table__id">{e.to_series}</td>
                        <td>{sign(e.expected_sign)}</td>
                        <td className="is-numeric">{num(e.beta, 3)}</td>
                        <td className="is-numeric">{num(e.beta_t_stat)}</td>
                        <td className="is-numeric">{num(e.r_squared, 3)}</td>
                        <td className="is-numeric">{num(e.corr)}</td>
                        <td className="is-numeric">{num(e.corr_percentile, 1)}</td>
                        <td className="is-numeric">{e.beta_window ?? '—'}</td>
                        <td>
                          {e.sign_conflict && <span className="flag flag--conflict">conflict</span>}
                          {e.significant ? (
                            <span className="flag flag--sig">significant</span>
                          ) : (
                            <span className="flag flag--insig">not significant</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </DataTableFrame>

          {edges.data.unestimated.length > 0 && (
            <DataTableFrame
              title="Defined but not estimated"
              readingCue="These edges exist in theory but have no estimate at this as-of. Listed rather than dropped: a missing edge and an unmeasured one look identical on a graph otherwise."
            >
              <ul className="terminal-unscored">
                {edges.data.unestimated.map((e) => (
                  <li key={e}>
                    <span className="terminal-table__id">{e}</span>
                  </li>
                ))}
              </ul>
            </DataTableFrame>
          )}
        </>
      )}
    </div>
  );
}
