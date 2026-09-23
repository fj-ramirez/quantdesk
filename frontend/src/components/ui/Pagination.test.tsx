import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Pagination } from './Pagination';
import { pageWindow, usePagination } from './usePagination';

const ROWS = Array.from({ length: 52 }, (_, i) => i);

describe('usePagination', () => {
  it('slices the given order, so page 1 is the top of the ranking', () => {
    const { result } = renderHook(() => usePagination(ROWS, 15, 'k'));
    expect(result.current.pageRows).toEqual(ROWS.slice(0, 15));
    expect(result.current.pageCount).toBe(4);
    act(() => result.current.setPage(3));
    expect(result.current.pageRows).toEqual(ROWS.slice(45));
  });

  it('returns to page 1 when the reset key changes (a re-sort or new filter)', () => {
    const { result, rerender } = renderHook(({ key }) => usePagination(ROWS, 15, key), {
      initialProps: { key: 'rate|desc' },
    });
    act(() => result.current.setPage(2));
    expect(result.current.page).toBe(2);
    rerender({ key: 'rate|asc' });
    expect(result.current.page).toBe(0);
  });

  it('shows every row when no page size is given', () => {
    const { result } = renderHook(() => usePagination(ROWS, undefined));
    expect(result.current.pageRows).toHaveLength(52);
    expect(result.current.pageCount).toBe(1);
  });
});

describe('pageWindow', () => {
  it('lists every page up to seven, and elides the middle beyond that', () => {
    expect(pageWindow(0, 4)).toEqual([0, 1, 2, 3]);
    expect(pageWindow(5, 12)).toEqual([0, null, 4, 5, 6, null, 11]);
    expect(pageWindow(0, 12)).toEqual([0, 1, null, 11]);
  });
});

describe('Pagination', () => {
  it('states the whole count, not only the page, and marks the current page', () => {
    let page = 1;
    render(<Pagination page={1} pageCount={4} total={52} pageSize={15} onPage={(p) => (page = p)} noun="symbols" />);
    const nav = screen.getByRole('navigation', { name: 'Pages of symbols' });
    expect(nav).toHaveTextContent('16–30 of 52 symbols');
    expect(screen.getByRole('button', { name: 'Page 2' })).toHaveAttribute('aria-current', 'page');
    fireEvent.click(screen.getByRole('button', { name: /Next/ }));
    expect(page).toBe(2);
  });

  it('renders nothing when everything fits on one page', () => {
    const { container } = render(<Pagination page={0} pageCount={1} total={8} pageSize={15} onPage={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
