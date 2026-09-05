/**
 * TanStack Query hooks, one per T11 endpoint. Chart components (T13-T16) should use these
 * rather than calling `apiClient` directly, so query keys and caching stay consistent.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  report: (underlying: Underlying, filter: ExpiryFilter) => ['report', underlying, filter] as const,
  reportText: (underlying: Underlying, filter: ExpiryFilter) => ['report-text', underlying, filter] as const,
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

/** T39/T40's report endpoint. Same `(underlying, filter)` contract as `useGexResult`, minus
 * the pinned-snapshot dimension: the report page always reads the latest capture, because a
 * playbook drawn from a snapshot the user pinned days ago would be actively misleading. */
export function useReport(underlying: Underlying, filter: ExpiryFilter) {
  return useQuery({
    queryKey: queryKeys.report(underlying, filter),
    queryFn: () => apiClient.report(underlying, filter),
  });
}

/** The plain-text rendering, fetched lazily.
 *
 * `enabled` is the point: the full text is only fetched once the user actually expands the
 * "View full report" panel, so the common case (glance at the summary cards, leave) costs one
 * request rather than two. It is a separate endpoint round trip rather than a client-side
 * re-render of `useReport`'s data specifically so the copyable text is the backend's own
 * `render_text` output and cannot drift from it. */
export function useReportText(underlying: Underlying, filter: ExpiryFilter, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.reportText(underlying, filter),
    queryFn: () => apiClient.reportText(underlying, filter),
    enabled,
  });
}

/** T37's "Capture now" affordance, shared by the empty state.
 *
 * On success every query for that symbol is invalidated rather than just the report's: a
 * fresh capture changes the dashboard, the history page and the snapshot list too, and
 * leaving those stale is how the user ends up looking at an empty dashboard behind a report
 * that just filled in. */
export function useCaptureSnapshot(underlying: Underlying) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.captureSnapshot(underlying),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        predicate: (query) => (query.queryKey as unknown[]).includes(underlying),
      });
    },
  });
}
