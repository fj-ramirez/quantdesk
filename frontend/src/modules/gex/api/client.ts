/**
 * Typed fetch wrapper for the T11 read API. No caching/retry logic here — TanStack Query
 * (src/api/queries.ts) owns that; this module only knows which paths exist and what they
 * return. The transport itself (base URL, `ApiError`, `apiFetch`) lives in `src/lib/http.ts`
 * since T78, shared with every other module.
 */
import type {
  Bar,
  BreakoutsResponse,
  CaptureHealth,
  ChainResponse,
  CrossAssetResponse,
  DecisionsHistoryResponse,
  DecisionsRecordRun,
  DecisionsResponse,
  ExpiryFilter,
  FlowsResponse,
  GexResult,
  LevelHistoryRow,
  RegimeResponse,
  Report,
  RotationBenchmark,
  RotationGroup,
  RotationResponse,
  SnapshotSummary,
  SymbolBreakoutsResponse,
  SymbolTrendResponse,
  TrendResponse,
  Underlying,
  UniverseResponse,
} from './types';

// T78 moved the transport to `src/lib/http.ts` -- unchanged in behaviour, but no longer inside
// a module, because research and terminal need the identical rules and `resolveBaseUrl` in
// particular must never be copy-pasted: its correctness rests on a chain of reasoning about
// same-origin deployment that a second, drifting copy would silently break.
//
// `ApiError` and `API_BASE_URL` are re-exported so this module's own callers
// (`pages/Report.tsx`, `pages/Settings.tsx`) did not have to change.
import { ApiError, API_BASE_URL, apiFetch, apiFetchText, apiPost } from '../../../lib/http';

export { ApiError, API_BASE_URL };

export const apiClient = {
  gexLatest(underlying: Underlying, filter: ExpiryFilter): Promise<GexResult> {
    return apiFetch<GexResult>(`/api/gex/gex/${underlying}/latest`, { filter });
  },

  /** T126: today's expiry pulled from the provider on request -- never stored, so
   * `snapshot.id` is null. 409 on a non-trading day, 503 when the provider has no answer. */
  gexLive(underlying: Underlying): Promise<GexResult> {
    return apiFetch<GexResult>(`/api/gex/gex/${underlying}/live`, { filter: 'ZERO_DTE' });
  },

  gexSnapshot(underlying: Underlying, snapshotId: string, filter: ExpiryFilter): Promise<GexResult> {
    return apiFetch<GexResult>(`/api/gex/gex/${underlying}/snapshots/${snapshotId}`, { filter });
  },

  levelsHistory(
    underlying: Underlying,
    opts: { filter: ExpiryFilter; start?: string; end?: string; eodOnly?: boolean },
  ): Promise<LevelHistoryRow[]> {
    return apiFetch<LevelHistoryRow[]>(`/api/gex/gex/${underlying}/levels/history`, {
      filter: opts.filter,
      start: opts.start,
      end: opts.end,
      eod_only: opts.eodOnly,
    });
  },

  chainLatest(underlying: Underlying, expiry: string): Promise<ChainResponse> {
    return apiFetch<ChainResponse>(`/api/gex/chains/${underlying}/latest`, { expiry });
  },

  snapshots(underlying: Underlying, limit = 30): Promise<SnapshotSummary[]> {
    return apiFetch<SnapshotSummary[]>('/api/gex/snapshots', { underlying, limit });
  },

  /** `cfdSpot` (T41) is optional and threaded straight through as `?cfd_spot=`; omit it and
   * the response is today's report unchanged, with `cfd: null`. */
  report(underlying: Underlying, filter: ExpiryFilter, cfdSpot?: number): Promise<Report> {
    return apiFetch<Report>(`/api/gex/report/${underlying}`, { filter, cfd_spot: cfdSpot });
  },

  /** The same report rendered as plain text by the backend (`app.gex.report.render_text`).
   * Fetched rather than reassembled in the browser so the copyable text and the on-screen
   * numbers can never drift apart. `cfdSpot` carries the CFD translation into the copyable
   * text too (T41), the same way it does in the JSON response. */
  reportText(underlying: Underlying, filter: ExpiryFilter, cfdSpot?: number): Promise<string> {
    return apiFetchText(`/api/gex/report/${underlying}`, { filter, format: 'text', cfd_spot: cfdSpot });
  },

  /** T37's "Capture now" affordance. The only non-GET call in this client; it takes roughly
   * two seconds against the live Cboe endpoint. */
  captureSnapshot(underlying: Underlying): Promise<unknown> {
    return apiPost(`/api/gex/snapshots/capture`, { underlying });
  },

  // -------------------------------------------------------------------------------------
  // T55: scan (T43 breakouts, T45 trend), bars (T42) and capture-health's `bars` block.
  //
  // Every symbol path segment below goes through `encodeURIComponent` -- `SCAN_UNIVERSE`
  // includes `^VIX`, and a bare `^` is a likely first-contact failure the plan calls out by
  // name (works in some browsers, breaks under `fetch`/Node's URL parser and in tests).
  // -------------------------------------------------------------------------------------

  breakouts(opts: { n?: number; k?: number; lookback?: number } = {}): Promise<BreakoutsResponse> {
    return apiFetch<BreakoutsResponse>('/api/gex/scan/breakouts', {
      n: opts.n,
      k: opts.k,
      lookback: opts.lookback,
    });
  },

  symbolBreakouts(
    symbol: string,
    opts: { n?: number; k?: number; lookback?: number } = {},
  ): Promise<SymbolBreakoutsResponse> {
    return apiFetch<SymbolBreakoutsResponse>(`/api/gex/scan/breakouts/${encodeURIComponent(symbol)}`, {
      n: opts.n,
      k: opts.k,
      lookback: opts.lookback,
    });
  },

  trend(): Promise<TrendResponse> {
    return apiFetch<TrendResponse>('/api/gex/scan/trend');
  },

  symbolTrend(symbol: string): Promise<SymbolTrendResponse> {
    return apiFetch<SymbolTrendResponse>(`/api/gex/scan/trend/${encodeURIComponent(symbol)}`);
  },

  bars(symbol: string, opts: { start?: string; end?: string } = {}): Promise<Bar[]> {
    return apiFetch<Bar[]>(`/api/gex/bars/${encodeURIComponent(symbol)}`, { start: opts.start, end: opts.end });
  },

  universe(): Promise<UniverseResponse> {
    return apiFetch<UniverseResponse>('/api/gex/universe');
  },

  /** T51's `/rotation` page. */
  rotation(opts: { group: RotationGroup; benchmark: RotationBenchmark; weeks: number }): Promise<RotationResponse> {
    return apiFetch<RotationResponse>('/api/gex/scan/rotation', {
      group: opts.group,
      benchmark: opts.benchmark,
      weeks: opts.weeks,
    });
  },

  captureHealth(): Promise<CaptureHealth> {
    return apiFetch<CaptureHealth>('/api/gex/health/capture');
  },

  /** T49's `/regime` page. `filter` is the same three-way `ALL`/`ZERO_DTE`/`EX_ZERO_DTE`
   * toolbar as the dashboard's expiry filter -- `07-ui.md`'s `/regime` section says to reuse
   * that validator rather than inventing a second one. */
  regime(filter: ExpiryFilter): Promise<RegimeResponse> {
    return apiFetch<RegimeResponse>('/api/gex/scan/regime', { filter });
  },

  // -------------------------------------------------------------------------------------
  // T54: the cross-asset regime strip (`RegimeStrip`, plans/continuation/06-cross-asset-
  // regime.md). No query params -- one snapshot row, always the latest.
  // -------------------------------------------------------------------------------------

  crossAsset(): Promise<CrossAssetResponse> {
    return apiFetch<CrossAssetResponse>('/api/gex/scan/cross-asset');
  },

  /** T53's `/flows` page. `window` must be one of `5 | 20 | 60` -- `useScanParams`'s own
   * validator already enforces that before this is ever called. */
  flows(window: number): Promise<FlowsResponse> {
    return apiFetch<FlowsResponse>('/api/gex/scan/flows', { window });
  },

  /** T60's `/decisions` page. `filter` is the same persisted three-way filter `/regime`
   * accepts; `minScore` only trims the cross-universe `ranked` list server-side. */
  decisions(filter: ExpiryFilter, minScore?: number): Promise<DecisionsResponse> {
    return apiFetch<DecisionsResponse>('/api/gex/decisions', { filter, min_score: minScore });
  },

  /** T61's track record: stored opportunities and their outcomes, newest first. */
  decisionsHistory(opts: { underlying?: string; outcome?: string; limit?: number } = {}): Promise<DecisionsHistoryResponse> {
    return apiFetch<DecisionsHistoryResponse>('/api/gex/decisions/history', {
      underlying: opts.underlying,
      outcome: opts.outcome,
      limit: opts.limit,
    });
  },

  /** Runs the 17:45 ET record-and-score job now (`POST /api/gex/decisions/record`). */
  recordDecisions(): Promise<DecisionsRecordRun> {
    return apiPost<DecisionsRecordRun>('/api/gex/decisions/record');
  },
};
