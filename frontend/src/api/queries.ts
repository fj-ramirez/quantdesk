/**
 * TanStack Query hooks, one per T11 endpoint. Chart components (T13-T16) should use these
 * rather than calling `apiClient` directly, so query keys and caching stay consistent.
 */
import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';
import type { ExpiryFilter, Underlying } from './types';

/** Query key prefix helpers, exported so a component can target `queryClient.invalidateQueries`
 * without duplicating the key shape (e.g. the T19 SSE hook invalidating on a push event). */
export const queryKeys = {
  gex: (underlying: Underlying, filter: ExpiryFilter, snapshotId: string | null) =>
    ['gex', underlying, filter, snapshotId ?? 'latest'] as const,
  levelsHistory: (
    underlying: Underlying,
    filter: ExpiryFilter,
    range: { start?: string; end?: string; eodOnly?: boolean },
  ) => ['gex-levels-history', underlying, filter, range] as const,
  chainLatest: (underlying: Underlying, expiry: string) => ['chain-latest', underlying, expiry] as const,
  snapshots: (underlying: Underlying, limit: number) => ['snapshots', underlying, limit] as const,
};

/**
 * The one hook every chart component needs: resolves to `GET .../latest` when no snapshot
 * is pinned in the URL, or `GET .../snapshots/{id}` when one is — mirrors the URL-state
 * contract from `src/state/urlState.ts` directly, so a component fed the same
 * `(underlying, filter, snapshotId)` triple as the URL always renders what the URL says.
 */
export function useGexResult(underlying: Underlying, filter: ExpiryFilter, snapshotId: string | null) {
  return useQuery({
    queryKey: queryKeys.gex(underlying, filter, snapshotId),
    queryFn: () =>
      snapshotId
        ? apiClient.gexSnapshot(underlying, snapshotId, filter)
        : apiClient.gexLatest(underlying, filter),
  });
}

export function useLevelsHistory(
  underlying: Underlying,
  filter: ExpiryFilter,
  range: { start?: string; end?: string; eodOnly?: boolean } = { eodOnly: true },
) {
  return useQuery({
    queryKey: queryKeys.levelsHistory(underlying, filter, range),
    queryFn: () => apiClient.levelsHistory(underlying, { filter, ...range }),
  });
}

export function useChainLatest(underlying: Underlying, expiry: string) {
  return useQuery({
    queryKey: queryKeys.chainLatest(underlying, expiry),
    queryFn: () => apiClient.chainLatest(underlying, expiry),
    enabled: expiry.length > 0,
  });
}

export function useSnapshots(underlying: Underlying, limit = 30) {
  return useQuery({
    queryKey: queryKeys.snapshots(underlying, limit),
    queryFn: () => apiClient.snapshots(underlying, limit),
  });
}
