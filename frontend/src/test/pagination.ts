/**
 * T122 — tables now page, so "one row per fixture record" is asserted as "a full first page,
 * plus a pager that states the whole count". The count in the pager is the part that matters:
 * a first page that silently dropped rows would still render a plausible-looking table.
 */
import { fireEvent, screen, within } from '@testing-library/react';
import { expect } from 'vitest';

export function expectPagedRows(table: HTMLElement, total: number, pageSize: number, noun = 'rows') {
  const shown = Math.min(total, pageSize);
  expect(within(table).getAllByRole('row')).toHaveLength(shown + 1); // + header row
  if (total > pageSize) {
    const nav = screen.getByRole('navigation', { name: `Pages of ${noun}` });
    expect(nav).toHaveTextContent(`1–${shown} of ${total} ${noun}`);
  }
}

/**
 * Runs `check` against every page of a paginated table, so an assertion about *all* rows
 * ("no row reads continuation") is not quietly narrowed to the first page.
 */
export function forEveryPage(tableName: RegExp, noun: string, check: (table: HTMLElement) => void) {
  check(screen.getByRole('table', { name: tableName }));
  const nav = screen.queryByRole('navigation', { name: `Pages of ${noun}` });
  if (!nav) return;
  const pages = within(nav).getAllByRole('button', { name: /^Page \d+$/ });
  for (const button of pages.slice(1)) {
    fireEvent.click(button);
    check(screen.getByRole('table', { name: tableName }));
  }
  fireEvent.click(pages[0]);
}
