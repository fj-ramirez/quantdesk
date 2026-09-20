/**
 * T56 -- pure row-ranking and link-building logic behind `Overview.tsx` and its mini-table
 * components. Split out of any `.tsx` file for the same reason `regimeRows.ts` is: keeping
 * every component file an export-only-a-component file, out of
 * `react-refresh/only-export-components` (the project's fixed count of 4 pre-existing warnings
 * must not grow -- see `plans/continuation/07-ui.md`'s "Verified facts").
 *
 * **Null breakout rate (and null trend composite) is excluded from ranking, not sorted to an
 * end.** `SymbolBreakoutSummary.rate` is `null` below the five-event floor
 * (`BreakoutSummary.rate`, `backend/app/modules/gex/scan/breakouts.py`) -- a real "not enough resolved
 * events to quote a rate from" state, not a zero and not a low rate. `ScanTable` already pins
 * `null` to the end of a sort in both directions, so a full `/scan` table never shows a null
 * rate as though it were the worst -- but that alone would still let a null-rate symbol occupy
 * one of Overview's 8 "worst" seats if fewer than 8 symbols had a real rate, which reads exactly
 * like "measured as the worst" to someone skimming a top-8/worst-8 pair. Overview goes one step
 * further and drops null-rate (or null-composite) symbols from the ranked pool entirely before
 * taking the top/worst N; the excluded count is returned so the page can say so rather than
 * silently shrinking the list without explanation.
 *
 * "Worst by rate" is computed by sorting the ranked pool descending once and reversing it, not
 * by sorting ascending separately -- so a tie between two rates lands in the same relative
 * order in the worst list as its mirror position in the top list would, rather than an
 * independent (and potentially different, for a stable sort) tie-break.
 */
import type { RegimeSymbolRow, SymbolBreakoutSummary, TrendRow } from '../../api/types';
import { toRegimeRows, type RegimeRow } from '../regime/regimeRows';

export const TOP_N = 8;

export interface RankedBreakouts {
  top: SymbolBreakoutSummary[];
  worst: SymbolBreakoutSummary[];
  /** Symbols with `rate: null` -- excluded from both `top` and `worst`, never a zero. */
  unrankedCount: number;
}

export function rankBreakoutsByRate(summaries: SymbolBreakoutSummary[], n: number = TOP_N): RankedBreakouts {
  const ranked = summaries.filter(
    (s): s is SymbolBreakoutSummary & { rate: number } => s.rate != null,
  );
  const byRateDesc = [...ranked].sort((a, b) => b.rate - a.rate);
  return {
    top: byRateDesc.slice(0, n),
    worst: [...byRateDesc].reverse().slice(0, n),
    unrankedCount: summaries.length - ranked.length,
  };
}

export interface RankedTrend {
  top: TrendRow[];
  /** Symbols with `composite: null` -- excluded, same rule as `rankBreakoutsByRate`. */
  unrankedCount: number;
}

export function rankTrendByComposite(rows: TrendRow[], n: number = TOP_N): RankedTrend {
  const ranked = rows.filter((r): r is TrendRow & { composite: number } => r.composite != null);
  const byCompositeDesc = [...ranked].sort((a, b) => b.composite - a.composite);
  return { top: byCompositeDesc.slice(0, n), unrankedCount: rows.length - ranked.length };
}

/** The regime rows whose verdict is a genuine `continuation` call -- never a `stale` or
 * `noise-dominated` null masquerading as one (`regimeRows.ts`'s `verdictGroupOf`; `verdict`
 * itself is only ever the literal string for a real call). Not capped at `TOP_N` -- 07-ui.md
 * asks for "the regime rows whose verdict is continuation", not a top-N cut of them. */
export function continuationRegimeRows(rows: RegimeSymbolRow[]): RegimeRow[] {
  return toRegimeRows(rows).filter((row) => row.verdict === 'continuation');
}

function hrefWithParams(pathname: string, params: Record<string, string>): string {
  const search = new URLSearchParams(params);
  return `${pathname}?${search.toString()}`;
}

/** `/scan` link carrying the sort this block's own ranking used, so the reader lands on a
 * full table already ordered the way this summary presented it. `n`/`k`/`lookback` are left
 * off deliberately: Overview fetches with no override (the same defaults `useScanParams`
 * itself falls back to), so omitting them here and letting `/scan` apply its own defaults
 * can never drift from what Overview actually displayed. */
export function scanBreakoutsHref(sort: 'rate' | null, dir: 'asc' | 'desc' = 'desc'): string {
  const params: Record<string, string> = { view: 'breakouts' };
  if (sort) {
    params.sort = sort;
    params.dir = dir;
  }
  return hrefWithParams('/scan', params);
}

export function scanTrendHref(): string {
  return hrefWithParams('/scan', { view: 'trend', sort: 'composite', dir: 'desc' });
}

export const REGIME_HREF = '/regime';
