/**
 * Typed fetch wrapper for the T11 read API. No caching/retry logic here — TanStack Query
 * (src/api/queries.ts) owns that; this module only knows how to turn a path + params into
 * a parsed, typed response or a thrown `ApiError`.
 */
import type {
  Bar,
  BreakoutsResponse,
  CaptureHealth,
  ChainResponse,
  CrossAssetResponse,
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

// The backend runs on 8001 (not FastAPI's default 8000 — see TASKS.md/README). Always read
// this from env so a deployed build can point elsewhere without a rebuild-time hardcode.
const DEFAULT_BASE_URL = 'http://localhost:8001';

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? DEFAULT_BASE_URL;

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;
  /** The backend's parsed `detail` string, when the body was a FastAPI error envelope.
   *
   * Exists so no caller ever has to interpolate a raw response body into the UI. T37's whole
   * bug was `Failed to load SPY GEX: {"detail":"no snapshot captured yet for SPY"}` rendered
   * verbatim on the page: the envelope was never parsed, so the JSON reached the screen.
   * `message` is kept as the human-readable text (the detail when there is one), and `detail`
   * is exposed separately for code that needs to *match* on it -- distinguishing "no snapshot
   * yet", which is an empty state, from a genuine server failure, which is an error. */
  readonly detail: string | null;

  constructor(status: number, url: string, message: string, detail: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.url = url;
    this.detail = detail;
  }
}

/** Pull `detail` out of a FastAPI error envelope, tolerating anything that is not one.
 *
 * FastAPI's `HTTPException` serializes as `{"detail": "..."}`, but a validation error makes
 * `detail` an array of objects and a proxy or a crashed worker may return HTML or nothing at
 * all. Only a plain string is treated as a displayable detail; everything else falls back to
 * the status text, so a non-string can never be stringified onto the screen as `[object
 * Object]`. */
function parseErrorDetail(body: string): string | null {
  if (!body) return null;
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
    }
  } catch {
    // Not JSON -- an HTML error page or a truncated body. Nothing displayable in it.
  }
  return null;
}

type QueryParams = Record<string, string | number | boolean | undefined>;

function buildUrl(path: string, params?: QueryParams): string {
  const url = new URL(path, API_BASE_URL);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function apiFetch<T>(path: string, params?: QueryParams): Promise<T> {
  const url = buildUrl(path, params);
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const detail = parseErrorDetail(body);
    // Never `body` -- see `ApiError.detail`. An unparseable body degrades to the status
    // text, which is always safe to show.
    throw new ApiError(res.status, url, detail ?? res.statusText, detail);
  }
  return (await res.json()) as T;
}

/** Same error contract as `apiFetch`, but returns the body as text rather than parsing JSON.
 * Used for `GET /api/report/{underlying}?format=text`. */
async function apiFetchText(path: string, params?: QueryParams): Promise<string> {
  const url = buildUrl(path, params);
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const detail = parseErrorDetail(body);
    throw new ApiError(res.status, url, detail ?? res.statusText, detail);
  }
  return await res.text();
}

async function apiPost<T>(path: string, params?: QueryParams): Promise<T> {
  const url = buildUrl(path, params);
  const res = await fetch(url, { method: 'POST' });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const detail = parseErrorDetail(body);
    throw new ApiError(res.status, url, detail ?? res.statusText, detail);
  }
  return (await res.json()) as T;
}

export const apiClient = {
  gexLatest(underlying: Underlying, filter: ExpiryFilter): Promise<GexResult> {
    return apiFetch<GexResult>(`/api/gex/${underlying}/latest`, { filter });
  },

  gexSnapshot(underlying: Underlying, snapshotId: string, filter: ExpiryFilter): Promise<GexResult> {
    return apiFetch<GexResult>(`/api/gex/${underlying}/snapshots/${snapshotId}`, { filter });
  },

  levelsHistory(
    underlying: Underlying,
    opts: { filter: ExpiryFilter; start?: string; end?: string; eodOnly?: boolean },
  ): Promise<LevelHistoryRow[]> {
    return apiFetch<LevelHistoryRow[]>(`/api/gex/${underlying}/levels/history`, {
      filter: opts.filter,
      start: opts.start,
      end: opts.end,
      eod_only: opts.eodOnly,
    });
  },

  chainLatest(underlying: Underlying, expiry: string): Promise<ChainResponse> {
    return apiFetch<ChainResponse>(`/api/chains/${underlying}/latest`, { expiry });
  },

  snapshots(underlying: Underlying, limit = 30): Promise<SnapshotSummary[]> {
    return apiFetch<SnapshotSummary[]>('/api/snapshots', { underlying, limit });
  },

  /** `cfdSpot` (T41) is optional and threaded straight through as `?cfd_spot=`; omit it and
   * the response is today's report unchanged, with `cfd: null`. */
  report(underlying: Underlying, filter: ExpiryFilter, cfdSpot?: number): Promise<Report> {
    return apiFetch<Report>(`/api/report/${underlying}`, { filter, cfd_spot: cfdSpot });
  },

  /** The same report rendered as plain text by the backend (`app.gex.report.render_text`).
   * Fetched rather than reassembled in the browser so the copyable text and the on-screen
   * numbers can never drift apart. `cfdSpot` carries the CFD translation into the copyable
   * text too (T41), the same way it does in the JSON response. */
  reportText(underlying: Underlying, filter: ExpiryFilter, cfdSpot?: number): Promise<string> {
    return apiFetchText(`/api/report/${underlying}`, { filter, format: 'text', cfd_spot: cfdSpot });
  },

  /** T37's "Capture now" affordance. The only non-GET call in this client; it takes roughly
   * two seconds against the live Cboe endpoint. */
  captureSnapshot(underlying: Underlying): Promise<unknown> {
    return apiPost(`/api/snapshots/capture`, { underlying });
  },

  // -------------------------------------------------------------------------------------
  // T55: scan (T43 breakouts, T45 trend), bars (T42) and capture-health's `bars` block.
  //
  // Every symbol path segment below goes through `encodeURIComponent` -- `SCAN_UNIVERSE`
  // includes `^VIX`, and a bare `^` is a likely first-contact failure the plan calls out by
  // name (works in some browsers, breaks under `fetch`/Node's URL parser and in tests).
  // -------------------------------------------------------------------------------------

  breakouts(opts: { n?: number; k?: number; lookback?: number } = {}): Promise<BreakoutsResponse> {
    return apiFetch<BreakoutsResponse>('/api/scan/breakouts', {
      n: opts.n,
      k: opts.k,
      lookback: opts.lookback,
    });
  },

  symbolBreakouts(
    symbol: string,
    opts: { n?: number; k?: number; lookback?: number } = {},
  ): Promise<SymbolBreakoutsResponse> {
    return apiFetch<SymbolBreakoutsResponse>(`/api/scan/breakouts/${encodeURIComponent(symbol)}`, {
      n: opts.n,
      k: opts.k,
      lookback: opts.lookback,
    });
  },

  trend(): Promise<TrendResponse> {
    return apiFetch<TrendResponse>('/api/scan/trend');
  },

  symbolTrend(symbol: string): Promise<SymbolTrendResponse> {
    return apiFetch<SymbolTrendResponse>(`/api/scan/trend/${encodeURIComponent(symbol)}`);
  },

  bars(symbol: string, opts: { start?: string; end?: string } = {}): Promise<Bar[]> {
    return apiFetch<Bar[]>(`/api/bars/${encodeURIComponent(symbol)}`, { start: opts.start, end: opts.end });
  },

  universe(): Promise<UniverseResponse> {
    return apiFetch<UniverseResponse>('/api/universe');
  },

  /** T51's `/rotation` page. */
  rotation(opts: { group: RotationGroup; benchmark: RotationBenchmark; weeks: number }): Promise<RotationResponse> {
    return apiFetch<RotationResponse>('/api/scan/rotation', {
      group: opts.group,
      benchmark: opts.benchmark,
      weeks: opts.weeks,
    });
  },

  captureHealth(): Promise<CaptureHealth> {
    return apiFetch<CaptureHealth>('/api/health/capture');
  },

  /** T49's `/regime` page. `filter` is the same three-way `ALL`/`ZERO_DTE`/`EX_ZERO_DTE`
   * toolbar as the dashboard's expiry filter -- `07-ui.md`'s `/regime` section says to reuse
   * that validator rather than inventing a second one. */
  regime(filter: ExpiryFilter): Promise<RegimeResponse> {
    return apiFetch<RegimeResponse>('/api/scan/regime', { filter });
  },

  // -------------------------------------------------------------------------------------
  // T54: the cross-asset regime strip (`RegimeStrip`, plans/continuation/06-cross-asset-
  // regime.md). No query params -- one snapshot row, always the latest.
  // -------------------------------------------------------------------------------------

  crossAsset(): Promise<CrossAssetResponse> {
    return apiFetch<CrossAssetResponse>('/api/scan/cross-asset');
  },

  /** T53's `/flows` page. `window` must be one of `5 | 20 | 60` -- `useScanParams`'s own
   * validator already enforces that before this is ever called. */
  flows(window: number): Promise<FlowsResponse> {
    return apiFetch<FlowsResponse>('/api/scan/flows', { window });
  },
};
