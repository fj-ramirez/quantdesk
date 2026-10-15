/**
 * The conditions reading (T172, plans/sentiment): dealer gamma and the sentiment composite, and
 * the fixed synthesis of the two. The macro leg is the Regime page's own hero, above this.
 *
 * **Every number carries its status.** A component the desk cannot read at this `as_of` is shown
 * as missing with its reason, never as a 50 — an invented neutral would pull the eye toward calm.
 * The composite is a description, not a signal; the panel says so in place.
 */
import { InfoTip } from '../../../components/ui/InfoTip';
import { Surface } from '../../../components/ui/Surface';
import { formatGex } from '../../../lib/format';
import { ErrorState } from '../../gex/components/ErrorState';
import { useConditions } from '../api/queries';
import type { GammaReading, SentimentComponent, SentimentResponse } from '../api/types';
import { BAND_TEXT, SYNTHESIS_TEXT } from './conditionsText';

function GammaCard({ g }: { g: GammaReading }) {
  return (
    <div className={`conditions-gamma conditions-gamma--${g.sign ?? 'none'}`}>
      <div className="conditions-gamma__head">
        <strong>{g.underlying}</strong>
        <span>{g.status === 'ok' ? `${g.sign} gamma` : g.status.replace('_', ' ')}</span>
      </div>
      {g.status === 'ok' ? (
        <p className="conditions-gamma__body">
          {formatGex(g.net_gex)} net
          {g.flip_dist_pct != null &&
            ` · spot ${Math.abs(g.flip_dist_pct).toFixed(2)}% ${g.flip_dist_pct >= 0 ? 'above' : 'below'} flip`}
          {g.value_date && <span className="conditions-muted"> · {g.value_date}</span>}
        </p>
      ) : (
        <p className="conditions-gamma__body conditions-muted">{g.detail}</p>
      )}
    </div>
  );
}

function ComponentRow({ c }: { c: SentimentComponent }) {
  const ok = c.status === 'ok' && c.score != null;
  return (
    <tr className={ok ? undefined : 'conditions-row--missing'}>
      <th scope="row">
        {c.label}
        <span className="conditions-muted"> · {c.transform}</span>
      </th>
      <td>{ok ? c.value?.toFixed(Math.abs(c.value ?? 0) < 10 ? 3 : 1) : '·'}</td>
      <td>{ok && c.percentile != null ? c.percentile.toFixed(0) : '·'}</td>
      <td>
        {ok ? (
          <span className="conditions-bar" aria-label={`score ${c.score!.toFixed(0)}`}>
            <span className="conditions-bar__fill" style={{ width: `${c.score}%` }} />
          </span>
        ) : (
          <span className="conditions-muted">{c.status.replace('_', ' ')}</span>
        )}
      </td>
      <td className="conditions-muted">{ok ? c.value_date : c.detail}</td>
    </tr>
  );
}

function Sentiment({ s }: { s: SentimentResponse }) {
  return (
    <Surface className="conditions-sentiment" level="app" bordered>
      <h2>
        Sentiment
        <InfoTip label="how the sentiment score is built">
          Each component is ranked within its own trailing three years and oriented so 100 means
          greed. The score is their plain mean — equal weights, nothing fitted to returns. Fewer
          than {s.min_components} components present and no score is given. A description of
          conditions, not a signal.
        </InfoTip>
      </h2>
      {s.score != null && s.band ? (
        <div className={`conditions-score conditions-score--${s.band}`}>
          <span className="conditions-score__value">{s.score.toFixed(0)}</span>
          <span className="conditions-score__band">{BAND_TEXT[s.band] ?? s.band}</span>
          <span className="conditions-muted">
            {s.used} of {s.components.length} components
          </span>
        </div>
      ) : (
        <p className="conditions-muted">
          Insufficient: {s.used} components at this as-of, {s.min_components} needed.
        </p>
      )}
      {s.score != null && (
        <div className="conditions-scale" aria-hidden="true">
          <span className="conditions-scale__marker" style={{ left: `${s.score}%` }} />
        </div>
      )}
      <table className="conditions-table">
        <thead>
          <tr>
            <th scope="col">Component</th>
            <th scope="col">Value</th>
            <th scope="col">%ile</th>
            <th scope="col">Score (100 = greed)</th>
            <th scope="col">As of</th>
          </tr>
        </thead>
        <tbody>
          {s.components.map((c) => (
            <ComponentRow key={c.key} c={c} />
          ))}
        </tbody>
      </table>
      {Object.entries(s.display).map(([sid, v]) => (
        <p key={sid} className="conditions-muted">
          {sid}: {v == null ? '·' : v.toFixed(1)} — shown, not scored: its direction depends on
          the rest of the surface.
        </p>
      ))}
    </Surface>
  );
}

export function ConditionsPanel({ asOf }: { asOf: string | undefined }) {
  const conditions = useConditions(asOf);

  if (conditions.isError) return <ErrorState message="Could not load the conditions reading." />;
  if (conditions.isPending) return <p className="terminal-loading">Reading conditions…</p>;

  const c = conditions.data;
  return (
    <section className="conditions" aria-label="Conditions">
      <Surface
        className={`conditions-synthesis conditions-synthesis--${c.synthesis ?? 'none'}`}
        level="subtle"
        bordered
      >
        <div className="conditions-synthesis__label">
          {c.synthesis ? (SYNTHESIS_TEXT[c.synthesis] ?? c.synthesis) : 'No synthesis'}
        </div>
        <p>{c.synthesis_detail}</p>
        <p className="conditions-muted">
          SPX dealer gamma × sentiment side, from a fixed table — a description, not a forecast.
        </p>
      </Surface>
      <div className="conditions-gammas">
        {c.gamma.map((g) => (
          <GammaCard key={g.underlying} g={g} />
        ))}
      </div>
      <Sentiment s={c.sentiment} />
    </section>
  );
}
