/**
 * One trial, expanded: its parameters and both sides of the IS/OOS split (T78).
 *
 * The in-sample numbers appear **here and not on the leaderboard**, deliberately. IS
 * performance is what the search fitted, so sitting next to a ranking it reads as corroborating
 * evidence when it is nothing of the kind. On a detail page with OOS beside it, the *gap*
 * between the two is the informative part — a Sharpe of 7.4 in-sample and 1.1 out-of-sample is
 * a story about overfitting, and you can only see it when both are on screen.
 */
import { DetailDrawer } from '../../../components/ui/DetailDrawer';
import { InfoTip } from '../../../components/ui/InfoTip';
import { formatPct } from '../../../lib/format';
import { useTrial } from '../api/queries';

function num(value: number | null, digits = 2): string {
  return value == null ? '—' : value.toFixed(digits);
}

export function TrialDrawer({ hash, onClose }: { hash: string | null; onClose: () => void }) {
  const trial = useTrial(hash);

  return (
    <DetailDrawer open={hash != null} onClose={onClose} title="Trial detail">
      {trial.isPending && <p>Loading…</p>}
      {trial.isError && <p role="alert">Could not load this trial.</p>}
      {trial.data && (
        <div className="trial-detail">
          <p className="trial-detail__identity">
            <strong>{trial.data.strategy}</strong> on {trial.data.symbol} ({trial.data.market},{' '}
            {trial.data.timeframe}) — first tested {trial.data.run_date}
          </p>

          <h3>Parameters</h3>
          <dl className="trial-detail__params">
            {Object.entries(trial.data.params).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{String(value)}</dd>
              </div>
            ))}
          </dl>

          <h3>
            In-sample vs out-of-sample
            <InfoTip label="the in-sample and out-of-sample split">
              The in-sample figures are what the search fitted. A large gap between the two
              columns is the signature of a parameter set that was tuned to its own history.
            </InfoTip>
          </h3>
          <table className="trial-detail__split">
            <thead>
              <tr>
                <th scope="col" />
                <th scope="col" className="is-numeric">
                  In-sample
                </th>
                <th scope="col" className="is-numeric">
                  Out-of-sample
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">Sharpe</th>
                <td className="is-numeric">{num(trial.data.is_sharpe)}</td>
                <td className="is-numeric">{num(trial.data.oos_sharpe)}</td>
              </tr>
              <tr>
                <th scope="row">CAGR</th>
                <td className="is-numeric">{formatPct(trial.data.is_cagr)}</td>
                <td className="is-numeric">{formatPct(trial.data.oos_cagr)}</td>
              </tr>
              <tr>
                <th scope="row">Max drawdown</th>
                <td className="is-numeric">{formatPct(trial.data.is_max_dd)}</td>
                <td className="is-numeric">{formatPct(trial.data.oos_max_dd)}</td>
              </tr>
              <tr>
                <th scope="row">Fills</th>
                <td className="is-numeric">{trial.data.is_fills ?? '—'}</td>
                <td className="is-numeric">{trial.data.oos_fills ?? '—'}</td>
              </tr>
              <tr>
                <th scope="row">Exposure</th>
                <td className="is-numeric">—</td>
                <td className="is-numeric">{formatPct(trial.data.oos_exposure)}</td>
              </tr>
              <tr>
                <th scope="row">Span</th>
                <td className="is-numeric">—</td>
                <td className="is-numeric">
                  {num(trial.data.oos_years, 1)} yrs / {trial.data.oos_bars ?? '—'} bars
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </DetailDrawer>
  );
}
