/**
 * The HTTP transport every module's API client is built on (T78).
 *
 * Lifted verbatim out of `modules/gex/api/client.ts`, which is where it lived when there was
 * one module. Nothing about its behaviour changed; what changed is that it is no longer inside
 * a module, because research (T78) and terminal (T80) need the identical rules and the one
 * thing that must not be copy-pasted is `resolveBaseUrl` — its correctness depends on a chain
 * of reasoning about same-origin deployment that a second, drifting copy would silently break.
 *
 * `modules/gex/api/client.ts` re-exports `ApiError` and `API_BASE_URL` so its own callers
 * (`pages/Report.tsx`, `pages/Settings.tsx`) are untouched by the move.
 */

// The backend runs on 8001 (not FastAPI's default 8000 — see TASKS.md/README). Always read
// this from env so a deployed build can point elsewhere without a rebuild-time hardcode.
const DEFAULT_BASE_URL = 'http://localhost:8001';

/**
 * The **origin** every request is sent to — never a path prefix.
 *
 * `buildUrl` below resolves with `new URL(path, API_BASE_URL)`, and every `path` passed to it
 * is already absolute and already carries its own `/api` prefix (`/api/gex/gex/...`,
 * `/api/research/leaderboard`). The URL constructor discards a base's path component when the
 * input is absolute, so the base can only ever contribute scheme + host + port. It also
 * *requires* an absolute base: `new URL('/api/x', '/api')` throws `TypeError: Invalid base URL`.
 *
 * That is why a relative value is resolved against the page's own origin rather than passed
 * through. The production build (see `frontend/Dockerfile`) is served same-origin behind a
 * reverse proxy, so it deliberately ships with no absolute origin baked in — nothing in the
 * bundle may assume a hostname, since the app is reached both as `homeserver.local` on the LAN
 * and over Tailscale. Dev is unaffected: it passes a full `http://localhost:8001`, which
 * matches the absolute branch.
 */
function resolveBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL as string | undefined;
  if (configured && /^https?:\/\//i.test(configured)) return configured;
  // No `window` in a non-DOM context (SSR, a bare node script).
  if (typeof window !== 'undefined') return window.location.origin;
  return DEFAULT_BASE_URL;
}

export const API_BASE_URL: string = resolveBaseUrl();

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;
  /** The backend's parsed `detail` string, when the body was a FastAPI error envelope.
   *
   * Exists so no caller ever has to interpolate a raw response body into the UI. T37's whole
   * bug was `Failed to load SPY GEX: {"detail":"no snapshot captured yet for SPY"}` rendered
   * verbatim on the page: the envelope was never parsed, so the JSON reached the screen.
   * `message` is kept as the human-readable text (the detail when there is one), and `detail`
   * is exposed separately for code that needs to *match* on it — distinguishing "no snapshot
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
export function parseErrorDetail(body: string): string | null {
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
    }
  } catch {
    // Not JSON — an HTML error page or a truncated body. Nothing displayable in it.
  }
  return null;
}

export type QueryParams = Record<string, string | number | boolean | undefined>;

export function buildUrl(path: string, params?: QueryParams): string {
  const url = new URL(path, API_BASE_URL);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export async function apiFetch<T>(path: string, params?: QueryParams): Promise<T> {
  const url = buildUrl(path, params);
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const detail = parseErrorDetail(body);
    // Never `body` — see `ApiError.detail`. An unparseable body degrades to the status
    // text, which is always safe to show.
    throw new ApiError(res.status, url, detail ?? res.statusText, detail);
  }
  return (await res.json()) as T;
}

/** Same error contract as `apiFetch`, but returns the body as text rather than parsing JSON. */
export async function apiFetchText(path: string, params?: QueryParams): Promise<string> {
  const url = buildUrl(path, params);
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const detail = parseErrorDetail(body);
    throw new ApiError(res.status, url, detail ?? res.statusText, detail);
  }
  return await res.text();
}

export async function apiPost<T>(path: string, params?: QueryParams): Promise<T> {
  const url = buildUrl(path, params);
  const res = await fetch(url, { method: 'POST' });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const detail = parseErrorDetail(body);
    throw new ApiError(res.status, url, detail ?? res.statusText, detail);
  }
  return (await res.json()) as T;
}
