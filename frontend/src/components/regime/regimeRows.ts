/**
 * T49 -- pure row-shaping logic behind `RegimeTable.tsx`, split into its own module (no JSX)
 * for the same reason `rankRows.ts` is split from `RankTable.tsx`: it keeps `RegimeTable.tsx`
 * an export-only-a-component file, out of `react-refresh/only-export-components` (the
 * project's fixed count of 4 pre-existing warnings must not grow).
 *
 * The one non-trivial thing here is `verdictGroupOf` and the default sort it feeds.
 * `verdict: null` has two distinct, named causes
 * (`plans/continuation/03-regime-board.md`'s "Verified facts (2026-09-09, from T47's first
 * live 16:45 run)"): a **stale** chain (more than 30 minutes past its trading day's close --
 * the verdict is suppressed outright, a data-quality fact) or a **noise-dominated** one (fresh,
 * but net gamma too small relative to gross to trust a direction from -- a market fact). Both
 * must render distinguishably from each other and from a genuine verdict; `verdictGroupOf`
 * resolves the ambiguity by checking staleness first, since a stale row's chain predates the
 * close and has nothing to do with today's actual positioning at all -- it must never be
 * classified next to a merely-noisy-but-current reading.
 */
import type { RegimeSymbolRow, RegimeWall } from '../../api/types';

export type VerdictGroup =
  | 'continuation'
  | 'mixed'
  | 'fade'
  | 'noise-dominated'
  | 'stale'
  | 'no-data';

/** Default sort order (07-ui.md's `/regime` section): continuation, mixed, fade,
 * noise-dominated, then -- this page's own addition, see the module docstring -- stale last of
 * all, since it is the least trustworthy row on the board. */
const GROUP_RANK: Record<VerdictGroup, number> = {
  continuation: 0,
  mixed: 1,
  fade: 2,
  'noise-dominated': 3,
  stale: 4,
  // Below stale: a stale row is at least a real reading of a real chain, just an old one.
  // This one has no chain at all, so it cannot inform anything and sorts last.
  'no-data': 5,
};

export function verdictGroupOf(row: RegimeSymbolRow): VerdictGroup {
  // Checked before `stale`, which the server reports as `false` for a missing row (there is no
  // chain whose age could exceed the threshold). Without this the row would fall through to
  // 'noise-dominated' below and claim a reading that was never taken.
  if (row.positioning === null) return 'no-data';
  if (row.stale) return 'stale';
  if (row.verdict) return row.verdict;
  // The only other way the backend gives a null verdict is `positioning.noise_dominated`
  // (plan 03's two named causes) -- but this is a fallback, not an assumption: a null verdict
  // that is neither stale nor flagged noise-dominated still renders and sorts sensibly rather
  // than throwing.
  return 'noise-dominated';
}

export interface RoomBeyond {
  strike: number;
  direction: 'up' | 'down';
}

export interface RegimeRow {
  symbol: string;
  verdict: 'continuation' | 'mixed' | 'fade' | null;
  verdictGroup: VerdictGroup;
  /** Single numeric key that reproduces "group ascending, then net/abs descending" through
   * `ScanTable`'s own single-column descending sort: `ratio - groupRank * 2`. `ratio` is a
   * fraction always <= 1 (|net|/gross), and each group is 2 apart, so no ratio spread can ever
   * push one group's rows past a neighbouring group's -- descending sort of this one number
   * yields exactly "lower group rank first, higher ratio first within a group" without
   * `ScanTable` needing to know about compound sorting at all. */
  defaultSortKey: number;
  reasons: string[];
  /** `null` for a symbol with no snapshot captured yet -- see `RegimeSymbolRow`'s docstring. */
  spot: number | null;
  netGex: number | null;
  ratio: number | null;
  flipPoint: number | null;
  /** Signed percent, server convention -- see `RegimeSymbolRow.flip_distance_pct`'s own
   * docstring for the sign caveat. */
  flipDistancePct: number | null;
  wallBelow: RegimeWall | null;
  wallAbove: RegimeWall | null;
  /** The wall-beyond room in the direction of the 5-day move (`return_5d`); `null` when that
   * side has no wall at all to measure beyond (e.g. a symbol with no wall_below in the current
   * ladder). Formatting (the signed distance and the arrow) is the table's job, not this
   * module's -- this only picks *which* side's room applies. */
  roomBeyond: RoomBeyond | null;
  zeroDteShare: number | null;
  ivRvRatio: number | null;
  trendPct: number | null;
  stale: boolean;
  noiseDominated: boolean;
  chainAgeMinutes: number | null;
}

export function toRegimeRows(rows: RegimeSymbolRow[]): RegimeRow[] {
  return rows.map((row) => {
    const verdictGroup = verdictGroupOf(row);
    const ratio = row.positioning?.ratio ?? null;
    const defaultSortKey = (ratio ?? 0) - GROUP_RANK[verdictGroup] * 2;
    const direction: 'up' | 'down' = (row.return_5d ?? 0) >= 0 ? 'up' : 'down';
    const roomWall = direction === 'up' ? row.wall_above : row.wall_below;
    const roomBeyond: RoomBeyond | null = roomWall ? { strike: roomWall.room_beyond_strike, direction } : null;

    return {
      symbol: row.underlying,
      verdict: row.verdict,
      verdictGroup,
      defaultSortKey,
      reasons: row.reasons,
      spot: row.spot,
      netGex: row.positioning?.net_gex ?? null,
      ratio,
      flipPoint: row.flip_point,
      flipDistancePct: row.flip_distance_pct,
      wallBelow: row.wall_below,
      wallAbove: row.wall_above,
      roomBeyond,
      zeroDteShare: row.zero_dte_share,
      ivRvRatio: row.iv_rv_ratio,
      trendPct: row.trend_pct,
      stale: row.stale,
      noiseDominated: row.positioning?.noise_dominated ?? false,
      chainAgeMinutes: row.chain_age_minutes,
    };
  });
}

export const REGIME_DEFAULT_SORT = 'defaultSortKey';

/** The row-tint class for `RegimeTable`'s cells (see that file's docstring for why this is a
 * per-cell wrapper rather than a `<tr>` class -- `ScanTable` owns row markup and is not
 * modifiable here). `undefined` for a genuine verdict row, which renders completely plain. */
export function rowTintClassFor(group: VerdictGroup): string | undefined {
  if (group === 'stale' || group === 'no-data') return 'regime-row-tint regime-row-tint--stale';
  if (group === 'noise-dominated') return 'regime-row-tint regime-row-tint--noise';
  return undefined;
}
