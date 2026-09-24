/**
 * T122 — pagination for long tables. A 50-row scan table or a growing track-record ledger
 * pushed everything below it off the page; a page of rows plus "1–15 of 52" keeps the page
 * short while still telling the reader how many rows exist — a hidden count would make a
 * page of fifteen look like the whole universe.
 *
 * Pagination always happens **after** sorting (the caller slices its already-ordered rows),
 * so page 1 is the top of the ranking the reader asked for, never an arbitrary fifteen.
 *
 * The page number is local state, not URL state: it is a scroll position, not a view anyone
 * would link to, and it resets to page 1 whenever the row set or its ordering changes
 * (`resetKey`) so a re-sort never strands the reader on page 3 of a different ordering.
 */
import { useState } from 'react';

export const DEFAULT_PAGE_SIZE = 15;

export interface PageState<Row> {
  pageRows: Row[];
  page: number;
  pageCount: number;
  total: number;
  pageSize: number;
  setPage: (page: number) => void;
}

/**
 * Slices `rows` into pages. `pageSize` of `undefined` disables paging (every row on one
 * page). `resetKey` — anything that changes when the ordering or filter does — returns the
 * reader to the first page.
 */
export function usePagination<Row>(rows: Row[], pageSize: number | undefined, resetKey?: unknown): PageState<Row> {
  const [state, setState] = useState<{ page: number; key: unknown }>({ page: 0, key: resetKey });
  // Reset during render when the key changes -- React's documented alternative to an effect
  // for "adjust state when a prop changes", and it avoids one frame on a stale page.
  let page = state.page;
  if (state.key !== resetKey) {
    page = 0;
    setState({ page: 0, key: resetKey });
  }

  const size = pageSize ?? Math.max(rows.length, 1);
  const pageCount = Math.max(1, Math.ceil(rows.length / size));
  const clamped = Math.min(page, pageCount - 1);
  return {
    pageRows: pageSize == null ? rows : rows.slice(clamped * size, clamped * size + size),
    page: clamped,
    pageCount,
    total: rows.length,
    pageSize: size,
    setPage: (next) => setState({ page: Math.max(0, Math.min(next, pageCount - 1)), key: resetKey }),
  };
}

/** Page indices to show as buttons: the first, the last, and the current one with its
 * neighbours; `null` marks a gap. Seven pages or fewer show every button. */
export function pageWindow(page: number, pageCount: number): (number | null)[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, i) => i);
  const keep = new Set([0, pageCount - 1, page - 1, page, page + 1].filter((i) => i >= 0 && i < pageCount));
  const sorted = [...keep].sort((a, b) => a - b);
  const out: (number | null)[] = [];
  sorted.forEach((i, n) => {
    if (n > 0 && i - sorted[n - 1] > 1) out.push(null);
    out.push(i);
  });
  return out;
}

