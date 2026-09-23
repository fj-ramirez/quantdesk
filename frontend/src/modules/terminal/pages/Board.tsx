/**
 * `/terminal` — the normalized change board (T80). The module's default screen.
 *
 * Every series, its move, and **how unusual that move is for that series**. The raw change is
 * nearly useless across assets — 8bp on the 10y and 0.4% on EURUSD are not comparable numbers —
 * so the z-score is the primary column and the sort key, and the board is ranked by |z| so the
 * unusual things are at the top without anyone having to hunt.
 *
 * Three things this screen refuses to do, each because the alternative quietly lies:
 *
 * - **A row with no data shows its status, never a zero.** At a historical `as_of` most series
 *   legitimately have no value yet. A grid of zeros would render an eerily calm market.
 * - **The z is shown with the window it was measured against.** A z of 2 from 60 observations
 *   and a z of 2 from 250 are different claims, and `window_n` is on the row.
 * - **Volatility context is shown next to the z.** A 2-sigma move against a *compressed*
 *   trailing window means something quite different from one against a normal window — that is
 *   the difference between "big move" and "big move in a market that had gone quiet", which is
 *   usually the more interesting reading.
 */
import { useMemo, useState } from 'react';
import { DataTableFrame } from '../../../components/ui/DataTableFrame';
import { Pagination } from '../../../components/ui/Pagination';
import { usePagination } from '../../../components/ui/usePagination';
import { PageHeader } from '../../../components/ui/PageHeader';
import { EmptyState } from '../../gex/components/EmptyState';
import { ErrorState } from '../../gex/components/ErrorState';
import { useBoard } from '../api/queries';
import { ZCell } from '../components/ZCell';
import { useAsOf } from '../state/asOf';
import type { BoardRow } from '../api/types';

const STATUS_TEXT: Record<string, string> = {
  no_data: 'no data at this as-of',
  insufficient_history: 'not enough history to score',
  zero_variance: 'no variation in the window',
  not_daily: 'not a daily series',
  transform_error: 'transform failed',
};

function num(value: number | null, digits = 2): string {
  return value == null ? '—' : value.toFixed(digits);
}

export function Board() {
  const { asOf, isHistorical } = useAsOf();
  const [assetClass, setAssetClass] = useState<string>('');
  const board = useBoard(asOf, assetClass || undefined);

  const classes = useMemo(() => {
    const set = new Set<string>();
    for (const row of board.data?.rows ?? []) if (row.asset_class) set.add(row.asset_class);
    return [...set].sort();
  }, [board.data]);

  const rows: BoardRow[] = board.data?.rows ?? [];
  const scored = rows.filter((r) => r.status === 'ok');
  // T122: twenty rows a page, after the |z| ranking; a new as-of or class filter starts at page 1.
  const scoredPage = usePagination(scored, 20, `${asOf}|${assetClass}|${scored.length}`);
  const unscored = rows.filter((r) => r.status !== 'ok');

  return (
    <div className="terminal-page">
      <PageHeader
        title="Change board"
        description="Every series, its latest move, and how unusual that move is against its own trailing history."
        caveat={
          <p>
            Ranked by |z|. A z-score is measured against this series' own trailing window of past
            changes, which always excludes the change being scored — a move never partly defines
            its own normality.
            {isHistorical && (
              <>
                {' '}
                <strong>
                  This board is rendered as of a past moment; revisions published later are
                  excluded.
                </strong>
              </>
            )}
          </p>
        }
      />

      {board.isError ? (
        <ErrorState
          message={
            board.error instanceof Error
              ? `Could not load the board: ${board.error.message}`
              : 'Could not load the board.'
          }
        />
      ) : board.isPending ? (
        <p className="terminal-loading">Building the board…</p>
      ) : (
        <>
          <div className="terminal-filters">
            <label className="terminal-filters__field">
              <span>Asset class</span>
              <select value={assetClass} onChange={(e) => setAssetClass(e.target.value)}>
                <option value="">All</option>
                {classes.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <span className="terminal-filters__count">
              {scored.length} of {rows.length} series scored at this as-of
            </span>
          </div>

          <DataTableFrame
            title="Normalized changes"
            readingCue="z is standard deviations against this series' own trailing window. Vol context says whether that window is itself unusually compressed or wide."
            sourceTiming={`as of ${new Date(board.data.as_of).toLocaleString()} · window ${board.data.params.zscore_window}, minimum ${board.data.params.min_observations} observations`}
          >
            {scored.length === 0 ? (
              <EmptyState heading="No series could be scored at this as-of">
                Every series is either unpublished at this moment or lacks the trailing history a
                z-score needs. The rows below say which.
              </EmptyState>
            ) : (
              <>
              <div className="terminal-table__scroll">
                <table className="terminal-table">
                  <thead>
                    <tr>
                      <th scope="col">Series</th>
                      <th scope="col">Class</th>
                      <th scope="col">Date</th>
                      <th scope="col" className="is-numeric">
                        Change
                      </th>
                      <th scope="col" className="is-numeric">
                        z
                      </th>
                      <th scope="col" className="is-numeric">
                        %ile
                      </th>
                      <th scope="col" className="is-numeric">
                        n
                      </th>
                      <th scope="col">Vol context</th>
                      <th scope="col" className="is-numeric">
                        Stale
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {scoredPage.pageRows.map((row) => (
                      <tr key={row.series_id}>
                        <td>
                          <span className="terminal-table__id">{row.series_id}</span>
                          {row.display_name && (
                            <span className="terminal-table__name">{row.display_name}</span>
                          )}
                        </td>
                        <td>{row.asset_class ?? '—'}</td>
                        <td>{row.value_date ?? '—'}</td>
                        <td className="is-numeric">
                          {num(row.change, 3)}
                          {row.change_unit && (
                            <span className="terminal-table__unit"> {row.change_unit}</span>
                          )}
                        </td>
                        <ZCell z={row.z} />
                        <td className="is-numeric">{num(row.percentile, 1)}</td>
                        <td className="is-numeric">{row.window_n ?? '—'}</td>
                        <td>
                          {row.vol_flag ? (
                            <span className={`vol-flag vol-flag--${row.vol_flag}`}>
                              {row.vol_flag}
                            </span>
                          ) : (
                            <span className="vol-flag vol-flag--normal">normal</span>
                          )}
                        </td>
                        <td className="is-numeric">
                          {row.stale_days == null ? '—' : `${row.stale_days.toFixed(0)}d`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={scoredPage.page}
                pageCount={scoredPage.pageCount}
                total={scoredPage.total}
                pageSize={scoredPage.pageSize}
                onPage={scoredPage.setPage}
                noun="series"
              />
              </>
            )}
          </DataTableFrame>

          {unscored.length > 0 && (
            <DataTableFrame
              title="Not scored"
              readingCue="These series have no z at this as-of, and the reason matters — an unpublished series and a series with no variation are different facts. Neither is a zero."
            >
              <ul className="terminal-unscored">
                {unscored.map((row) => (
                  <li key={row.series_id}>
                    <span className="terminal-table__id">{row.series_id}</span>
                    <span className="terminal-unscored__why">
                      {STATUS_TEXT[row.status] ?? row.status}
                    </span>
                  </li>
                ))}
              </ul>
            </DataTableFrame>
          )}
        </>
      )}
    </div>
  );
}
