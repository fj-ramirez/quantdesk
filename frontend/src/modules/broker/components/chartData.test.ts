/**
 * T159: the builders between the chart response and lightweight-charts. The failure they guard
 * is the invisible one: a marker whose time matches no bar is silently dropped by the library.
 */
import { describe, expect, it } from 'vitest';
import type { ChartBar, ChartEvent, ChartLevel } from '../api/types';
import { DEFAULT_LAYERS, type Layer } from '../state/chartParams';
import { buildLevelLine, buildMarkers, toChartTime } from './chartData';

const colors = { up: 'green', down: 'red', neutral: 'grey' };

function bar(time: string, forming = false): ChartBar {
  return { time, open: 1, high: 2, low: 0.5, close: 1.5, tick_volume: 1, spread: 1, forming };
}

function event(kind: ChartEvent['kind'], time: string, direction: ChartEvent['direction'] = 'up'): ChartEvent {
  return { kind, direction, time, confirmed_time: time, price: 1, ref_time: null };
}

const bars = [bar('2026-10-13T14:00:00Z'), bar('2026-10-13T14:15:00Z'), bar('2026-10-13T14:30:00Z')];

describe('buildMarkers', () => {
  it('matches the server ISO strings exactly, whatever their spelling', () => {
    // Pydantic may print `+00:00` where the bars say `Z`: the same instant must still match.
    const { markers, dropped } = buildMarkers(
      bars,
      [event('bos', '2026-10-13T14:15:00+00:00')],
      new Set(DEFAULT_LAYERS),
      colors,
    );
    expect(dropped).toBe(0);
    expect(markers).toHaveLength(1);
    expect(markers[0].time).toBe(toChartTime('2026-10-13T14:15:00Z'));
    expect(markers[0].text).toBe('BOS');
    expect(markers[0].shape).toBe('arrowUp');
  });

  it('counts an event with no bar as dropped instead of losing it silently', () => {
    const { markers, dropped } = buildMarkers(bars, [event('sweep', '2026-10-13T14:07:00Z')], new Set(DEFAULT_LAYERS), colors);
    expect(markers).toHaveLength(0);
    expect(dropped).toBe(1);
  });

  it('draws only the layers that are on', () => {
    const events = [event('HH', bars[0].time, 'up'), event('pin', bars[1].time), event('sweep', bars[2].time, 'down')];
    const only = (layers: Layer[]) => buildMarkers(bars, events, new Set(layers), colors).markers.map((m) => m.text);
    expect(only(['structure'])).toEqual(['HH']);
    expect(only(['patterns', 'sweeps'])).toEqual(['pin', 'sweep']);
    expect(only([])).toEqual([]);
  });

  it('puts highs above the bar and lows below, and returns markers in time order', () => {
    const events = [event('LL', bars[2].time, 'down'), event('LH', bars[0].time, 'down')];
    const { markers } = buildMarkers(bars, events, new Set<Layer>(['structure']), colors);
    expect(markers.map((m) => [m.text, m.position])).toEqual([
      ['LH', 'aboveBar'],
      ['LL', 'belowBar'],
    ]);
  });
});

describe('buildLevelLine', () => {
  it('gives every bar a point, and whitespace where there is no level', () => {
    const levels: ChartLevel[] = [
      { time: bars[1].time, captured_at: bars[1].time, call_wall: 10, put_wall: null, flip_point: 5, spot: 7 },
    ];
    const call = buildLevelLine(bars, levels, 'call_wall');
    expect(call).toHaveLength(3);
    expect(call[0]).toEqual({ time: toChartTime(bars[0].time) });
    expect(call[1]).toEqual({ time: toChartTime(bars[1].time), value: 10 });
    // A null wall is whitespace, never a zero on the price axis.
    expect(buildLevelLine(bars, levels, 'put_wall')[1]).toEqual({ time: toChartTime(bars[1].time) });
  });
});
