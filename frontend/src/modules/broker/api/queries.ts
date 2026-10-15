/**
 * TanStack Query hooks for `/api/broker/*` (T159). Same shape as the other modules: a key
 * table, one hook per endpoint, components never call the client directly.
 */
import { useQuery } from '@tanstack/react-query';
import { brokerClient } from './client';
import type { ChartQuery } from './types';

export const brokerQueryKeys = {
  symbols: () => ['broker-symbols'] as const,
  chart: (query: ChartQuery) => ['broker-chart', query] as const,
};

/** The ingest worker writes M1 bars every minute; a live chart re-asks on that clock. */
const LIVE_REFRESH_MS = 60_000;

export function useBrokerSymbols() {
  return useQuery({
    queryKey: brokerQueryKeys.symbols(),
    queryFn: () => brokerClient.symbols(),
    staleTime: 5 * 60 * 1000,
  });
}

export function useBrokerChart(query: ChartQuery) {
  return useQuery({
    queryKey: brokerQueryKeys.chart(query),
    queryFn: () => brokerClient.chart(query),
    // Keeps the previous chart up while the next timeframe or step loads, instead of
    // blanking it.
    placeholderData: (previous) => previous,
    // A pinned past moment never changes; only "now" moves.
    refetchInterval: query.end ? false : LIVE_REFRESH_MS,
  });
}
