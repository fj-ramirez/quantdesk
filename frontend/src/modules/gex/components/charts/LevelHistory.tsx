/**
 * T15 — level history: how the flip point, call wall and put wall have moved against spot.
 *
 * The `/history` page has been a scaffold since T12 ("Table/chart mounts here (T15)"); this is
 * that chart. Design choices below follow the `dataviz` skill, invoked before writing this file,
 * and are deliberately the *same* choices `GexByStrike` made so the two read as one system.
 *
 * - **One axis, four series.** Flip point, call wall, put wall and spot are all prices in the
 *   same units, so they share a y-axis honestly. (A dual axis would be the single worst thing
 *   this chart could do, and there is no reason to reach for one.)
 * - **Colors are reused, not re-chosen.** `THEME_COLORS` is imported from `GexByStrike` rather
 *   than copied, so calls stay blue and puts stay red across both charts and a palette edit
 *   can never make them disagree. Spot takes the app's accent — the color the rest of the UI
 *   already means "where the live selection is" — and flip takes primary ink, exactly as
 *   `GexByStrike` draws its derived net line in ink over the colored parts. Flip is likewise
 *   derived (where net GEX crosses zero), so ink is the consistent reading and it keeps the
 *   categorical palette at three hues rather than four.
 * - **Validated, not eyeballed.** `validate_palette.js` on the three categorical hues:
 *   light (#2a78d6, #e34948, #9935e6 on #ffffff) passes all six checks; dark
 *   (#3987e5, #e66767, #c084fc on #16171d) passes CVD (ΔE 19.2 protan), chroma, normal-vision
 *   (ΔE 21.5) and contrast, and fails only the lightness band on #c084fc (L 0.722). That is the
 *   app's existing dark accent, already used for spot in `GexByStrike`; giving spot a different
 *   color *here* would break the one-system requirement for a uniformity nicety, so it is kept
 *   deliberately rather than silently.
 * - **Nulls are gaps, never zeros.** Every level column is nullable on purpose — `ZERO_DTE`
 *   after the close legitimately has no wall at all — and plotting a missing wall at 0 would
 *   draw a cliff to the bottom of the chart and read as a real move. `null` is passed straight
 *   through to ECharts, which breaks the line.
 * - **Time axis, not category.** Captures are irregularly spaced (one a day before T18, every
 *   fifteen minutes after), so equal-width category slots would misrepresent a fast session as
 *   a calm one.
 */
import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption, TooltipComponentFormatterCallbackParams } from 'echarts';
import type { LevelHistoryRow, Underlying } from '../../api/types';
import { formatStrike } from '../../../../lib/format';
import { formatNyDateTime } from '../../../../lib/time';
import { useTheme } from '../../../../theme/ThemeContext';
import { THEME_COLORS } from './GexByStrike';

export interface LevelHistoryProps {
  rows: LevelHistoryRow[];
  symbol: Underlying;
  /** Rendered height in px. The page gives it a comfortable default; History's narrow-width
   * mode passes something shorter. */
  height?: number;
}

type SeriesKey = 'flip_point' | 'call_wall' | 'put_wall' | 'spot';

interface SeriesSpec {
  key: SeriesKey;
  name: string;
  color: (colors: (typeof THEME_COLORS)['light']) => string;
  /** Dashed for the two reference-ish series, solid for the walls — a second, non-color
   * channel carrying identity, which is what keeps this legible in grayscale and for a
   * reader who cannot separate the hues. */
  dashed: boolean;
}

const SERIES: readonly SeriesSpec[] = [
  { key: 'call_wall', name: 'Call wall', color: (c) => c.call, dashed: false },
  { key: 'put_wall', name: 'Put wall', color: (c) => c.put, dashed: false },
  { key: 'spot', name: 'Spot', color: (c) => c.accent, dashed: true },
  { key: 'flip_point', name: 'Flip point', color: (c) => c.heading, dashed: true },
];

export function LevelHistory({ rows, symbol, height = 320 }: LevelHistoryProps) {
  const { theme } = useTheme();
  const colors = THEME_COLORS[theme];

  const option = useMemo<EChartsOption>(() => {
    const points = rows.map((row) => new Date(row.captured_at).getTime());

    return {
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 64, right: 84, top: 44, bottom: 36, containLabel: false },
      legend: {
        // Always present: four series, so identity must never be color-alone.
        data: SERIES.map((s) => s.name),
        top: 4,
        textStyle: { color: colors.text, fontSize: 12 },
        inactiveColor: colors.border,
        icon: 'roundRect',
        itemWidth: 12,
        itemHeight: 3,
      },
      tooltip: {
        // Crosshair + shared tooltip: the whole question this chart answers is "where was spot
        // relative to the walls at this moment", which is a cross-series read at one instant.
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { show: false }, lineStyle: { color: colors.border } },
        backgroundColor: colors.surface,
        borderColor: colors.border,
        textStyle: { color: colors.heading, fontSize: 12 },
        formatter: (params: TooltipComponentFormatterCallbackParams) => {
          const list = Array.isArray(params) ? params : [params];
          if (list.length === 0) return '';
          const stamp = (list[0].value as [number, number | null])[0];
          const header = formatNyDateTime(new Date(stamp).toISOString());
          const lines = list
            .map((p) => {
              const value = (p.value as [number, number | null])[1];
              // A series with no value at this instant is omitted rather than shown as "—":
              // the reader is asking what the levels were, and a missing wall is answered by
              // the gap in the line, not by a row of dashes.
              if (value == null) return null;
              return `${p.marker as string}${p.seriesName} <b>${formatStrike(value)}</b>`;
            })
            .filter(Boolean);
          return [header, ...lines].join('<br/>');
        },
      },
      xAxis: {
        type: 'time',
        axisLine: { lineStyle: { color: colors.border } },
        axisTick: { show: false },
        axisLabel: { color: colors.text, fontSize: 11, hideOverlap: true },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        name: 'Price',
        nameTextStyle: { color: colors.text, fontSize: 11, align: 'left' },
        nameGap: 12,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: colors.text, fontSize: 11, formatter: (v: number) => formatStrike(v) },
        // Recessive grid: present enough to read a value off, quiet enough not to compete.
        splitLine: { lineStyle: { color: colors.border, opacity: 0.5 } },
      },
      series: SERIES.map((spec) => ({
        name: spec.name,
        type: 'line' as const,
        showSymbol: rows.length <= 60,
        symbolSize: 8,
        connectNulls: false,
        lineStyle: {
          width: 2,
          color: spec.color(colors),
          type: spec.dashed ? ('dashed' as const) : ('solid' as const),
        },
        itemStyle: { color: spec.color(colors), borderColor: colors.surface, borderWidth: 2 },
        // Selective direct labels: the line ends carry the series name, so the common read
        // needs no trip to the legend. Four series is within the skill's direct-label budget.
        endLabel: {
          show: true,
          color: colors.text,
          fontSize: 11,
          formatter: spec.name,
          distance: 6,
        },
        emphasis: { focus: 'series' as const },
        data: rows.map((row, i) => [points[i], row[spec.key]] as [number, number | null]),
      })),
    };
  }, [rows, colors]);

  return (
    <ReactECharts
      key={theme}
      option={option}
      style={{ height, width: '100%' }}
      notMerge
      aria-label={`${symbol} level history: flip point, call wall, put wall and spot over time`}
    />
  );
}
