/**
 * T122 — the pager rendered under a paginated table. State and slicing live in
 * `usePagination.ts`; see its docstring for why paging happens after sorting and why the page
 * number is not URL state.
 */
import { pageWindow } from './usePagination';

export interface PaginationProps {
  page: number;
  pageCount: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
  /** What a row is, for the range label and the nav's accessible name ("opportunities"). */
  noun?: string;
}

/** Renders nothing when everything fits on one page. */
export function Pagination({ page, pageCount, total, pageSize, onPage, noun = 'rows' }: PaginationProps) {
  if (pageCount <= 1) return null;
  const from = page * pageSize + 1;
  const to = Math.min(total, from + pageSize - 1);
  return (
    <nav className="ui-pagination" aria-label={`Pages of ${noun}`}>
      <span className="ui-pagination__range" aria-live="polite">
        {from}–{to} of {total} {noun}
      </span>
      <div className="ui-pagination__controls">
        <button type="button" className="scan-toolbar__btn" onClick={() => onPage(page - 1)} disabled={page === 0}>
          ‹ Prev
        </button>
        {pageWindow(page, pageCount).map((i, n) =>
          i == null ? (
            <span key={`gap-${n}`} className="ui-pagination__gap" aria-hidden="true">
              …
            </span>
          ) : (
          <button
            key={i}
            type="button"
            className={i === page ? 'scan-toolbar__btn scan-toolbar__btn--active' : 'scan-toolbar__btn'}
            aria-current={i === page ? 'page' : undefined}
            aria-label={`Page ${i + 1}`}
            onClick={() => onPage(i)}
          >
            {i + 1}
          </button>
          ),
        )}
        <button
          type="button"
          className="scan-toolbar__btn"
          onClick={() => onPage(page + 1)}
          disabled={page === pageCount - 1}
        >
          Next ›
        </button>
      </div>
    </nav>
  );
}
