/**
 * T51 -- pure row-shaping logic behind `RankTable.tsx`, split into its own module (no JSX)
 * for the same reason `rrgOption.ts` is split from `RrgChart.tsx`: it keeps `RankTable.tsx`
 * an export-only-a-component file, out of `react-refresh/only-export-components` (the
 * project's fixed count of 4 pre-existing warnings must not grow).
 *
 * The quadrant boundaries here are the same ones `rrgOption.ts`'s markArea shades -- the
 * axes cross at (100, 100) -- so a symbol's chip in the table and its position in the chart
 * always agree.
 */
import type { RotationSymbol } from '../../api/types';

export type Quadrant = 'leading' | 'weakening' | 'lagging' | 'improving';

export const QUADRANT_LABEL: Record<Quadrant, string> = {
  leading: 'Leading',
  weakening: 'Weakening',
  lagging: 'Lagging',
  improving: 'Improving',
};

/** Same quadrant boundaries `rrgOption.ts`'s markArea draws: the axes cross at (100, 100). */
export function quadrantOf(rsRatio: number | null, rsMomentum: number | null): Quadrant | null {
  if (rsRatio == null || !Number.isFinite(rsRatio) || rsMomentum == null || !Number.isFinite(rsMomentum)) {
    return null;
  }
  if (rsRatio >= 100) return rsMomentum >= 100 ? 'leading' : 'weakening';
  return rsMomentum >= 100 ? 'improving' : 'lagging';
}

export interface RankRow {
  symbol: string;
  return_5: number | null;
  return_20: number | null;
  return_65: number | null;
  quadrant: Quadrant | null;
}

/** Builds one row per symbol from the rotation response -- the quadrant is derived here
 * (from each symbol's *latest valid* trail point, the same point `RrgChart`'s terminal dot
 * draws) rather than carried on `RotationSymbol` itself. `null` when that point doesn't
 * exist (every trail entry still inside its z-score warm-up), never guessed. */
export function toRankRows(symbols: RotationSymbol[]): RankRow[] {
  return symbols.map((s) => {
    const validPoints = s.trail.filter(
      (p) => p.rs_ratio_approx != null && p.rs_momentum_approx != null,
    );
    const last = validPoints[validPoints.length - 1] ?? null;
    return {
      symbol: s.symbol,
      return_5: s.return_5,
      return_20: s.return_20,
      return_65: s.return_65,
      quadrant: last ? quadrantOf(last.rs_ratio_approx, last.rs_momentum_approx) : null,
    };
  });
}

export const RANK_DEFAULT_SORT = 'return_20';
