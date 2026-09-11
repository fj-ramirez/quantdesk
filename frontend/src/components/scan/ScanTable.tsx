/**
 * T55 — the one sortable table every scan-family page (`/scan`, `/regime`, `/rotation`,
 * `/flows`) renders. Built once here; no page task may write its own `<table>` (07-ui.md:
 * "Nothing in the shared kit may be reimplemented inside a page").
 *
 * `Row` is deliberately whatever shape the calling page prepares, not a raw API response
 * row — a page that needs a derived column (the breakouts table's "Last event" cell combines
 * `last_event.date` and `direction`) computes that field onto its own row objects before
 * handing them to `ScanTable`, rather than `ScanTable` growing a nested-accessor mini
 * language. `ColumnDef.key` is a real `keyof Row`, used both to read the cell's raw value and,
 * when the column is sortable, as the comparison key — so a column's displayed value and its
 * sort order can never quietly disagree.
 *
 * Two rules this component enforces itself, because getting them right here is the point of
 * building it once (07-ui.md's "Likely first-contact failures" calls the second one out by
 * name):
 *  - `null`/`undefined` sorts to the end **in both directions** — `dir="asc"` does not put
 *    `null` first. JavaScript's default `Array.prototype.sort` comparator would place it
 *    arbitrarily; the comparator below special-cases it before ever comparing values.
 *  - A column's own `format` is responsible for rendering `null` as an em dash (never `0`,
 *    never blank) — every formatter in `lib/format.ts` already does this, so a column that
 *    reads `format={formatGex}` (etc.) gets the rule for free. `ScanTable` does not
 *    second-guess a column's rendering, because a future page legitimately needs a *different*
 *    null message on one column (the plan's own example: the breakouts table's Rate column
 *    renders `n<5` instead of a bare dash when `rate` is null) — enforcing one universal dash
 *    here would make that impossible to build without bypassing the shared component.
 */
import type { ReactNode } from 'react';
import { useMemo } from 'react';

export type SortDir = 'asc' | 'desc';

export interface ColumnDef<Row> {
  key: keyof Row & string;
  header: string;
  align?: 'left' | 'right' | 'center';
  /** Renders one cell. Receives the column's own raw value (`row[key]`) and the whole row,
   * for a format that reads a second field (e.g. a signed figure whose sign depends on
   * `direction`). */
  format: (value: Row[keyof Row], row: Row) => ReactNode;
  sortable?: boolean;
  /** Header tooltip (`title` attribute) — e.g. "ADX14"'s tooltip naming its cross-sectional
   * percentile. */
  title?: string;
}

export interface ScanTableProps<Row> {
  columns: ColumnDef<Row>[];
  rows: Row[];
  /** The active sort column's `key`, or `null` for "unsorted" (render in `rows` order). */
  sort: string | null;
  dir: SortDir;
  /** Called with a sortable column's `key` when its header is activated. This component does
   * not decide the next direction itself — the caller (typically `useScanParams`' `setSort`)
   * owns "clicking the active column flips direction; clicking a new one resets to desc",
   * since that policy is a page concern, not a table-rendering one. */
  onSort: (key: string) => void;
  onRowClick?: (row: Row) => void;
  rowKey: (row: Row) => string;
  selectedKey?: string | null;
  /** Accessible name for the table (it has no visible caption otherwise). Optional so a page
   * embedding several tables under its own labelled `<section>` isn't forced to duplicate a
   * heading it already has. */
  caption?: string;
}

function compareValues(a: unknown, b: unknown, dir: SortDir): number {
  const aNull = a == null;
  const bNull = b == null;
  // Pinned to the end regardless of direction -- this is the rule, not a byproduct of the
  // numeric/string comparison below ever running on a null.
  if (aNull && bNull) return 0;
  if (aNull) return 1;
  if (bNull) return -1;
  if (typeof a === 'number' && typeof b === 'number') {
    return dir === 'asc' ? a - b : b - a;
  }
  const as = String(a);
  const bs = String(b);
  return dir === 'asc' ? as.localeCompare(bs) : bs.localeCompare(as);
}

export function ScanTable<Row>({
  columns,
  rows,
  sort,
  dir,
  onSort,
  onRowClick,
  rowKey,
  selectedKey,
  caption,
}: ScanTableProps<Row>) {
  const sortColumn = sort ? columns.find((c) => c.key === sort) : undefined;

  const sortedRows = useMemo(() => {
    if (!sortColumn) return rows;
    const key = sortColumn.key;
    return [...rows].sort((a, b) => compareValues(a[key], b[key], dir));
  }, [rows, sortColumn, dir]);

  function handleRowActivate(row: Row) {
    if (onRowClick) onRowClick(row);
  }

  return (
    // T68: a scrollable container with no way to reach it by keyboard fails axe-core's
    // "scrollable-region-focusable" -- `tabIndex={0}` puts it in the tab order so arrow keys
    // can scroll it once focused. Not `role="region"` too: the table already has its own
    // `<caption>` when one is given, and pairing that with a same-named region here would
    // just announce the caption twice.
    <div className="scan-table-container" tabIndex={0}>
      <table className="scan-table">
        {caption && <caption className="scan-table__caption">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((col) => {
              const isActive = col.key === sort;
              const ariaSort = isActive ? (dir === 'asc' ? 'ascending' : 'descending') : undefined;
              return (
                <th
                  key={col.key}
                  scope="col"
                  title={col.title}
                  aria-sort={ariaSort}
                  style={{ textAlign: col.align ?? 'left' }}
                >
                  {col.sortable ? (
                    <button type="button" className="scan-table__sort-btn" onClick={() => onSort(col.key)}>
                      {col.header}
                      {isActive && (
                        <span aria-hidden="true" className="scan-table__sort-arrow">
                          {dir === 'asc' ? ' ▲' : ' ▼'}
                        </span>
                      )}
                    </button>
                  ) : (
                    col.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row) => {
            const key = rowKey(row);
            const selected = selectedKey != null && selectedKey === key;
            return (
              <tr
                key={key}
                className={selected ? 'scan-table__row scan-table__row--selected' : 'scan-table__row'}
                tabIndex={onRowClick ? 0 : undefined}
                aria-selected={onRowClick ? selected : undefined}
                onClick={onRowClick ? () => handleRowActivate(row) : undefined}
                onKeyDown={
                  onRowClick
                    ? (event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          handleRowActivate(row);
                        }
                      }
                    : undefined
                }
              >
                {columns.map((col) => (
                  <td key={col.key} style={{ textAlign: col.align ?? 'left' }}>
                    {col.format(row[col.key], row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
