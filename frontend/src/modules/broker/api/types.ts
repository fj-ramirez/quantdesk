/**
 * The wire contract for `/api/broker/*` (T159). Mirrors `backend/app/modules/broker/api/chart.py`;
 * update both together. Every instant is an ISO 8601 string in UTC.
 */

export const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export interface BrokerPair {
  /** The desk symbol whose GEX levels this CFD is drawn with. */
  desk: string;
  /** `offset` or `ratio`: how a desk level is translated. */
  method: string;
  /** The CFD tracks a different underlying (NAS100 is NDX, not QQQ). */
  proxy: boolean;
}

export interface BrokerSymbol {
  symbol: string;
  last_bar_at: string | null;
  pair: BrokerPair | null;
}

export interface ChartBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume: number;
  /** Lowest spread in the bar, in points. */
  spread: number;
  /** Had not closed at the horizon. No detector saw it. */
  forming: boolean;
}

export type EventKind =
  | 'swing_high'
  | 'swing_low'
  | 'HH'
  | 'LH'
  | 'HL'
  | 'LL'
  | 'bos'
  | 'choch'
  | 'inside'
  | 'outside'
  | 'pin'
  | 'engulfing'
  | 'sweep';

export interface ChartEvent {
  kind: EventKind;
  direction: 'up' | 'down' | null;
  /** The bar the event is about. */
  time: string;
  /** The bar at whose close it was knowable. */
  confirmed_time: string;
  price: number | null;
  ref_time: string | null;
}

export interface ChartZone {
  price: number;
  direction: 'up' | 'down' | null;
  touches: number;
  last_time: string;
}

export interface ChartLevel {
  /** The bar this level is drawn on. */
  time: string;
  /** The snapshot it comes from: the newest at or before the bar's close. */
  captured_at: string;
  call_wall: number | null;
  put_wall: number | null;
  flip_point: number | null;
  spot: number | null;
}

export interface LevelBasis {
  captured_at: string;
  basis_at: string;
  basis_kind: string;
  /** A roll happened between the basis and the snapshot. */
  roll_after_basis: boolean;
}

export interface ChartResponse {
  symbol: string;
  timeframe: Timeframe;
  horizon: string;
  last_bar_at: string | null;
  pair: BrokerPair | null;
  bars: ChartBar[];
  events: ChartEvent[];
  zones: ChartZone[];
  levels: ChartLevel[];
  level_basis: LevelBasis | null;
}

export interface ChartQuery {
  symbol: string;
  tf: Timeframe;
  bars?: number;
  /** ISO instant; undefined means now. */
  end?: string;
}
