/**
 * T53 -- a compact per-fund flow trend for the table beneath `FlowBars` (07-ui.md: "Per-fund
 * Sparkline of ... flow in a compact table beneath").
 *
 * **Why this isn't `components/scan/Sparkline` and isn't a daily series.** The only backend
 * surface this task may read is `GET /api/gex/scan/flows?window=` (frontend-only task; see
 * `Flows.tsx`'s own docstring) -- there is no endpoint returning a *daily* flow series, only
 * one aggregate `flow_pct` per requested window. 07-ui.md's own text describes "60-day
 * cumulative flow", which would need a daily history endpoint that does not exist; building
 * one is backend work, out of scope here. What genuinely exists, and what this component
 * plots instead, is the three real window aggregates (`5d`/`20d`/`60d` flow-percent) `Flows.tsx`
 * already fetches for the toolbar. This is a real, if coarser, three-point trend -- never a
 * fabricated daily curve. Flagged in the T53 report as a deliberate deviation from the plan
 * text, not an oversight.
 *
 * A `null` point (no computed flow at that window) is **omitted from the plotted bars
 * entirely**, not drawn as a zero-height bar -- the same "a missing value and a zero are
 * different facts" rule `Sparkline.tsx` and `PercentileBar.tsx` already apply. A fund with
 * *no* defined point at all (every window still null) renders the shared em-dash empty state
 * instead of an empty chart canvas.
 */
import ReactECharts from 'echarts-for-react';
import { useTheme } from '../../../../theme/ThemeContext';
import { vizPaletteFor } from '../../../../theme/vizPalette';

const DASH = '—';

export interface FlowSparklinePoint {
  /** e.g. `'5d'`, `'20d'`, `'60d'`. */
  label: string;
  /** Fraction of AUM, or `null` when this window has no computed flow yet. */
  value: number | null;
}

export interface FlowSparklineProps {
  points: FlowSparklinePoint[];
  height?: number;
  /** Accessible label -- ECharts renders to a `<canvas>`, which carries no text content of
   * its own. */
  ariaLabel: string;
}

export function FlowSparkline({ points, height = 28, ariaLabel }: FlowSparklineProps) {
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

  const known = points.filter((p) => p.value != null && Number.isFinite(p.value));

  if (known.length === 0) {
    return (
      <span role="img" aria-label={`${ariaLabel}: no data`} className="flow-sparkline flow-sparkline--empty">
        {DASH}
      </span>
    );
  }

  const option = {
    animation: false,
    grid: { left: 2, right: 2, top: 4, bottom: 4 },
    xAxis: { type: 'category', show: false, data: known.map((p) => p.label) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [
      {
        type: 'bar',
        barWidth: '55%',
        data: known.map((p) => ({
          value: p.value,
          itemStyle: {
            color: (p.value as number) >= 0 ? palette.divergingPositive : palette.divergingNegative,
          },
        })),
      },
    ],
  };

  return (
    <span role="img" aria-label={ariaLabel} className="flow-sparkline">
      <ReactECharts option={option} style={{ height, width: '100%' }} notMerge />
    </span>
  );
}
