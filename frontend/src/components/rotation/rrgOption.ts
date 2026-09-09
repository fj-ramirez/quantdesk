/**
 * T51 -- the pure ECharts option builder behind `RrgChart.tsx`. Split into its own module
 * (rather than living alongside the component, the way `GexByStrike.tsx` keeps
 * `buildGexByStrikeOption` in the same file) so `RrgChart.tsx` exports only the component --
 * this file carries no JSX and is not subject to `react-refresh/only-export-components`,
 * keeping the project's fixed count of 4 pre-existing warnings (`GammaProfile.tsx`,
 * `GexByStrike.tsx`, `ThemeContext.tsx`) from growing.
 *
 * See `RrgChart.tsx`'s module docstring for the design rationale (naming discipline, null
 * handling, dark-theme markArea alpha, colour-is-not-the-identity-channel); this file is the
 * implementation, `RrgChart.test.tsx` is the test (imports `buildRrgOption` from here
 * directly, following `GexByStrike.test.tsx`'s house pattern for testing an ECharts option
 * object without a real `<canvas>`).
 */
import type { EChartsOption } from 'echarts';
import type { RotationSymbol } from '../../api/types';
import { formatSignedPct } from '../../lib/format';
import type { VizPalette } from '../../theme/vizPalette';

interface TrailPointDatum {
  value: [number, number];
  date: string;
  itemStyle: { color: string; opacity: number };
  symbolSize: number;
}

interface DotDatum {
  value: [number, number];
  date: string;
  return20: number | null;
  symbolSize: number;
  itemStyle: { color: string; borderColor: string; borderWidth: number };
  label: {
    show: true;
    formatter: string;
    position: 'right';
    color: string;
    fontWeight: 'bold';
    fontSize: number;
  };
}

/** Cycles a symbol's trail/dot colour through four already-validated palette slots rather
 * than minting a 12-hue categorical set -- see `RrgChart.tsx`'s "Colour is not the identity
 * channel". */
const TRAIL_COLOR_KEYS: (keyof VizPalette)[] = [
  'divergingPositive',
  'divergingNegative',
  'seriesExZeroDte',
  'levelSupport',
];

function trailColorFor(palette: VizPalette, index: number): string {
  return palette[TRAIL_COLOR_KEYS[index % TRAIL_COLOR_KEYS.length]];
}

/** Same four-slot mapping `theme/vizPalette.ts`'s `statusChipColor` uses for `RankTable`'s
 * quadrant chip, so the chart's background tint and the table's chip colour never disagree
 * about what colour a quadrant is. */
function quadrantFill(name: 'leading' | 'weakening' | 'lagging' | 'improving', palette: VizPalette): string {
  const base =
    name === 'leading'
      ? palette.levelSupport
      : name === 'lagging'
        ? palette.levelResistance
        : name === 'weakening'
          ? palette.seriesExZeroDte
          : palette.divergingPositive;
  // Explicit alpha suffix on a 6-digit hex -- '29' is ~16% opacity. Never rely on echarts'
  // own default alpha for markArea; see `RrgChart.tsx`'s dark-theme warning.
  return `${base}29`;
}

export interface BuildRrgOptionParams {
  symbols: RotationSymbol[];
  palette: VizPalette;
}

export function buildRrgOption({ symbols, palette }: BuildRrgOptionParams): EChartsOption {
  const allX: number[] = [100];
  const allY: number[] = [100];
  for (const s of symbols) {
    for (const p of s.trail) {
      if (p.rs_ratio_approx != null && Number.isFinite(p.rs_ratio_approx)) allX.push(p.rs_ratio_approx);
      if (p.rs_momentum_approx != null && Number.isFinite(p.rs_momentum_approx)) allY.push(p.rs_momentum_approx);
    }
  }
  let xMin = Math.min(...allX);
  let xMax = Math.max(...allX);
  let yMin = Math.min(...allY);
  let yMax = Math.max(...allY);
  const padX = Math.max((xMax - xMin) * 0.15, 2);
  const padY = Math.max((yMax - yMin) * 0.15, 2);
  xMin -= padX;
  xMax += padX;
  yMin -= padY;
  yMax += padY;

  const legendData = symbols.map((s) => s.symbol);

  const trailAndDotSeries = symbols.flatMap((s, index) => {
    // Filter first: a null coordinate never becomes a data point at all, so it can never be
    // plotted at (0, 0) or trip up echarts -- see `RrgChart.tsx`'s module docstring.
    const validPoints = s.trail.filter(
      (p) =>
        p.rs_ratio_approx != null &&
        Number.isFinite(p.rs_ratio_approx) &&
        p.rs_momentum_approx != null &&
        Number.isFinite(p.rs_momentum_approx),
    ) as { date: string; rs_ratio_approx: number; rs_momentum_approx: number }[];

    const color = trailColorFor(palette, index);

    const trailData: TrailPointDatum[] = validPoints.map((p, i) => {
      const isLast = i === validPoints.length - 1;
      const fraction = validPoints.length > 1 ? i / (validPoints.length - 1) : 1;
      return {
        value: [p.rs_ratio_approx, p.rs_momentum_approx],
        date: p.date,
        itemStyle: { color, opacity: 0.25 + 0.55 * fraction },
        // The terminal point is drawn by the dedicated "dot" series below instead, at the
        // same coordinate, so the line still visually terminates there without a doubled
        // marker.
        symbolSize: isLast ? 0 : 6,
      };
    });

    const trailSeries = {
      name: s.symbol,
      type: 'line' as const,
      data: trailData,
      showSymbol: true,
      connectNulls: false,
      lineStyle: { color, width: 1.5, opacity: 0.5 },
      itemStyle: { color },
      z: 2,
    };

    const last = validPoints[validPoints.length - 1];
    const dotData: DotDatum[] = last
      ? [
          {
            value: [last.rs_ratio_approx, last.rs_momentum_approx],
            date: last.date,
            return20: s.return_20,
            symbolSize: 14,
            itemStyle: { color, borderColor: palette.surface, borderWidth: 1.5 },
            label: {
              show: true,
              formatter: s.symbol,
              position: 'right',
              color: palette.textPrimary,
              fontWeight: 'bold',
              fontSize: 11,
            },
          },
        ]
      : [];

    const dotSeries = {
      // Same `name` as the trail series so the legend shows one entry per symbol and
      // toggling it hides/shows both series together.
      name: s.symbol,
      type: 'scatter' as const,
      data: dotData,
      z: 3,
    };

    return [trailSeries, dotSeries];
  });

  // Each markArea entry is a fixed 2-tuple (opposite corners of the rectangle) -- echarts'
  // own `MarkArea2DDataItemOption` type requires exactly two elements, so this is typed as a
  // tuple explicitly rather than left to infer as a variable-length array of the two corners'
  // (differently-shaped) object literals.
  type MarkAreaLabelPosition = 'insideTopRight' | 'insideBottomRight' | 'insideBottomLeft' | 'insideTopLeft';
  interface MarkAreaCorner {
    name?: string;
    xAxis: number;
    yAxis: number;
    itemStyle?: { color: string };
    label?: { position: MarkAreaLabelPosition };
  }
  const markAreaData: [MarkAreaCorner, MarkAreaCorner][] = [
    [
      { name: 'Leading', xAxis: 100, yAxis: 100, itemStyle: { color: quadrantFill('leading', palette) }, label: { position: 'insideTopRight' } },
      { xAxis: xMax, yAxis: yMax },
    ],
    [
      { name: 'Weakening', xAxis: 100, yAxis: yMin, itemStyle: { color: quadrantFill('weakening', palette) }, label: { position: 'insideBottomRight' } },
      { xAxis: xMax, yAxis: 100 },
    ],
    [
      { name: 'Lagging', xAxis: xMin, yAxis: yMin, itemStyle: { color: quadrantFill('lagging', palette) }, label: { position: 'insideBottomLeft' } },
      { xAxis: 100, yAxis: 100 },
    ],
    [
      { name: 'Improving', xAxis: xMin, yAxis: 100, itemStyle: { color: quadrantFill('improving', palette) }, label: { position: 'insideTopLeft' } },
      { xAxis: 100, yAxis: yMax },
    ],
  ];

  // A dedicated, legend-invisible series purely to host the quadrant markArea and the
  // (100, 100) crosshair markLine -- kept off `legendData` above so it never grows a stray
  // legend entry of its own.
  const quadrantSeries = {
    name: '__quadrants__',
    type: 'scatter' as const,
    data: [],
    silent: true,
    markArea: {
      silent: true,
      label: { fontSize: 11, color: palette.textMuted },
      data: markAreaData,
    },
    markLine: {
      silent: true,
      symbol: 'none',
      label: { show: false },
      lineStyle: { color: palette.baseline, type: 'dashed' as const, width: 1 },
      data: [{ xAxis: 100 }, { yAxis: 100 }],
    },
  };

  return {
    backgroundColor: 'transparent',
    grid: { left: 56, right: 24, top: 48, bottom: 48, containLabel: true },
    legend: {
      data: legendData,
      type: 'scroll',
      top: 0,
      textStyle: { color: palette.textSecondary },
    },
    tooltip: {
      trigger: 'item',
      backgroundColor: palette.surface,
      borderColor: palette.gridline,
      textStyle: { color: palette.textPrimary },
      formatter: (paramsRaw: unknown) => {
        const params = paramsRaw as {
          seriesName?: string;
          data?: { value?: [number, number]; date?: string; return20?: number | null };
        };
        const value = params.data?.value;
        if (!value) return '';
        const [x, y] = value;
        const lines = [`<strong>${params.seriesName ?? ''}</strong>`];
        if (params.data?.date) lines.push(params.data.date);
        lines.push(`RS-ratio (approx.): ${x.toFixed(2)}`);
        lines.push(`RS-momentum (approx.): ${y.toFixed(2)}`);
        if (params.data && 'return20' in params.data) {
          lines.push(`4-week return: ${formatSignedPct(params.data.return20)}`);
        }
        return lines.join('<br/>');
      },
    },
    xAxis: {
      type: 'value',
      min: xMin,
      max: xMax,
      name: 'RS-ratio (approx.)',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: palette.textSecondary },
      axisLabel: { color: palette.textSecondary },
      axisLine: { lineStyle: { color: palette.gridline } },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      min: yMin,
      max: yMax,
      name: 'RS-momentum (approx.)',
      nameTextStyle: { color: palette.textSecondary },
      axisLabel: { color: palette.textSecondary },
      axisLine: { lineStyle: { color: palette.gridline } },
      splitLine: { lineStyle: { color: palette.gridline } },
    },
    series: [quadrantSeries, ...trailAndDotSeries],
  };
}
