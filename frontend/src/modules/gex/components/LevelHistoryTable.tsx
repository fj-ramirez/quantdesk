/**
 * T15 — the table half of level history, and the chart's required table view.
 *
 * Two jobs, both real. It is the accessible equivalent of `LevelHistory`'s canvas (ECharts
 * paints to `<canvas>`, which a screen reader cannot read at all), and it is the view you want
 * when the question is "what exactly was the call wall at 15:45", which a line chart answers
 * only approximately.
 *
 * Newest first, deliberately: the last capture is the one being checked most of the time, and
 * the chart already carries the left-to-right chronological read.
 *
 * Nulls render as an em dash, never as 0. Every level column is nullable on purpose — the
 * everyday case is `ZERO_DTE` after the close, where no contract remains and there is
 * genuinely no wall — and showing that as `0` would misreport "no level exists" as "level at
 * strike zero", which is the exact confusion `gex_levels`' own schema comment warns about.
 */
import type { LevelHistoryRow } from '../api/types';
import { formatGex, formatStrike } from '../../../lib/format';
import { formatNyDateTime } from '../../../lib/time';

const DASH = '—';

function level(value: number | null): string {
  return value == null ? DASH : formatStrike(value);
}

export interface LevelHistoryTableProps {
  rows: LevelHistoryRow[];
  /** Cap the rendered rows; the chart still shows every point. Intraday polling makes this a
   * long table fast (27 captures a session), and a page that renders a thousand rows to show
   * the last twenty is a page nobody scrolls. */
  limit?: number;
}

export function LevelHistoryTable({ rows, limit = 50 }: LevelHistoryTableProps) {
  const ordered = [...rows].sort((a, b) => b.captured_at.localeCompare(a.captured_at));
  const shown = ordered.slice(0, limit);

  return (
    <div className="level-history-table__scroll">
      <table className="level-history-table">
        <caption className="sr-only">
          Level history: captured time, flip point, call wall, put wall, spot and net GEX, newest
          first.
        </caption>
        <thead>
          <tr>
            <th scope="col">Captured (ET)</th>
            <th scope="col">Spot</th>
            <th scope="col">Flip</th>
            <th scope="col">Call wall</th>
            <th scope="col">Put wall</th>
            <th scope="col">Net GEX</th>
            <th scope="col">EOD</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr key={row.snapshot_id}>
              <td>{formatNyDateTime(row.captured_at)}</td>
              <td className="num">{level(row.spot)}</td>
              <td className="num">{level(row.flip_point)}</td>
              <td className="num">{level(row.call_wall)}</td>
              <td className="num">{level(row.put_wall)}</td>
              <td className="num">{row.net_gex == null ? DASH : formatGex(row.net_gex)}</td>
              <td>{row.is_eod ? 'Yes' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {ordered.length > shown.length && (
        <p className="level-history-table__more">
          Showing the {shown.length} most recent of {ordered.length} captures. The chart above
          plots all of them.
        </p>
      )}
    </div>
  );
}
