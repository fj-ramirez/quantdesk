/**
 * T15 — `LevelHistoryTable`.
 *
 * The null-versus-zero case is the one that matters: every level column is nullable on purpose
 * (the everyday case is `ZERO_DTE` after the close, where no contract remains), and rendering a
 * missing wall as `0` would misreport "no level exists" as "level at strike zero".
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LevelHistoryTable } from './LevelHistoryTable';
import type { LevelHistoryRow } from '../api/types';

function row(overrides: Partial<LevelHistoryRow> & { snapshot_id: number; captured_at: string }): LevelHistoryRow {
  return {
    is_eod: false,
    filter: 'ALL',
    net_gex: 1_000_000_000,
    call_wall: 780,
    call_wall_gex: 1,
    put_wall: 760,
    put_wall_gex: -1,
    max_abs_strike: 760,
    max_call_gex_strike: 780,
    max_put_gex_strike: 760,
    flip_point: 771.98,
    spot: 770.19,
    computed_at: overrides.captured_at,
    ...overrides,
  };
}

describe('LevelHistoryTable', () => {
  it('renders one row per capture', () => {
    render(
      <LevelHistoryTable
        rows={[
          row({ snapshot_id: 1, captured_at: '2026-09-10T20:19:00Z' }),
          row({ snapshot_id: 2, captured_at: '2026-09-11T17:15:00Z' }),
        ]}
      />,
    );
    expect(screen.getAllByRole('row')).toHaveLength(3); // header + 2
  });

  it('orders newest first', () => {
    render(
      <LevelHistoryTable
        rows={[
          row({ snapshot_id: 1, captured_at: '2026-09-10T20:19:00Z', spot: 111 }),
          row({ snapshot_id: 2, captured_at: '2026-09-11T17:15:00Z', spot: 222 }),
        ]}
      />,
    );
    const [, first] = screen.getAllByRole('row');
    expect(within(first).getByText('222')).toBeInTheDocument();
  });

  it('renders a missing wall as a dash, never as zero', () => {
    render(
      <LevelHistoryTable
        rows={[
          row({
            snapshot_id: 1,
            captured_at: '2026-09-11T20:20:00Z',
            call_wall: null,
            put_wall: null,
            flip_point: null,
            net_gex: null,
          }),
        ]}
      />,
    );
    const [, only] = screen.getAllByRole('row');
    expect(within(only).getAllByText('—')).toHaveLength(4);
    expect(within(only).queryByText('0')).not.toBeInTheDocument();
  });

  it('marks EOD captures', () => {
    render(
      <LevelHistoryTable rows={[row({ snapshot_id: 1, captured_at: '2026-09-11T20:20:00Z', is_eod: true })]} />,
    );
    expect(screen.getByText('Yes')).toBeInTheDocument();
  });

  it('caps the rendered rows and says so, since intraday polling makes this long fast', () => {
    const rows = Array.from({ length: 60 }, (_, i) =>
      row({ snapshot_id: i, captured_at: `2026-09-11T1${(i % 10).toString()}:00:00Z` }),
    );
    render(<LevelHistoryTable rows={rows} limit={10} />);

    expect(screen.getAllByRole('row')).toHaveLength(11); // header + 10
    expect(screen.getByText(/Showing the 10 most recent of 60 captures/)).toBeInTheDocument();
  });

  it('says nothing about truncation when everything is shown', () => {
    render(<LevelHistoryTable rows={[row({ snapshot_id: 1, captured_at: '2026-09-11T20:20:00Z' })]} />);
    expect(screen.queryByText(/most recent of/)).not.toBeInTheDocument();
  });
});
