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
