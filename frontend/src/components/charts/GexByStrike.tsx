/**
 * T13 — GEX by strike. The primary chart of the app: where dealer hedging concentrates,
 * per strike, with the call/put walls and the flip point marked so "where's the wall
 * relative to spot" reads in one glance. See PLAN.md §3 for what these levels mean.
 *
 * Design choices below were made per the `dataviz` skill (invoked before writing this
 * file), specifically so this chart and T14's GammaProfile read as one system:
 *
 * - Calls/puts are a **diverging** encoding (above/below the zero baseline), so they take
 *   the skill's validated diverging pair — blue for calls, red for puts — re-validated with
 *   `validate_palette.js` against *this app's* own `--bg` values (`#ffffff` / `#16171d`
 *   from src/index.css), not just the skill's reference surface: both pairs pass every
 *   check (lightness band, chroma floor, CVD ΔE >= 8, normal-vision floor >= 15, contrast)
 *   on both surfaces.
 * - The net line is deliberately **not** a third categorical hue. It's a derived total
 *   (call + put), not a fourth identity to track, so it takes this app's own heading-ink
 *   token (`--text-h`) — solid, 2px, high contrast against both bar colors — the same
 *   "total drawn in ink over colored parts" convention as a stacked-bar total line.
 * - Spot and flip are both reference *lines*, not data, so they're dashed (dataviz flags
 *   dashed gridlines/axes as noise, but a dashed threshold/reference line is the correct
 *   read here) and get their own colors so they're never confused with each other: spot
 *   takes this app's `--accent` (the color the rest of the UI already uses for "this is the
 *   live selection"), flip takes the muted secondary-ink token so it reads as a quieter,
 *   data-derived annotation rather than a live position.
 * - Call wall / put wall get both a labeled pin (markPoint) and a highlighted border on the
 *   matching bar itself, so the wall is findable either by scanning labels or by scanning
 *   the tallest/deepest bar.
 */
import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption, TooltipComponentFormatterCallbackParams } from 'echarts';
import type { StrikeGex, Underlying } from '../../api/types';
import { formatGex, formatStrike } from '../../lib/format';
import { useTheme, type Theme } from '../../theme/ThemeContext';

export interface GexByStrikeColors {
  surface: string;
  /** Secondary ink — axis labels, gridlines, the flip marker (a quieter, derived annotation). */
  text: string;
  /** Primary ink — the net-GEX line and its markers. */
  heading: string;
  border: string;
  /** This app's accent color — used only for the spot marker, so "where am I" always reads
   * as the same color the rest of the UI already uses for the live selection. */
  accent: string;
  call: string;
  put: string;
}

/**
 * Hardcoded copies of src/index.css's `--bg` / `--text` / `--text-h` / `--border` /
 * `--accent` custom properties, plus the diverging call/put pair. ECharts paints to
 * <canvas>, which cannot read CSS custom properties at draw time, so the values
 * necessarily exist twice: once for DOM chrome (the CSS vars) and once here for canvas
 * fills/strokes. There is no automated drift check — if index.css's palette changes, update
 * this too.
 */
export const THEME_COLORS: Record<Theme, GexByStrikeColors> = {
  light: {
    surface: '#ffffff',
    text: '#6b6375',
    heading: '#08060d',
    border: '#e5e4e7',
    accent: '#9935e6',
    call: '#2a78d6',
    put: '#e34948',
  },
  dark: {
    surface: '#16171d',
    text: '#9ca3af',
    heading: '#f3f4f6',
    border: '#2e303a',
    accent: '#c084fc',
    call: '#3987e5',
    put: '#e66767',
  },
};

export interface GexByStrikeProps {
  /** `data.by_strike` from `GexResult` — one row per merged strike (SPX/SPXW already
   * summed server-side per PLAN.md §3.2). */
  rows: StrikeGex[];
  spot: number;
  /** `data.levels.call_wall` — legitimately `null` whenever the filter admits no contracts
   * (the everyday post-close `ZERO_DTE` case). Renders with no call-wall marker/border,
   * never a wall at strike 0. */
  callWall: number | null;
  /** `data.levels.put_wall` — same nullability and rendering rule as `callWall` above. */
  putWall: number | null;
  /** `data.levels.flip_point` — legitimately `null` when the gamma profile has no sign
   * change in the grid (verified against the real SPX/QQQ chain; the QQQ mock fixture
   * exercises this). Renders with no flip marker, never a line at 0. */
  flipPoint: number | null;
  /** Only used for the chart's accessible label; not required to render. */
  underlying?: Underlying;
  /** Plot height in px; the range slider and x-axis band add to this, not consume it —
   * see the dataviz skill's "container excludes the axis band" anti-pattern. */
  height?: number;
}

interface BuildOptionParams {
  rows: StrikeGex[];
  spot: number;
  callWall: number | null;
  putWall: number | null;
  flipPoint: number | null;
  colors: GexByStrikeColors;
}

const BAR_WIDTH_PX = 8;

/**
 * Pure option builder, exported so tests can assert on the produced `EChartsOption`
 * directly (dataZoom bounds, marker presence/absence, tooltip formatting) without needing
 * a real <canvas> — jsdom has none, so a rendered-chart test would test nothing.
 *
 * Axis choice: **value axis for strikes, not category.** SPX spaces strikes 5 points near
 * the money and 25-50 points in the wings; a category axis draws every strike the same
 * width regardless of its actual distance from its neighbors, which would visually
 * compress the liquid, tightly-spaced region right around spot — exactly the region this
 * chart exists to make readable — and stretch the sparse wings. A value axis (with bar
 * series using a fixed pixel `barWidth` rather than an auto-computed category band) places
 * every bar at its true numeric position, so gaps in the chain read as gaps, and the
 * ±5%-of-spot zoom window below is a real distance in strike-points, not an arbitrary
 * number of categories.
 */
export function buildGexByStrikeOption({
  rows,
  spot,
  callWall,
  putWall,
  flipPoint,
  colors,
}: BuildOptionParams): EChartsOption {
  const sorted = [...rows].sort((a, b) => a.strike - b.strike);
  const strikes = sorted.map((r) => r.strike);
  const domainMin = strikes.length ? Math.min(...strikes) : spot - 1;
  const domainMax = strikes.length ? Math.max(...strikes) : spot + 1;

  // ±5% of spot, clamped into the strikes actually on the chain. Spot sitting near (or
  // past) either edge of a short/thin chain — the QQQ mock fixture's grid ends well within
  // 5% of spot on one side — must not hand echarts a start/end outside the data; clamping
  // here (rather than trusting echarts to do it) keeps the default-zoom contract exact and
  // testable, and degrades to the full domain if the whole chain is narrower than ±5%.
  const zoomStart = Math.min(Math.max(spot * 0.95, domainMin), domainMax);
  const zoomEnd = Math.max(Math.min(spot * 1.05, domainMax), domainMin);

  const callWallRow = sorted.find((r) => r.strike === callWall);
  const putWallRow = sorted.find((r) => r.strike === putWall);

  // Highlighting: a 2px heading-ink border on the wall's own bar (findable by scanning the
  // tallest/deepest bar) plus a labeled pin below (findable by scanning labels) — belt and
  // suspenders, since which one a reader notices first depends on zoom level.
  const wallBorder = { borderColor: colors.heading, borderWidth: 2 };
  const callData = sorted.map((r) => ({
    value: [r.strike, r.call_gex] as [number, number],
    itemStyle: r.strike === callWall ? wallBorder : undefined,
  }));
  const putData = sorted.map((r) => ({
    value: [r.strike, r.put_gex] as [number, number],
    itemStyle: r.strike === putWall ? wallBorder : undefined,
  }));
  const netData = sorted.map((r) => [r.strike, r.net_gex]);

  // Spot is always known and always drawn. Flip is legitimately absent (no sign change in
  // the grid) — per PLAN.md/T11's contract this must render cleanly with no marker and
  // never fall back to a line at 0, so it's simply omitted from markLine.data rather than
  // coerced to a value.
  interface MarkLineDatum {
    xAxis: number;
    label: { formatter: string; color: string; fontWeight?: 'bold' };
    lineStyle: { color: string; type: 'dashed'; width: number };
  }
  const markLineData: MarkLineDatum[] = [
    {
      xAxis: spot,
      label: { formatter: 'Spot', color: colors.accent, fontWeight: 'bold' },
      lineStyle: { color: colors.accent, type: 'dashed', width: 2 },
    },
  ];
  if (flipPoint != null) {
    markLineData.push({
      xAxis: flipPoint,
      label: { formatter: 'Flip', color: colors.text },
      lineStyle: { color: colors.text, type: 'dashed', width: 2 },
    });
  }

  // T36: the label used to be rendered *inside* the pin (echarts markPoint default), which
  // clips to the symbol's own bounding shape -- "Call wall" / "Put wall" are both wider than
  // a 34px pin at any legible font size, so only the horizontally-centered slice of each
  // string ("ll w" / "t w") ever painted. Moving the label above the pin (`position: 'top'`)
  // lets it lay out at full width against the chart background instead of being clipped to
  // the marker glyph; it's colored to match the pin/bar it belongs to rather than the old
  // white-on-fill, since it's no longer painted over that fill.
  const wallMarkPoint = (name: string, wallStrike: number | null, wallValue: number | undefined, color: string) =>
    wallValue === undefined
      ? undefined
      : {
          symbol: 'pin' as const,
          symbolSize: 34,
          itemStyle: { color },
          label: {
            formatter: name,
            color,
            fontSize: 11,
            fontWeight: 'bold' as const,
            position: 'top' as const,
            distance: 4,
            // A wall whose own GEX bar isn't the chart's tallest (typically the put wall,
            // whose bar sits inside the same near-zero cluster as everything else) lands its
            // "top" label in the middle of other bars rather than in clear space above them
            // -- a surface-colored chip keeps the text legible regardless of what's directly
            // behind it, rather than depending on the data happening to leave room.
            backgroundColor: colors.surface,
            padding: [1, 4] as [number, number],
            borderRadius: 3,
          },
          data: [{ name, coord: [wallStrike, wallValue] as [number, number] }],
        };

  return {
    backgroundColor: 'transparent',
    grid: { left: 64, right: 24, top: 56, bottom: 96, containLabel: true },
    legend: {
      data: ['Call GEX', 'Put GEX', 'Net GEX'],
      top: 4,
      icon: 'roundRect',
      textStyle: { color: colors.text },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: colors.surface,
      borderColor: colors.border,
      textStyle: { color: colors.heading },
      // Values (formatGex) lead visually via <strong>; the series name follows — the
      // interaction spec's "values lead, labels follow" tooltip hierarchy.
      formatter: (paramsRaw: TooltipComponentFormatterCallbackParams) => {
        const params = Array.isArray(paramsRaw) ? paramsRaw : [paramsRaw];
        if (params.length === 0) return '';
        const first = params[0] as { value?: unknown };
        const strike = Array.isArray(first.value) ? Number(first.value[0]) : NaN;
        const byName = new Map<string, number>();
        for (const p of params as { seriesName?: string; value?: unknown }[]) {
          if (p.seriesName && Array.isArray(p.value)) byName.set(p.seriesName, Number(p.value[1]));
        }
        const rows_ = [
          ['Call', byName.get('Call GEX')],
          ['Put', byName.get('Put GEX')],
          ['Net', byName.get('Net GEX')],
        ] as const;
        const body = rows_
          .map(([label, value]) => `${label}: <strong>${formatGex(value ?? null)}</strong>`)
          .join('<br/>');
        return `Strike ${formatStrike(strike)}<br/>${body}`;
      },
    },
    xAxis: {
      type: 'value',
      min: domainMin,
      max: domainMax,
      name: 'Strike',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: colors.text },
      axisLabel: { color: colors.text, formatter: (v: number) => formatStrike(v) },
      axisLine: { lineStyle: { color: colors.border } },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: 'GEX / 1% move',
      nameTextStyle: { color: colors.text },
      axisLabel: { color: colors.text, formatter: (v: number) => formatGex(v, 0) },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: colors.border } },
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0, startValue: zoomStart, endValue: zoomEnd },
      {
        type: 'slider',
        xAxisIndex: 0,
        startValue: zoomStart,
        endValue: zoomEnd,
        bottom: 8,
        height: 22,
        borderColor: colors.border,
        fillerColor: colors.accent + '26', // ~15% alpha wash, matches the "area fill ~10%" spec closely enough for a slider filler
        handleStyle: { color: colors.surface, borderColor: colors.accent },
        textStyle: { color: colors.text },
        dataBackground: {
          lineStyle: { color: colors.border },
          areaStyle: { color: colors.border },
        },
      },
    ],
    series: [
      {
        name: 'Call GEX',
        type: 'bar',
        data: callData,
        barWidth: BAR_WIDTH_PX,
        barGap: '-100%',
        z: 2,
        itemStyle: { color: colors.call, borderRadius: [4, 4, 0, 0] },
        markPoint: wallMarkPoint('Call wall', callWall, callWallRow?.call_gex, colors.call),
      },
      {
        name: 'Put GEX',
        type: 'bar',
        data: putData,
        barWidth: BAR_WIDTH_PX,
        z: 2,
        itemStyle: { color: colors.put, borderRadius: [0, 0, 4, 4] },
        markPoint: wallMarkPoint('Put wall', putWall, putWallRow?.put_gex, colors.put),
      },
      {
        name: 'Net GEX',
        type: 'line',
        data: netData,
        showSymbol: false,
        z: 3,
        lineStyle: { width: 2, color: colors.heading },
        itemStyle: { color: colors.heading },
        markLine: {
          symbol: 'none',
          data: markLineData,
          animation: false,
        },
      },
    ],
  };
}

export function GexByStrike({ rows, spot, callWall, putWall, flipPoint, underlying, height = 460 }: GexByStrikeProps) {
  const { theme } = useTheme();
  const colors = THEME_COLORS[theme];

  // Rebuilt whenever data or theme changes; `notMerge` below always replaces the option
  // wholesale rather than diffing against the previous one, so a theme flip can't leave a
  // stale marker/color from the other palette behind.
  const option = useMemo(
    () => buildGexByStrikeOption({ rows, spot, callWall, putWall, flipPoint, colors }),
    [rows, spot, callWall, putWall, flipPoint, colors],
  );

  return (
    <div style={{ width: '100%' }} role="img" aria-label={`GEX by strike${underlying ? ` for ${underlying}` : ''}`}>
      <ReactECharts option={option} notMerge lazyUpdate={false} style={{ width: '100%', height }} opts={{ renderer: 'canvas' }} />
    </div>
  );
}
