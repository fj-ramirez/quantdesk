/**
 * T44 -- `EventChart`'s marker/bar date matching.
 *
 * This is the one piece of the scan page whose failure is *invisible*: lightweight-charts
 * silently drops a marker whose `time` has no matching bar, so an off-by-one or a
 * timezone-shifted date renders a chart that looks perfectly fine and is quietly missing
 * information, with nothing in the console. 07-ui.md lists it under "Likely first-contact
 * failures", which is why it gets its own test file rather than a line in the page's.
 *
 * `lightweight-charts` is mocked, following the same reasoning (and the same house pattern) as
 * `GexByStrike.test.tsx`'s `echarts-for-react` mock: it draws to `<canvas>`, which jsdom does
 * not implement, and it additionally calls `window.matchMedia`, which jsdom lacks here.
 * Rendering it for real would be testing the charting library, not this component. The mock
 * captures the markers array the component hands over, so these tests assert the thing that
 * actually breaks -- which dates survived matching -- rather than that a chart was built.
 */
import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Bar, BreakoutEvent } from '../../api/types';
import { ThemeProvider } from '../../../../theme/ThemeContext';

let capturedMarkers: { time: string; position: string; shape: string }[] | null = null;
let capturedBarTimes: string[] = [];
let capturedPriceLines: { price: number }[] = [];

vi.mock('lightweight-charts', () => {
  const series = {
    setData: (data: { time: string }[]) => {
      capturedBarTimes = data.map((point) => point.time);
    },
    createPriceLine: (line: { price: number }) => {
      capturedPriceLines.push(line);
    },
  };
  return {
    CandlestickSeries: 'candlestick',
    createChart: () => ({
      addSeries: () => series,
      timeScale: () => ({ fitContent: () => {} }),
      applyOptions: () => {},
      remove: () => {},
    }),
    createSeriesMarkers: (
      _series: unknown,
      markers: { time: string; position: string; shape: string }[],
    ) => {
      capturedMarkers = markers;
      return { setMarkers: () => {} };
    },
  };
});

const { EventChart } = await import('./EventChart');

function bar(date: string, close = 100): Bar {
  return {
    date,
    open: close - 1,
    high: close + 1,
    low: close - 2,
    close,
    volume: 1000,
    source: 'test',
  };
}

function event(date: string, overrides: Partial<BreakoutEvent> = {}): BreakoutEvent {
  return {
    date,
    direction: 'up',
    level: 100,
    close: 101,
    outcome: 'continued',
    resolved_at: null,
    bars_elapsed: 3,
    follow_through_atr: 0.5,
    excursion_atr: 0.5,
    mfe_atr: 0.6,
    mae_atr: -0.1,
    rel_volume: null,
    ...overrides,
  };
}

function renderChart(bars: Bar[], events: BreakoutEvent[]) {
  const onMarkersRendered = vi.fn();
  render(
    <ThemeProvider>
      <EventChart bars={bars} events={events} onMarkersRendered={onMarkersRendered} />
    </ThemeProvider>,
  );
  return onMarkersRendered;
}

const BARS = [bar('2026-09-01'), bar('2026-09-02'), bar('2026-09-03'), bar('2026-09-04')];

beforeEach(() => {
  capturedMarkers = null;
  capturedBarTimes = [];
  capturedPriceLines = [];
});

describe('EventChart marker matching', () => {
  it('renders a marker for every event whose date has a bar', async () => {
    const onMarkersRendered = renderChart(BARS, [event('2026-09-02'), event('2026-09-04')]);
    await waitFor(() => expect(onMarkersRendered).toHaveBeenCalledWith(2));
    expect(capturedMarkers?.map((marker) => marker.time)).toEqual([
      '2026-09-02',
      '2026-09-04',
    ]);
  });

  it('drops an event whose date has no bar instead of misplacing it', async () => {
    // 2026-09-05 is a Saturday, so no bar exists for it. The point is that the marker is
    // dropped deliberately and countably here, not silently inside the charting library.
    const onMarkersRendered = renderChart(BARS, [event('2026-09-02'), event('2026-09-05')]);
    await waitFor(() => expect(onMarkersRendered).toHaveBeenCalledWith(1));
    expect(capturedMarkers?.map((marker) => marker.time)).toEqual(['2026-09-02']);
  });

  it('passes bar dates through verbatim, with no Date round trip to shift the day', async () => {
    // Parsing '2026-09-01' as local time and re-serializing lands on 2026-08-31 anywhere west
    // of UTC, which would move the marker onto a date with no bar and drop it.
    renderChart(BARS, [event('2026-09-01')]);
    await waitFor(() => expect(capturedBarTimes).toEqual([
      '2026-09-01',
      '2026-09-02',
      '2026-09-03',
      '2026-09-04',
    ]));
    expect(capturedMarkers?.[0].time).toBe('2026-09-01');
  });

  it('reports zero markers and builds nothing when there are no bars', async () => {
    const onMarkersRendered = renderChart([], [event('2026-09-02')]);
    await waitFor(() => expect(onMarkersRendered).toHaveBeenCalledWith(0));
    expect(capturedMarkers).toBeNull();
  });

  it('places an up break below its bar and a down break above it', async () => {
    renderChart(BARS, [
      event('2026-09-02', { direction: 'up' }),
      event('2026-09-03', { direction: 'down' }),
    ]);
    await waitFor(() => expect(capturedMarkers).not.toBeNull());
    expect(capturedMarkers?.[0]).toMatchObject({ position: 'belowBar', shape: 'arrowUp' });
    expect(capturedMarkers?.[1]).toMatchObject({ position: 'aboveBar', shape: 'arrowDown' });
  });

  it('draws a reference line at the last event level', async () => {
    renderChart(BARS, [event('2026-09-02', { level: 100 }), event('2026-09-03', { level: 107.5 })]);
    await waitFor(() => expect(capturedPriceLines).toHaveLength(1));
    expect(capturedPriceLines[0].price).toBe(107.5);
  });
});
