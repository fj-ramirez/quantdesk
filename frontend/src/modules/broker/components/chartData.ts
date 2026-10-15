/**
 * Pure builders from the chart response to what lightweight-charts draws (T159). Kept apart
 * from `PriceChart` so the parts that can go quietly wrong are testable without a canvas.
 *
 * **A marker's time must equal a bar's time exactly**, or lightweight-charts drops it without
 * a word (see `modules/gex/components/scan/EventChart.tsx`). Bars and events both carry the
 * server's ISO strings, converted by the same `toChartTime`, and markers are filtered against
 * the bar set, so a marker either lands on its bar or is visibly counted as dropped.
 */
import type { SeriesMarker, UTCTimestamp, WhitespaceData, LineData } from 'lightweight-charts';
import type { ChartBar, ChartEvent, ChartLevel, EventKind } from '../api/types';
import type { Layer } from '../state/chartParams';

export function toChartTime(iso: string): UTCTimestamp {
  return Math.floor(Date.parse(iso) / 1000) as UTCTimestamp;
}

/** Which toggle an event kind belongs to. */
export const KIND_LAYER: Record<EventKind, Layer> = {
  swing_high: 'swings',
  swing_low: 'swings',
  HH: 'structure',
  LH: 'structure',
  HL: 'structure',
  LL: 'structure',
  bos: 'breaks',
  choch: 'breaks',
  sweep: 'sweeps',
  pin: 'patterns',
  engulfing: 'patterns',
  inside: 'patterns',
  outside: 'patterns',
};

export interface MarkerColors {
  up: string;
  down: string;
  neutral: string;
}

/** Highs are drawn above the bar, lows below; everything else by the side it favours. */
function above(event: ChartEvent): boolean {
  switch (event.kind) {
    case 'swing_high':
    case 'HH':
    case 'LH':
      return true;
    case 'swing_low':
    case 'HL':
    case 'LL':
      return false;
    default:
      return event.direction === 'down';
  }
}

function label(kind: EventKind): string | undefined {
  switch (kind) {
    case 'HH':
    case 'LH':
    case 'HL':
    case 'LL':
      return kind;
    case 'bos':
      return 'BOS';
    case 'choch':
      return 'CHoCH';
    case 'sweep':
      return 'sweep';
    case 'pin':
      return 'pin';
    case 'engulfing':
      return 'eng';
    case 'inside':
      return 'in';
    case 'outside':
      return 'out';
    default:
      return undefined;
  }
}

function shape(event: ChartEvent): SeriesMarker<UTCTimestamp>['shape'] {
  switch (event.kind) {
    case 'bos':
    case 'choch':
      return event.direction === 'up' ? 'arrowUp' : 'arrowDown';
    case 'sweep':
      return 'circle';
    case 'pin':
    case 'engulfing':
    case 'inside':
    case 'outside':
      return 'square';
    default:
      return above(event) ? 'arrowDown' : 'arrowUp';
  }
}

export interface MarkerBuild {
  markers: SeriesMarker<UTCTimestamp>[];
  /** Events in a shown layer whose time matched no bar. Should always be 0. */
  dropped: number;
}

export function buildMarkers(
  bars: readonly ChartBar[],
  events: readonly ChartEvent[],
  layers: ReadonlySet<Layer>,
  colors: MarkerColors,
): MarkerBuild {
  const barTimes = new Set(bars.map((b) => toChartTime(b.time)));
  const markers: SeriesMarker<UTCTimestamp>[] = [];
  let dropped = 0;
  for (const event of events) {
    if (!layers.has(KIND_LAYER[event.kind])) continue;
    const time = toChartTime(event.time);
    if (!barTimes.has(time)) {
      dropped += 1;
      continue;
    }
    const isAbove = above(event);
    markers.push({
      time,
      position: isAbove ? 'aboveBar' : 'belowBar',
      shape: shape(event),
      color:
        event.direction === 'up' ? colors.up : event.direction === 'down' ? colors.down : colors.neutral,
      text: label(event.kind),
      size: event.kind === 'swing_high' || event.kind === 'swing_low' ? 0.5 : 1,
    });
  }
  // lightweight-charts requires markers in time order.
  markers.sort((a, b) => a.time - b.time);
  return { markers, dropped };
}

export type LevelKey = 'call_wall' | 'put_wall' | 'flip_point';

/**
 * One point per bar: the level's value, or whitespace where there is none, so the step line
 * breaks across a gap in capture instead of joining two snapshots days apart with a line.
 */
export function buildLevelLine(
  bars: readonly ChartBar[],
  levels: readonly ChartLevel[],
  key: LevelKey,
): (LineData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[] {
  const byTime = new Map(levels.map((l) => [toChartTime(l.time), l]));
  return bars.map((bar) => {
    const time = toChartTime(bar.time);
    const value = byTime.get(time)?.[key];
    return value == null ? { time } : { time, value };
  });
}
