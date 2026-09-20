/**
 * `/terminal/regime` — the regime reading and the evidence for it (T80).
 *
 * **A state is never shown without its three z-scores.** `risk_off` with nothing behind it is an
 * opinion you cannot check; `risk_off` with real yields +1.8, credit +2.1 and the dollar +1.4 is
 * a claim you can argue with. The classifier's own rules are on the page for the same reason —
 * so the label can be audited rather than trusted.
 *
 * `quiet` is a **real answer**, not a failure to classify: it means no leg moved enough to call a
 * regime, and saying "quiet" is more honest than putting a confident label on noise.
 */
import { InfoTip } from '../../../components/ui/InfoTip';
import { MetricStrip } from '../../../components/ui/MetricCard';
import { PageHeader } from '../../../components/ui/PageHeader';
import { Surface } from '../../../components/ui/Surface';
import { ErrorState } from '../../gex/components/ErrorState';
import { useRegime } from '../api/queries';
import { useAsOf } from '../state/asOf';

const STATE_TEXT: Record<string, string> = {
  risk_off: 'Risk off',
  risk_on: 'Risk on',
  rates_led: 'Rates led',
  quiet: 'Quiet',
};

const RULES: Array<[string, string]> = [
  ['Quiet', 'No leg moved by the minimum z. Not a regime — the absence of one.'],
  ['Risk off', 'Credit wider AND dollar stronger — the classic pairing.'],
  ['Risk on', 'Credit tighter AND dollar weaker.'],
  [
    'Rates led',
    'Neither risk pairing holds, so the real-yield leg is what is doing the work.',
  ],
];

export function Regime() {
  const { asOf, isHistorical } = useAsOf();
  const regime = useRegime(asOf);

  return (
    <div className="terminal-page">
      <PageHeader
        title="Regime"
        description="Which of four states the last few weeks look like, and the three legs that decide it."
        caveat={
          isHistorical ? (
            <p>
              <strong>Classified as of a past moment</strong> — using only observations published
              by then.
            </p>
          ) : undefined
        }
      />

      {regime.isError ? (
        <ErrorState message="Could not load the regime reading." />
      ) : regime.isPending ? (
        <p className="terminal-loading">Classifying…</p>
      ) : (
        <>
          <Surface className={`regime-hero regime-hero--${regime.data.state}`} level="subtle" bordered>
            <div className="regime-hero__state">
              {STATE_TEXT[regime.data.state] ?? regime.data.state}
            </div>
            <p className="regime-hero__detail">{regime.data.detail}</p>
            <p className="regime-hero__meta">
              {regime.data.window_days}-day window
              {regime.data.value_date && ` · through ${regime.data.value_date}`}
            </p>
          </Surface>

          <MetricStrip
            label="Evidence"
            metrics={Object.entries(regime.data.evidence).map(([leg, z]) => ({
              metricKey: leg,
              label: leg,
              value: z == null ? '—' : `${z > 0 ? '+' : ''}${z.toFixed(2)}`,
              hint: 'Standard deviations over the window, scored against this leg’s own history.',
              status:
                z == null
                  ? undefined
                  : {
                      tone: Math.abs(z) >= 1 ? 'caution' : 'neutral',
                      label: Math.abs(z) >= 1 ? 'moved' : 'quiet',
                    },
            }))}
          />

          <Surface className="regime-rules" level="app" bordered>
            <h2>
              How this is decided
              <InfoTip label="how the regime label is decided">
                The rules are applied in order, and the first that matches wins. They are here
                so the label can be audited rather than believed.
              </InfoTip>
            </h2>
            <dl>
              {RULES.map(([name, rule]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{rule}</dd>
                </div>
              ))}
            </dl>
          </Surface>
        </>
      )}
    </div>
  );
}
