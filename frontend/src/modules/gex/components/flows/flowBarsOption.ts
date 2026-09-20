/**
 * T53 -- the pure ECharts option builder behind `FlowBars.tsx`. Split into its own module
 * (no JSX), following `rrgOption.ts`'s house pattern for a chart tested as an option object
 * under jsdom (`GexByStrike.test.tsx`'s original pattern) rather than through a real
 * `<canvas>`: `FlowBars.tsx` exports only the component, so this file stays out of
 * `react-refresh/only-export-components` (the project's fixed count of 4 pre-existing
 * warnings must not grow).
 *
 * **Only rows with a computed `flowPct` are ever passed in here.** `FlowBars.tsx` does the
 * filtering (a fund with `flow_pct: null` -- "no data yet" or "history since <date>" -- never
 * reaches this builder) so this module never has to special-case `null` into a zero-width
 * bar; the "never draw a symbol with no usable data at zero" rule (07-ui.md's `/flows`
 * section) lives at the call site, not here, the same split `RrgChart.tsx`'s own filtering of
 * warm-up trail points uses.
 *
 * **Sort order.** Rows are sorted descending by `flowPct` (biggest net inflow first) and the
 * category axis is inverted, so the chart reads top-to-bottom as best-to-worst -- the
 * conventional reading order for a diverging horizontal bar chart. Nothing in 07-ui.md pins a
 * direction; this is a judgment call, recorded here rather than left implicit.
 *
 * **Value labels at bar ends (07-ui.md's explicit requirement).** A positive bar's label sits
 * to its right (its far end, since the bar extends rightward from zero); a negative bar's
 * label sits to its left (its far end, extending leftward). This is set per data point, not
 * once on the series, because the two signs need opposite label positions to land at the
 * bar's actual end rather than overlapping the zero line.
 */
import type { EChartsOption } from 'echarts';
import { formatSignedPct } from '../../../../lib/format';
import type { VizPalette } from '../../../../theme/vizPalette';

export interface FlowBarRow {
  symbol: string;
  /** Fraction of AUM, e.g. `0.0039` -> "+0.39%". Never `null` here -- see module docstring. */
  flowPct: number;
}

export function buildFlowBarsOption({
  rows,
  palette,
}: {
  rows: FlowBarRow[];
  palette: VizPalette;
}): EChartsOption {
  const sorted = [...rows].sort((a, b) => b.flowPct - a.flowPct);
  const categories = sorted.map((r) => r.symbol);

  return {
    animation: false,
    grid: { left: 56, right: 64, top: 12, bottom: 24, containLabel: true },
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        const p = Array.isArray(params) ? params[0] : params;
        return `${p.name}: ${formatSignedPct(p.value as number)}`;
      },
    },
    xAxis: {
      type: 'value',
      axisLabel: { formatter: (value: number) => formatSignedPct(value), color: palette.textSecondary },
      axisLine: { lineStyle: { color: palette.gridline } },
      splitLine: { lineStyle: { color: palette.gridline } },
    },
    yAxis: {
      type: 'category',
      data: categories,
      inverse: true,
      axisLabel: { color: palette.textPrimary },
      axisLine: { lineStyle: { color: palette.gridline } },
      axisTick: { show: false },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 18,
        data: sorted.map((r) => ({
          value: r.flowPct,
          itemStyle: {
            color: r.flowPct >= 0 ? palette.divergingPositive : palette.divergingNegative,
          },
          label: {
            position: r.flowPct >= 0 ? 'right' : 'left',
          },
        })),
        label: {
          show: true,
          formatter: (params) => formatSignedPct((params as { value: number }).value),
          color: palette.textPrimary,
          fontSize: 11,
        },
      },
    ],
  };
}
