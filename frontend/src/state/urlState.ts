/**
 * Global dashboard state lives in the URL's search params, not a store (T12 deliverable —
 * T17's review checks specifically that deep links work). `?symbol=QQQ&filter=ZERO_DTE`
 * must reproduce the exact same view as clicking QQQ + 0DTE by hand, so a copied URL is a
 * shareable snapshot of "what am I looking at".
 */
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { EXPIRY_FILTERS, UNDERLYINGS, type ExpiryFilter, type Underlying } from '../api/types';

export const DEFAULT_SYMBOL: Underlying = 'SPX';
export const DEFAULT_FILTER: ExpiryFilter = 'ALL';

function isUnderlying(value: string | null): value is Underlying {
  return value !== null && (UNDERLYINGS as readonly string[]).includes(value);
}

function isExpiryFilter(value: string | null): value is ExpiryFilter {
  return value !== null && (EXPIRY_FILTERS as readonly string[]).includes(value);
}

export interface DashboardParams {
  symbol: Underlying;
  filter: ExpiryFilter;
  /** `null` means "latest" — the `/gex/{underlying}/latest` endpoint, not a pinned
   * historical snapshot. */
  snapshotId: string | null;
  /** T41: the CFD spot the user typed off their own platform (e.g. XAUUSD for GLD), as raw
   * URL text -- `?cfd=4412.50`. `null` when absent, which must leave the report exactly as it
   * is today: no converted block, no placeholder. Kept as a string rather than a parsed
   * number here because validating "is this usable" is a report-page concern (T41), not a
   * URL-state one -- this hook's job is only to read/write the query param faithfully. */
  cfdSpot: string | null;
  setSymbol: (symbol: Underlying) => void;
  setFilter: (filter: ExpiryFilter) => void;
  setSnapshotId: (snapshotId: string | null) => void;
  setCfdSpot: (cfdSpot: string | null) => void;
}

/**
 * Reads/writes `symbol`, `filter` and `snapshot` search params. An unrecognized or absent
 * `symbol`/`filter` falls back to the default rather than throwing, so a hand-edited or
 * stale URL degrades gracefully instead of crashing the shell; an unrecognized `snapshot`
 * is passed through as-is (it's an opaque id the API validates, not a closed enum here).
 */
export function useDashboardParams(): DashboardParams {
  const [searchParams, setSearchParams] = useSearchParams();

  const symbolRaw = searchParams.get('symbol');
  const filterRaw = searchParams.get('filter');
  const snapshotId = searchParams.get('snapshot');
  const cfdSpot = searchParams.get('cfd');

  const symbol = isUnderlying(symbolRaw) ? symbolRaw : DEFAULT_SYMBOL;
  const filter = isExpiryFilter(filterRaw) ? filterRaw : DEFAULT_FILTER;

  const setSymbol = useCallback(
    (next: Underlying) => {
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        params.set('symbol', next);
        // A pinned snapshot id belongs to the previous symbol's history; switching
        // symbols without clearing it would silently request the wrong underlying's
        // snapshot (or a 404) once T11 is live.
        params.delete('snapshot');
        // T41: the CFD ratio is anchored on *this* underlying's spot -- a GLD-derived
        // XAUUSD ratio means nothing once the symbol switches to DIA, so it must not survive
        // the switch as a silently-wrong number.
        params.delete('cfd');
        return params;
      });
    },
    [setSearchParams],
  );

  const setFilter = useCallback(
    (next: ExpiryFilter) => {
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        params.set('filter', next);
        return params;
      });
    },
    [setSearchParams],
  );

  const setSnapshotId = useCallback(
    (next: string | null) => {
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        if (next) params.set('snapshot', next);
        else params.delete('snapshot');
        return params;
      });
    },
    [setSearchParams],
  );

  const setCfdSpot = useCallback(
    (next: string | null) => {
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        if (next) params.set('cfd', next);
        else params.delete('cfd');
        return params;
      });
    },
    [setSearchParams],
  );

  return useMemo(
    () => ({ symbol, filter, snapshotId, cfdSpot, setSymbol, setFilter, setSnapshotId, setCfdSpot }),
    [symbol, filter, snapshotId, cfdSpot, setSymbol, setFilter, setSnapshotId, setCfdSpot],
  );
}

// ---------------------------------------------------------------------------------------
// T55: `useScanParams`, the scan family's URL state (07-ui.md "Information architecture":
// `/scan`, `/regime`, `/rotation`, `/flows`, `/overview`). Same pattern as
// `useDashboardParams` above -- every param is validated against an allowed list and falls
// back to a default rather than throwing, so a hand-edited or stale deep link degrades
// gracefully. Not every page reads every param (`/scan` uses `view`/`n`/`k`/`lookback`/
// `sort`/`dir`; `/rotation` uses `group`/`benchmark`/`weeks`; `/flows` uses `window`), but
// they share one hook and one query-string vocabulary per 07-ui.md so a symbol click that
// carries the current page's filter into `/?symbol=...` (SymbolCell) never collides with a
// dashboard param of the same name.
// ---------------------------------------------------------------------------------------

export const SCAN_VIEWS = ['breakouts', 'trend'] as const;
export type ScanView = (typeof SCAN_VIEWS)[number];
export const DEFAULT_SCAN_VIEW: ScanView = 'breakouts';

/** `/scan`'s `N` toggle -- `app/api/scan.py`'s `_VALID_N`. */
export const SCAN_N_VALUES = [20, 55] as const;
export const DEFAULT_SCAN_N = 20;

/** `/scan`'s `k` toggle -- `app/api/scan.py`'s `_VALID_K`. */
export const SCAN_K_VALUES = [3, 5, 10] as const;
export const DEFAULT_SCAN_K = 5;

/** `/scan`'s lookback toggle. `app/scan/breakouts.py`'s `DEFAULT_LOOKBACK` (126) is the
 * middle option, not the shortest -- the shortest (40) is deliberately the one where
 * `rate=null` is the common case, per the plan's "Verified facts". */
export const SCAN_LOOKBACK_VALUES = [40, 126, 252] as const;
export const DEFAULT_SCAN_LOOKBACK = 126;

export type SortDir = 'asc' | 'desc';
export const DEFAULT_SCAN_DIR: SortDir = 'desc';

/** `/rotation`'s grouping toggle. */
export const SCAN_GROUPS = ['sectors', 'industries', 'assets'] as const;
export type ScanGroup = (typeof SCAN_GROUPS)[number];
export const DEFAULT_SCAN_GROUP: ScanGroup = 'sectors';

/** `/rotation`'s benchmark toggle. */
export const SCAN_BENCHMARKS = ['SPY', 'RSP'] as const;
export type ScanBenchmark = (typeof SCAN_BENCHMARKS)[number];
export const DEFAULT_SCAN_BENCHMARK: ScanBenchmark = 'SPY';

/** `/rotation`'s trail length, in weeks. */
export const SCAN_WEEKS_VALUES = [6, 10, 14] as const;
export const DEFAULT_SCAN_WEEKS = 6;

/** `/flows`'s lookback window, in days. */
export const SCAN_WINDOW_VALUES = [5, 20, 60] as const;
export const DEFAULT_SCAN_WINDOW = 20;

/** T60: `/decisions`' score threshold. The values are the engine's own grade boundaries
 * (`app.scan.decisions.GRADE_C/B/A` = 45/60/75) plus "everything", so the toolbar reads as
 * "C or better", "B or better", "A only" rather than as arbitrary numbers. */
export const SCAN_MIN_SCORE_VALUES = [0, 45, 60, 75] as const;
export const DEFAULT_SCAN_MIN_SCORE = 0;

function isOneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

function numberOneOf(value: string | null, allowed: readonly number[]): number | null {
  if (value === null) return null;
  const parsed = Number(value);
  return allowed.includes(parsed) ? parsed : null;
}

export interface ScanParams {
  view: ScanView;
  n: number;
  k: number;
  lookback: number;
  /** The active sort column's `ColumnDef.key`, or `null` when the page should use its own
   * default (e.g. `rate` desc for Breakouts, `composite` desc for Trend). Not validated
   * against a fixed enum here -- `ScanTable`'s column set is page-specific and would make
   * this hook depend on every page that will ever use it; an unrecognized `sort` value is
   * simply a key no column matches, which `ScanTable` already treats as "nothing sorted". */
  sort: string | null;
  dir: SortDir;
  group: ScanGroup;
  benchmark: ScanBenchmark;
  weeks: number;
  window: number;
  /** T60: `/decisions`' score threshold, one of `SCAN_MIN_SCORE_VALUES`. */
  minScore: number;
  setView: (view: ScanView) => void;
  setN: (n: number) => void;
  setK: (k: number) => void;
  setLookback: (lookback: number) => void;
  /** Sets `sort`/`dir` together -- the one write `ScanTable`'s header click needs, so a
   * caller never has to sequence two separate `setSearchParams` calls (and risk the second
   * one clobbering the first under React 18/19 batching quirks). */
  setSort: (sort: string | null, dir: SortDir) => void;
  setGroup: (group: ScanGroup) => void;
  setBenchmark: (benchmark: ScanBenchmark) => void;
  setWeeks: (weeks: number) => void;
  setWindow: (window: number) => void;
  setMinScore: (minScore: number) => void;
}

/**
 * Reads/writes `view`, `n`, `k`, `lookback`, `sort`, `dir`, `group`, `benchmark`, `weeks`,
 * `window` search params for the scan family of pages. Every enumerable param falls back to
 * its default independently of the others -- `?n=999&k=10&lookback=252` yields `n=20`
 * (the default) while `k=10` and `lookback=252` pass through untouched, the same
 * per-param-independent validation `useDashboardParams` already does for `symbol`/`filter`.
 */
export function useScanParams(): ScanParams {
  const [searchParams, setSearchParams] = useSearchParams();

  const view = isOneOf(searchParams.get('view'), SCAN_VIEWS) ?? DEFAULT_SCAN_VIEW;
  const n = numberOneOf(searchParams.get('n'), SCAN_N_VALUES) ?? DEFAULT_SCAN_N;
  const k = numberOneOf(searchParams.get('k'), SCAN_K_VALUES) ?? DEFAULT_SCAN_K;
  const lookback = numberOneOf(searchParams.get('lookback'), SCAN_LOOKBACK_VALUES) ?? DEFAULT_SCAN_LOOKBACK;
  const sortRaw = searchParams.get('sort');
  const sort = sortRaw !== null && sortRaw.length > 0 ? sortRaw : null;
  const dirRaw = searchParams.get('dir');
  const dir: SortDir = dirRaw === 'asc' || dirRaw === 'desc' ? dirRaw : DEFAULT_SCAN_DIR;
  const group = isOneOf(searchParams.get('group'), SCAN_GROUPS) ?? DEFAULT_SCAN_GROUP;
  const benchmark = isOneOf(searchParams.get('benchmark'), SCAN_BENCHMARKS) ?? DEFAULT_SCAN_BENCHMARK;
  const weeks = numberOneOf(searchParams.get('weeks'), SCAN_WEEKS_VALUES) ?? DEFAULT_SCAN_WEEKS;
  const windowParam = numberOneOf(searchParams.get('window'), SCAN_WINDOW_VALUES) ?? DEFAULT_SCAN_WINDOW;
  const minScore = numberOneOf(searchParams.get('min_score'), SCAN_MIN_SCORE_VALUES) ?? DEFAULT_SCAN_MIN_SCORE;

  const setView = useCallback(
    (next: ScanView) => setSearchParams((prev) => setParam(prev, 'view', next)),
    [setSearchParams],
  );
  const setN = useCallback((next: number) => setSearchParams((prev) => setParam(prev, 'n', String(next))), [setSearchParams]);
  const setK = useCallback((next: number) => setSearchParams((prev) => setParam(prev, 'k', String(next))), [setSearchParams]);
  const setLookback = useCallback(
    (next: number) => setSearchParams((prev) => setParam(prev, 'lookback', String(next))),
    [setSearchParams],
  );
  const setSort = useCallback(
    (nextSort: string | null, nextDir: SortDir) =>
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        if (nextSort) params.set('sort', nextSort);
        else params.delete('sort');
        params.set('dir', nextDir);
        return params;
      }),
    [setSearchParams],
  );
  const setGroup = useCallback(
    (next: ScanGroup) => setSearchParams((prev) => setParam(prev, 'group', next)),
    [setSearchParams],
  );
  const setBenchmark = useCallback(
    (next: ScanBenchmark) => setSearchParams((prev) => setParam(prev, 'benchmark', next)),
    [setSearchParams],
  );
  const setWeeks = useCallback(
    (next: number) => setSearchParams((prev) => setParam(prev, 'weeks', String(next))),
    [setSearchParams],
  );
  const setWindow = useCallback(
    (next: number) => setSearchParams((prev) => setParam(prev, 'window', String(next))),
    [setSearchParams],
  );
  const setMinScore = useCallback(
    (next: number) => setSearchParams((prev) => setParam(prev, 'min_score', String(next))),
    [setSearchParams],
  );

  return useMemo(
    () => ({
      view,
      n,
      k,
      lookback,
      sort,
      dir,
      group,
      benchmark,
      weeks,
      window: windowParam,
      minScore,
      setView,
      setN,
      setK,
      setLookback,
      setSort,
      setGroup,
      setBenchmark,
      setWeeks,
      setWindow,
      setMinScore,
    }),
    [
      view,
      n,
      k,
      lookback,
      sort,
      dir,
      group,
      benchmark,
      weeks,
      windowParam,
      setView,
      setN,
      setK,
      setLookback,
      setSort,
      setGroup,
      setBenchmark,
      setWeeks,
      setWindow,
      minScore,
      setMinScore,
    ],
  );
}

function setParam(prev: URLSearchParams, key: string, value: string): URLSearchParams {
  const params = new URLSearchParams(prev);
  params.set(key, value);
  return params;
}
