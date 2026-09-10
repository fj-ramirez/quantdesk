/**
 * TanStack Query hooks, one per T11 endpoint. Chart components (T13-T16) should use these
 * rather than calling `apiClient` directly, so query keys and caching stay consistent.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import type { ExpiryFilter, RotationBenchmark, RotationGroup, Underlying } from './types';

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
  report: (underlying: Underlying, filter: ExpiryFilter, cfdSpot?: number) =>
    ['report', underlying, filter, cfdSpot ?? null] as const,
  reportText: (underlying: Underlying, filter: ExpiryFilter, cfdSpot?: number) =>
    ['report-text', underlying, filter, cfdSpot ?? null] as const,
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
 * playbook drawn from a snapshot the user pinned days ago would be actively misleading.
 *
 * `cfdSpot` (T41) is optional; passing it re-fetches with `?cfd_spot=` and the response's
 * `cfd` block carries the converted levels. Leaving it out is the default and produces
 * exactly today's report -- see `queryKeys.report`, which folds it into the cache key so a
 * change to the typed spot doesn't silently serve a stale conversion. */
export function useReport(underlying: Underlying, filter: ExpiryFilter, cfdSpot?: number) {
  return useQuery({
    queryKey: queryKeys.report(underlying, filter, cfdSpot),
    queryFn: () => apiClient.report(underlying, filter, cfdSpot),
  });
}

/** The plain-text rendering, fetched lazily.
 *
 * `enabled` is the point: the full text is only fetched once the user actually expands the
 * "View full report" panel, so the common case (glance at the summary cards, leave) costs one
 * request rather than two. It is a separate endpoint round trip rather than a client-side
 * re-render of `useReport`'s data specifically so the copyable text is the backend's own
 * `render_text` output and cannot drift from it. `cfdSpot` (T41) is forwarded so the copied
 * text carries the same translated playbook the on-screen cards show. */
export function useReportText(underlying: Underlying, filter: ExpiryFilter, enabled: boolean, cfdSpot?: number) {
  return useQuery({
    queryKey: queryKeys.reportText(underlying, filter, cfdSpot),
    queryFn: () => apiClient.reportText(underlying, filter, cfdSpot),
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

// ---------------------------------------------------------------------------------------
// T55: scan (T43 breakouts, T45 trend), bars (T42), universe (T42) and capture health's
// `bars` block -- one hook per `app/api/scan.py` / `app/api/bars.py` / `app/api/health.py`
// route, same "components call hooks, never `apiClient` directly" rule as above.
//
// No caching tricks beyond TanStack Query's own defaults: `GET /api/scan/trend` genuinely
// takes 3.5-5s (it re-flattens 28 option chains per request, see that route's own module
// docstring) -- that is a backend problem with its own future task, not something
// `useTrend` should paper over with a longer `staleTime` or similar.
// ---------------------------------------------------------------------------------------

export const scanQueryKeys = {
  breakouts: (n?: number, k?: number, lookback?: number) =>
    ['scan-breakouts', n ?? null, k ?? null, lookback ?? null] as const,
  symbolBreakouts: (symbol: string, n?: number, k?: number, lookback?: number) =>
    ['scan-breakouts', symbol, n ?? null, k ?? null, lookback ?? null] as const,
  trend: () => ['scan-trend'] as const,
  symbolTrend: (symbol: string) => ['scan-trend', symbol] as const,
  bars: (symbol: string, start?: string, end?: string) => ['bars', symbol, start ?? null, end ?? null] as const,
  universe: () => ['universe'] as const,
  captureHealth: () => ['capture-health'] as const,
  rotation: (group: RotationGroup, benchmark: RotationBenchmark, weeks: number) =>
    ['scan-rotation', group, benchmark, weeks] as const,
  regime: (filter: ExpiryFilter) => ['scan-regime', filter] as const,
};

export function useBreakouts(params: { n?: number; k?: number; lookback?: number } = {}) {
  return useQuery({
    queryKey: scanQueryKeys.breakouts(params.n, params.k, params.lookback),
    queryFn: () => apiClient.breakouts(params),
  });
}

/** `symbol` is any `SCAN_UNIVERSE` ticker, including `^VIX` -- `apiClient.symbolBreakouts`
 * percent-encodes it. `enabled` guards the empty-string transition some callers pass through
 * while a symbol is still being resolved from the URL. */
export function useSymbolBreakouts(symbol: string, params: { n?: number; k?: number; lookback?: number } = {}) {
  return useQuery({
    queryKey: scanQueryKeys.symbolBreakouts(symbol, params.n, params.k, params.lookback),
    queryFn: () => apiClient.symbolBreakouts(symbol, params),
    enabled: symbol.length > 0,
  });
}

export function useTrend() {
  return useQuery({
    queryKey: scanQueryKeys.trend(),
    queryFn: () => apiClient.trend(),
  });
}

export function useSymbolTrend(symbol: string) {
  return useQuery({
    queryKey: scanQueryKeys.symbolTrend(symbol),
    queryFn: () => apiClient.symbolTrend(symbol),
    enabled: symbol.length > 0,
  });
}

export function useBars(symbol: string, opts: { start?: string; end?: string } = {}) {
  return useQuery({
    queryKey: scanQueryKeys.bars(symbol, opts.start, opts.end),
    queryFn: () => apiClient.bars(symbol, opts),
    enabled: symbol.length > 0,
  });
}

export function useUniverse() {
  return useQuery({
    queryKey: scanQueryKeys.universe(),
    queryFn: () => apiClient.universe(),
  });
}

/** Backs `BarsFreshness` (and the TopBar's scan-family toolbar). */
export function useCaptureHealth() {
  return useQuery({
    queryKey: scanQueryKeys.captureHealth(),
    queryFn: () => apiClient.captureHealth(),
  });
}

/** T51's `/rotation` page. `group`/`benchmark`/`weeks` come straight from `useScanParams`. */
export function useRotation(group: RotationGroup, benchmark: RotationBenchmark, weeks: number) {
  return useQuery({
    queryKey: scanQueryKeys.rotation(group, benchmark, weeks),
    queryFn: () => apiClient.rotation({ group, benchmark, weeks }),
  });
}

/** T49's `/regime` page. `filter` comes from `useDashboardParams` -- the plan's own
 * instruction to reuse the dashboard's expiry-filter validator rather than a second one. */
export function useRegime(filter: ExpiryFilter) {
  return useQuery({
    queryKey: scanQueryKeys.regime(filter),
    queryFn: () => apiClient.regime(filter),
  });
}

// ---------------------------------------------------------------------------------------
// T54: `RegimeStrip` (plans/continuation/06-cross-asset-regime.md). No params -- always the
// latest snapshot row.
// ---------------------------------------------------------------------------------------

export function useCrossAsset() {
  return useQuery({
    queryKey: ['scan-cross-asset'] as const,
    queryFn: () => apiClient.crossAsset(),
  });
}

// ---------------------------------------------------------------------------------------
// T53: `/flows`. `window` comes straight from `useScanParams`, already validated against
// `5 | 20 | 60`.
// ---------------------------------------------------------------------------------------

export function useFlows(window: number) {
  return useQuery({
    queryKey: ['scan-flows', window] as const,
    queryFn: () => apiClient.flows(window),
  });
}
