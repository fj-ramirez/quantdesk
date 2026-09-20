/**
 * The research module's status strip (T78): how much has been tried, and when.
 *
 * `total_trials` is given the most prominent slot on purpose. It is not vanity telemetry — it
 * is the denominator of the noise ceiling, and the reason losing trials are never deleted from
 * the registry. A reader who understands why 134,377 is displayed here understands the whole
 * module.
 */
import { MetricStrip } from '../../../components/ui/MetricCard';
import type { ResearchStatus } from '../api/types';

export function StatusStrip({ status }: { status: ResearchStatus | undefined }) {
  return (
    <MetricStrip
      label="Registry"
      metrics={[
        {
          metricKey: 'trials',
          label: 'Trials searched',
          value: status ? status.total_trials.toLocaleString() : '—',
          hint: 'Every combination ever tested, losers included — the noise ceiling’s denominator.',
        },
        {
          metricKey: 'cycles',
          label: 'Cycles run',
          value: status ? status.cycles.toLocaleString() : '—',
          hint: 'Days on which a search cycle recorded work.',
        },
        {
          metricKey: 'last-cycle',
          label: 'Last cycle',
          value: status?.last_run_date ?? '—',
          hint: status ? `${status.trials_last_cycle.toLocaleString()} trials that day` : undefined,
        },
        {
          metricKey: 'paper',
          label: 'Paper candidates',
          value: status ? status.paper_candidates.toLocaleString() : '—',
          hint: 'Promoted to forward tracking — the only un-fitted evidence here.',
        },
      ]}
    />
  );
}
