/**
 * T64 — MetricCard/MetricStrip: a single current-state metric, and a row of them. Per the
 * plan ("Use metric cards for current state only, not every datum"), this is deliberately
 * not a general key/value dump — a page picks a handful of numbers that answer "what is true
 * right now," the same discipline `RegimeStrip` already applies by hand for its tape tiles.
 *
 * A `status` tone never carries meaning on colour alone: `statusLabel` is required whenever
 * `status` is set and renders as visible text next to the colour dot — the same border/dot
 * -never-text rule `StatusChip` (components/scan/StatusChip.tsx) and `vizPalette.ts`'s
 * `levelResistance`/`levelSupport` docstring already establish for this app's colour pairs.
 *
 * **T69 — `density="compact"` (additive).** A one-line variant for a status summary that reads
 * as a sentence rather than a stacked card (Overview's capture-freshness line: "5/5 chains
 * fresh · latest capture 4:19 PM ET"). Omitting `density` (every call site before this task —
 * Decisions/Dashboard/Regime/Rotation/Flows/Report) renders exactly the same card as before;
 * this prop only ever adds a class and hides the label visually (still present for assistive
 * tech via the existing `.sr-only` utility, `index.css`), it never removes a node or changes
 * default behaviour.
 */
import type { ReactNode } from 'react';
import { Surface } from './Surface';

export type MetricTone = 'positive' | 'negative' | 'caution' | 'info' | 'neutral';

export interface MetricStatus {
  tone: MetricTone;
  /** Visible text carrying the same signal as `tone`'s colour — never omit this. */
  label: string;
}

export interface MetricCardProps {
  label: string;
  value: ReactNode;
  /** A short secondary line under the value — units, a comparison, a caveat. */
  hint?: ReactNode;
  status?: MetricStatus;
  /** Additive display variant — omit for the existing full-card layout. `"compact"` lays the
   * value/status/hint out on one row and visually hides `label` (kept for screen readers via
   * `.sr-only`), for a status line rather than a stat card. */
  density?: 'compact';
}

export function MetricCard({ label, value, hint, status, density }: MetricCardProps) {
  const compact = density === 'compact';
  return (
    <Surface className={compact ? 'metric-card metric-card--compact' : 'metric-card'} level="raised">
      <span className={compact ? 'metric-card__label sr-only' : 'metric-card__label'}>{label}</span>
      <span className="metric-card__value">{value}</span>
      {status && (
        <span className={`metric-card__status metric-card__status--${status.tone}`}>
          <span aria-hidden="true" className="metric-card__status-dot" />
          {status.label}
        </span>
      )}
      {hint != null && <span className="metric-card__hint">{hint}</span>}
    </Surface>
  );
}

export interface MetricStripItem extends MetricCardProps {
  /** React key for this metric within the strip. */
  metricKey: string;
}

export interface MetricStripProps {
  /** Accessible name for the row, e.g. "Opportunities at a glance". */
  label: string;
  metrics: MetricStripItem[];
  /** T69: an additive extra class for a page-specific layout tweak (Dashboard's narrow-width
   * 2-column grid) — optional; every existing caller that omits it renders the exact same
   * wrapping `<div className="metric-strip">` as before. */
  className?: string;
}

export function MetricStrip({ label, metrics, className }: MetricStripProps) {
  return (
    <div className={className ? `metric-strip ${className}` : 'metric-strip'} role="group" aria-label={label}>
      {metrics.map(({ metricKey, ...metric }) => (
        <MetricCard key={metricKey} {...metric} />
      ))}
    </div>
  );
}
