/**
 * MSW handlers for `/api/broker/*` (T159). Deterministic fixtures: a fixed series of M15 bars
 * ending at `MOCK_NOW`, events on real bar times, and levels from one snapshot.
 *
 * **The chart fixture honours `end`**: bars after it are dropped, the bar it cuts is forming,
 * and events confirmed after it disappear. A mock that ignored `end` would let a broken replay
 * control pass every test (the terminal's MSW rule, T80).
 */
import { http, HttpResponse } from 'msw';
import type { BrokerSymbol, ChartBar, ChartEvent, ChartLevel, ChartResponse, Timeframe } from '../api/types';

export const MOCK_NOW = '2026-10-13T20:00:00.000Z';
const STEP_MS = 15 * 60_000;
const COUNT = 120;

export const MOCK_SYMBOLS: BrokerSymbol[] = [
  { symbol: 'XAUUSD', last_bar_at: '2026-10-13T19:59:00.000Z', pair: { desk: 'GLD', method: 'ratio', proxy: false } },
  { symbol: 'NAS100.fs', last_bar_at: '2026-10-13T19:59:00.000Z', pair: { desk: 'QQQ', method: 'ratio', proxy: true } },
  { symbol: 'EURUSD', last_bar_at: null, pair: null },
];

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function allBars(): ChartBar[] {
  const start = Date.parse(MOCK_NOW) - COUNT * STEP_MS;
  return Array.from({ length: COUNT }, (_, i) => {
    const close = 4100 + 20 * Math.sin(i / 9) + (i % 7);
    const open = 4100 + 20 * Math.sin((i - 1) / 9) + ((i + 6) % 7);
    return {
      time: iso(start + i * STEP_MS),
      open,
      high: Math.max(open, close) + 1.5,
      low: Math.min(open, close) - 1.5,
      close,
      tick_volume: 400 + i,
      spread: 12,
      forming: false,
    };
  });
}

function allEvents(bars: ChartBar[]): ChartEvent[] {
  const at = (i: number) => bars[i].time;
  return [
    { kind: 'swing_high', direction: 'down', time: at(14), confirmed_time: at(17), price: 4126, ref_time: null },
    { kind: 'HH', direction: 'up', time: at(14), confirmed_time: at(17), price: 4126, ref_time: at(2) },
    { kind: 'swing_low', direction: 'up', time: at(42), confirmed_time: at(45), price: 4078, ref_time: null },
    { kind: 'LL', direction: 'down', time: at(42), confirmed_time: at(45), price: 4078, ref_time: at(30) },
    { kind: 'choch', direction: 'down', time: at(50), confirmed_time: at(50), price: 4090, ref_time: at(42) },
    { kind: 'sweep', direction: 'down', time: at(70), confirmed_time: at(70), price: 4126, ref_time: at(14) },
    { kind: 'pin', direction: 'up', time: at(90), confirmed_time: at(90), price: null, ref_time: null },
    { kind: 'bos', direction: 'up', time: at(110), confirmed_time: at(110), price: 4120, ref_time: at(70) },
  ];
}

export function mockChart(symbol: string, tf: Timeframe, end: string | null): ChartResponse {
  const meta = MOCK_SYMBOLS.find((s) => s.symbol === symbol) ?? MOCK_SYMBOLS[0];
  const horizon = end ? Date.parse(end) : Date.parse(MOCK_NOW);
  const all = allBars();
  const bars = all
    .filter((b) => Date.parse(b.time) < horizon)
    .map((b) => ({ ...b, forming: Date.parse(b.time) + STEP_MS > horizon }));
  const times = new Set(bars.filter((b) => !b.forming).map((b) => b.time));
  const events = allEvents(all).filter((e) => times.has(e.confirmed_time));
  const snapshot = Date.parse('2026-10-13T14:00:00.000Z');
  const levels: ChartLevel[] =
    meta.pair == null
      ? []
      : bars
          .filter((b) => Date.parse(b.time) + STEP_MS >= snapshot)
          .map((b) => ({
            time: b.time,
            captured_at: iso(snapshot),
            call_wall: 4140,
            put_wall: 4060,
            flip_point: 4095,
            spot: 4110,
          }));
  return {
    symbol,
    timeframe: tf,
    horizon: iso(horizon),
    last_bar_at: bars.length ? iso(Date.parse(bars[bars.length - 1].time) + STEP_MS - 60_000) : null,
    pair: meta.pair,
    bars,
    events,
    zones: bars.length > 60 ? [{ price: 4126, direction: 'down', touches: 3, last_time: bars[60].time }] : [],
    levels,
    level_basis:
      levels.length > 0
        ? { captured_at: iso(snapshot), basis_at: '2026-10-13T13:55:00.000Z', basis_kind: '5m', roll_after_basis: false }
        : null,
  };
}

export const brokerHandlers = [
  http.get('*/api/broker/symbols', () => HttpResponse.json<BrokerSymbol[]>(MOCK_SYMBOLS)),
  http.get('*/api/broker/chart', ({ request }) => {
    const url = new URL(request.url);
    const symbol = url.searchParams.get('symbol') ?? 'XAUUSD';
    if (!MOCK_SYMBOLS.some((s) => s.symbol === symbol)) {
      return HttpResponse.json({ detail: `Unknown broker symbol: ${symbol}` }, { status: 404 });
    }
    const tf = (url.searchParams.get('tf') ?? 'M15') as Timeframe;
    return HttpResponse.json<ChartResponse>(mockChart(symbol, tf, url.searchParams.get('end')));
  }),
];
