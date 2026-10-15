/**
 * The broker chart (T159): CFD candles, price-action markers, recent zones as price lines, and
 * the GEX call wall, put wall and flip as step lines in CFD price.
 *
 * The chart is built once per theme and **updated** when data or layers change, so a live
 * refresh or a layer toggle keeps the zoom the user chose. It refits only when `fitKey`
 * changes (a new symbol or timeframe), because that is a different picture.
 *
 * Times are drawn in **New York** time, the desk's clock (`lib/time.ts`). The data is UTC; the
 * formatters below only change how a timestamp is printed, never which bar it belongs to.
 *
 * The forming bar (not closed at the horizon) is drawn muted. No detector saw it, so no marker
 * can sit on it, and the muting says why.
 */
import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  LineSeries,
  LineStyle,
  LineType,
  TickMarkType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
} from 'lightweight-charts';
import { useTheme } from '../../../theme/ThemeContext';
import { vizPaletteFor } from '../../../theme/vizPalette';
import type { ChartResponse } from '../api/types';
import type { Layer } from '../state/chartParams';
import { buildLevelLine, buildMarkers, toChartTime, type LevelKey } from './chartData';

export interface PriceChartProps {
  data: ChartResponse;
  layers: ReadonlySet<Layer>;
  /** Changes when the picture changes (symbol, timeframe); the chart refits then. */
  fitKey: string;
  height?: number;
  /** Reports how many markers were drawn and how many matched no bar (should be 0). */
  onMarkers?: (drawn: number, dropped: number) => void;
}

const NY = 'America/New_York';
const nyTime = new Intl.DateTimeFormat('en-US', { timeZone: NY, hour: '2-digit', minute: '2-digit', hour12: false });
const nyDay = new Intl.DateTimeFormat('en-US', { timeZone: NY, month: 'short', day: 'numeric' });
const nyMonth = new Intl.DateTimeFormat('en-US', { timeZone: NY, month: 'short', year: 'numeric' });
const nyYear = new Intl.DateTimeFormat('en-US', { timeZone: NY, year: 'numeric' });
const nyFull = new Intl.DateTimeFormat('en-US', {
  timeZone: NY,
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function asDate(time: Time): Date {
  return new Date((time as number) * 1000);
}

function tickMark(time: Time, type: TickMarkType): string {
  const date = asDate(time);
  switch (type) {
    case TickMarkType.Year:
      return nyYear.format(date);
    case TickMarkType.Month:
      return nyMonth.format(date);
    case TickMarkType.DayOfMonth:
      return nyDay.format(date);
    default:
      return nyTime.format(date);
  }
}

interface Handles {
  chart: IChartApi;
  candles: ISeriesApi<'Candlestick'>;
  lines: Record<LevelKey, ISeriesApi<'Line'>>;
  markers: ISeriesMarkersPluginApi<Time>;
  zoneLines: IPriceLine[];
}

export function PriceChart({ data, layers, fitKey, height = 520, onMarkers }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const handlesRef = useRef<Handles | null>(null);
  const fittedRef = useRef<string | null>(null);
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

  // Build: once per theme and height.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const p = vizPaletteFor(theme);
    const chart = createChart(container, {
      height,
      layout: { background: { color: 'transparent' }, textColor: p.textSecondary, attributionLogo: false },
      grid: { vertLines: { color: p.gridline }, horzLines: { color: p.gridline } },
      rightPriceScale: { borderColor: p.gridline },
      timeScale: {
        borderColor: p.gridline,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: tickMark,
      },
      localization: { timeFormatter: (time: Time) => `${nyFull.format(asDate(time))} ET` },
      crosshair: { mode: 0 },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: p.levelSupport,
      downColor: p.levelResistance,
      borderUpColor: p.levelSupport,
      borderDownColor: p.levelResistance,
      wickUpColor: p.levelSupport,
      wickDownColor: p.levelResistance,
    });
    const line = (color: string, title: string, style: LineStyle) =>
      chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        lineStyle: style,
        lineType: LineType.WithSteps,
        title,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: false,
      });
    const lines: Record<LevelKey, ISeriesApi<'Line'>> = {
      call_wall: line(p.levelResistance, 'call wall', LineStyle.Solid),
      put_wall: line(p.levelSupport, 'put wall', LineStyle.Solid),
      flip_point: line(p.seriesExZeroDte, 'flip', LineStyle.Dashed),
    };
    const markers = createSeriesMarkers(candles, []);
    handlesRef.current = { chart, candles, lines, markers, zoneLines: [] };
    fittedRef.current = null;

    const observer = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth }));
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.remove();
      handlesRef.current = null;
    };
  }, [theme, height]);

  // Update: on every data or layer change.
  useEffect(() => {
    const h = handlesRef.current;
    if (!h) return;
    const muted = palette.textMuted;

    h.candles.setData(
      data.bars.map((bar) => {
        const point = {
          time: toChartTime(bar.time),
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        };
        return bar.forming ? { ...point, color: muted, borderColor: muted, wickColor: muted } : point;
      }),
    );

    const showLevels = layers.has('levels');
    for (const key of ['call_wall', 'put_wall', 'flip_point'] as const) {
      h.lines[key].setData(showLevels ? buildLevelLine(data.bars, data.levels, key) : []);
    }

    for (const priceLine of h.zoneLines) h.candles.removePriceLine(priceLine);
    h.zoneLines = layers.has('zones')
      ? data.zones.map((zone) =>
          h.candles.createPriceLine({
            price: zone.price,
            color: palette.textMuted,
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            axisLabelVisible: true,
            title: `zone ×${zone.touches}`,
          }),
        )
      : [];

    const { markers, dropped } = buildMarkers(data.bars, data.events, layers, {
      up: palette.levelSupport,
      down: palette.levelResistance,
      neutral: palette.textMuted,
    });
    h.markers.setMarkers(markers);
    onMarkers?.(markers.length, dropped);

    if (fittedRef.current !== fitKey && data.bars.length > 0) {
      const n = data.bars.length;
      // The newest ~150 bars, not all of them: candles stay readable, and the rest is one
      // scroll away.
      h.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 150), to: n + 3 });
      fittedRef.current = fitKey;
    }
  }, [data, layers, fitKey, palette, onMarkers, theme]);

  return <div className="broker-chart" ref={containerRef} data-testid="broker-chart" />;
}

