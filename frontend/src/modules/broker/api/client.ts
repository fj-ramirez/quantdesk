/**
 * Typed paths for `/api/broker/*` (T159). Transport is `src/lib/http.ts`, shared with every
 * module; this file only knows which endpoints exist and what they return.
 */
import { apiFetch } from '../../../lib/http';
import type { BrokerSymbol, ChartQuery, ChartResponse } from './types';

export const brokerClient = {
  symbols(): Promise<BrokerSymbol[]> {
    return apiFetch<BrokerSymbol[]>('/api/broker/symbols');
  },

  /** Bars, price-action events, zones and GEX levels in one response, all as of `end`, so
   * nothing on the chart can come from a different moment than the candles under it. */
  chart(query: ChartQuery): Promise<ChartResponse> {
    return apiFetch<ChartResponse>('/api/broker/chart', {
      symbol: query.symbol,
      tf: query.tf,
      bars: query.bars,
      end: query.end,
    });
  },
};
