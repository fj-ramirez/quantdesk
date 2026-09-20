/**
 * T53 -- `/flows`'s horizontal bar chart: net flow as percent of AUM, one bar per fund with a
 * computed value, diverging around zero, sorted (see `flowBarsOption.ts` for the reading
 * order), value labels at bar ends. Pure option-building logic lives in the sibling
 * `flowBarsOption.ts`, tested directly against the produced `EChartsOption` object
 * (`RrgChart.tsx`/`rrgOption.ts`'s house pattern); this file is the component.
 *
 * **A fund with `flow_pct: null` never reaches the chart, in any form.** Filtering happens
 * here, before `buildFlowBarsOption` is ever called -- 07-ui.md's explicit rule ("Funds with
 * no data appear in a separate 'no flow data' list under the chart, never as a zero bar")
 * plus the plan's own "never draw a symbol with no usable source at zero" for the unsupported
 * four apply identically to a *supported* fund that simply has no stored history yet. Today
 * (docs/etf-flows-sources.md's accumulation caveat -- no backfill exists) that is nearly every
 * supported symbol, so this is normally an empty chart, not an edge case: rendered as an
 * honest `EmptyState` rather than a blank canvas, exactly the same principle `Sparkline.tsx`
 * applies to an all-null series.
 */
import ReactECharts from 'echarts-for-react';
import { useTheme } from '../../../../theme/ThemeContext';
import { vizPaletteFor } from '../../../../theme/vizPalette';
import { EmptyState } from '../EmptyState';
import { buildFlowBarsOption, type FlowBarRow } from './flowBarsOption';
import type { FlowSymbol } from '../../api/types';

export interface FlowBarsProps {
  symbols: FlowSymbol[];
  /** Height per bar, px -- the chart's total height scales with how many funds have a
   * computed flow today, rather than a fixed box sized for the full universe. */
  rowHeight?: number;
}

export function FlowBars({ symbols, rowHeight = 26 }: FlowBarsProps) {
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

  const rows: FlowBarRow[] = symbols
    .filter((s) => s.flow_pct != null && Number.isFinite(s.flow_pct))
    .map((s) => ({ symbol: s.symbol, flowPct: s.flow_pct as number }));

  if (rows.length === 0) {
    return (
      <EmptyState heading="No fund has a computed flow yet">
        Every supported fund needs at least a window's worth of paired shares-outstanding
        history before a flow can be computed, and issuer files only publish one day at a
        time -- this fills in as history accumulates, it is not a broken chart.
      </EmptyState>
    );
  }

  const option = buildFlowBarsOption({ rows, palette });
  const height = Math.max(120, rows.length * rowHeight + 40);

  return (
    <div role="img" aria-label="Net ETF flow as percent of AUM, by fund">
      <ReactECharts option={option} notMerge lazyUpdate={false} style={{ width: '100%', height }} />
    </div>
  );
}
