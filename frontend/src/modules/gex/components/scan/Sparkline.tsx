/**
 * T55 — a small ECharts line, no axes, for the trend detail panel's four rolling-history
 * mini-charts (ADX/ER/CHOP/RV20) and any future per-symbol history strip. Fixed height,
 * palette colours only (`vizPaletteFor` — never an inline hex).
 *
 * A `null` point breaks the line rather than plotting as zero (`connectNulls: false`) — the
 * same "a missing value and a zero are different facts" discipline the rest of this app
 * applies to Greeks and open interest. A series with **no** finite point at all (every day
 * null) renders the em-dash empty state instead of an empty chart canvas, so "no history yet"
 * reads as a stated fact rather than a chart that silently drew nothing.
 */
import ReactECharts from 'echarts-for-react';
import { useTheme } from '../../../../theme/ThemeContext';
import { vizPaletteFor } from '../../../../theme/vizPalette';

const DASH = '—';

export interface SparklinePoint {
  x: string | number;
  y: number | null;
}

export interface SparklineProps {
  data: SparklinePoint[];
  height?: number;
  /** Draws a thin dashed horizontal reference line at this value (e.g. a long-run average),
   * omitted entirely when `null`/`undefined`. */
  referenceValue?: number | null;
  /** Accessible label -- ECharts renders to a `<canvas>`, which carries no text content of
   * its own. */
  ariaLabel: string;
}

export function Sparkline({ data, height = 40, referenceValue, ariaLabel }: SparklineProps) {
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

  const hasData = data.some((point) => point.y != null && Number.isFinite(point.y));

  if (!hasData) {
    return (
      <span role="img" aria-label={`${ariaLabel}: no data`} className="sparkline sparkline--empty">
        {DASH}
      </span>
    );
  }

  const option = {
    animation: false,
    grid: { left: 2, right: 2, top: 4, bottom: 4 },
    xAxis: { type: 'category', show: false, data: data.map((point) => String(point.x)) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [
      {
        type: 'line',
        data: data.map((point) => (point.y == null ? null : point.y)),
        showSymbol: false,
        connectNulls: false,
        lineStyle: { color: palette.seriesAll, width: 1.5 },
        ...(referenceValue == null
          ? {}
          : {
              markLine: {
                symbol: 'none',
                silent: true,
                animation: false,
                lineStyle: { color: palette.baseline, type: 'dashed', width: 1 },
                label: { show: false },
                data: [{ yAxis: referenceValue }],
              },
            }),
      },
    ],
  };

  return (
    <span role="img" aria-label={ariaLabel} className="sparkline">
      <ReactECharts option={option} style={{ height, width: '100%' }} notMerge />
    </span>
  );
}
