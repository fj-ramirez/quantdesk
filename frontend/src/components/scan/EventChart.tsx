/**
 * Candlestick chart of a symbol's lookback window with one marker per breakout event
 * (T44, 07-ui.md's `/scan` detail-panel spec).
 *
 * **Marker dates must match a bar's `time` exactly.** 07-ui.md lists this under "Likely
 * first-contact failures" and it is the nastiest failure mode in this file: lightweight-charts
 * silently *drops* a marker whose time has no corresponding bar. A wrong or off-by-one date
 * therefore renders a chart that looks perfectly fine and is missing information, with no
 * console error to notice. Two consequences for the code below:
 *
 *  1. Bar times and marker times both come from the same `yyyy-mm-dd` strings the API returns,
 *     passed through untouched -- never through a `Date` round trip, which would reintroduce a
 *     timezone shift and move a marker onto a date with no bar.
 *  2. Markers are filtered against the actual set of bar dates before being handed over, and
 *     the count that survived is reported through `onMarkersRendered` so a test can assert
 *     markers really appear rather than asserting the chart merely mounted.
 *
 * lightweight-charts v5 API: `chart.addSeries(CandlestickSeries, …)` and the standalone
 * `createSeriesMarkers(series, markers)` plugin (v4's `series.setMarkers` is gone).
 */
import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts';
import { useTheme } from '../../theme/ThemeContext';
import { vizPaletteFor } from '../../theme/vizPalette';
import type { BreakoutEvent, Bar } from '../../api/types';

export interface EventChartProps {
  bars: Bar[];
  events: BreakoutEvent[];
  height?: number;
  /** Called with the number of markers that matched a real bar date. Exists so a test can
   * assert markers rendered -- see the module docstring's point 2. */
  onMarkersRendered?: (count: number) => void;
}

export function EventChart({ bars, events, height = 260, onMarkersRendered }: EventChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    const container = containerRef.current;
    if (!container || bars.length === 0) {
      onMarkersRendered?.(0);
      return;
    }

    const palette = vizPaletteFor(theme);
    const chart = createChart(container, {
      height,
      layout: {
        background: { color: 'transparent' },
        textColor: palette.textSecondary,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.gridline },
        horzLines: { color: palette.gridline },
      },
      rightPriceScale: { borderColor: palette.gridline },
      timeScale: { borderColor: palette.gridline, fixLeftEdge: true, fixRightEdge: true },
    });
    chartRef.current = chart;

    const series = chart.addSeries(CandlestickSeries, {
      upColor: palette.levelSupport,
      downColor: palette.levelResistance,
      borderUpColor: palette.levelSupport,
      borderDownColor: palette.levelResistance,
      wickUpColor: palette.levelSupport,
      wickDownColor: palette.levelResistance,
    });

    // `bar.date` is already `yyyy-mm-dd` from the API; handed over verbatim. See docstring.
    series.setData(
      bars.map((bar) => ({
        time: bar.date as Time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );

    const barDates = new Set(bars.map((bar) => bar.date));
    const markers: SeriesMarker<Time>[] = events
      .filter((event) => barDates.has(event.date))
      .map((event) => ({
        time: event.date as Time,
        position: event.direction === 'up' ? 'belowBar' : 'aboveBar',
        shape: event.direction === 'up' ? 'arrowUp' : 'arrowDown',
        color:
          event.outcome === 'continued'
            ? palette.levelSupport
            : event.outcome === 'failed'
              ? palette.levelResistance
              : palette.textMuted,
        text: event.outcome === 'pending' ? '?' : undefined,
      }));
    if (markers.length > 0) createSeriesMarkers(series, markers);
    onMarkersRendered?.(markers.length);

    // The last event's level, as a horizontal reference the eye can hang the break on.
    const lastEvent = events.length > 0 ? events[events.length - 1] : null;
    if (lastEvent) {
      series.createPriceLine({
        price: lastEvent.level,
        color: palette.textMuted,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'level',
      });
    }

    chart.timeScale().fitContent();

    const observer = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth });
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [bars, events, height, theme, onMarkersRendered]);

  return <div className="event-chart" ref={containerRef} data-testid="event-chart" />;
}
