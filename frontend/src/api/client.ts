/**
 * Typed fetch wrapper for the T11 read API. No caching/retry logic here — TanStack Query
 * (src/api/queries.ts) owns that; this module only knows how to turn a path + params into
 * a parsed, typed response or a thrown `ApiError`.
 */
import type {
  ChainResponse,
  ExpiryFilter,
  GexResult,
  LevelHistoryRow,
  SnapshotSummary,
  Underlying,
} from './types';

// The backend runs on 8001 (not FastAPI's default 8000 — see TASKS.md/README). Always read
// this from env so a deployed build can point elsewhere without a rebuild-time hardcode.
const DEFAULT_BASE_URL = 'http://localhost:8001';

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? DEFAULT_BASE_URL;

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;

  constructor(status: number, url: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.url = url;
  }
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
    throw new ApiError(res.status, url, body || res.statusText);
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
};
