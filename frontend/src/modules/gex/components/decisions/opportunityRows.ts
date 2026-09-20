/**
 * T60 -- pure row-shaping logic behind `OpportunityTable.tsx`, split into its own module (no
 * JSX) for the same reason `regimeRows.ts` is split from `RegimeTable.tsx`: it keeps the
 * table file an export-only-a-component file, out of `react-refresh/only-export-components`.
 *
 * The table renders `DecisionsResponse.ranked` verbatim -- the backend already orders it
 * (`active` before `watch` before `rejected`, then score descending), so with no sort column
 * active the rows are shown in API order and `rank` is just that position, 1-based, for the
 * leading column. Every other field is copied through under the same name so a `ColumnDef`
 * key reads exactly like the wire field it displays.
 */
import type { OpportunitySetup, OpportunitySide, OpportunityStatus, RankedOpportunity } from '../../api/types';

export interface OpportunityRow {
  /** `${underlying}:${key}` -- unique across the universe (one symbol never carries the same
   * setup key twice: the engine emits at most one fade per wall and one continuation). */
  id: string;
  rank: number;
  symbol: string;
  spot: number;
  key: string;
  setup: OpportunitySetup;
  side: OpportunitySide;
  status: OpportunityStatus;
  score: number;
  grade: string;
  entry: number;
  stop: number;
  target: number;
  rr: number | null;
  risk_atr: number | null;
  reward_atr: number | null;
  /** The full opportunity, for the detail panel -- never read by a column. */
  opportunity: RankedOpportunity;
}

export function rowIdOf(opportunity: Pick<RankedOpportunity, 'underlying' | 'key'>): string {
  return `${opportunity.underlying}:${opportunity.key}`;
}

export function toOpportunityRows(ranked: RankedOpportunity[]): OpportunityRow[] {
  return ranked.map((opportunity, index) => ({
    id: rowIdOf(opportunity),
    rank: index + 1,
    symbol: opportunity.underlying,
    spot: opportunity.spot,
    key: opportunity.key,
    setup: opportunity.setup,
    side: opportunity.side,
    status: opportunity.status,
    score: opportunity.score,
    grade: opportunity.grade,
    entry: opportunity.entry,
    stop: opportunity.stop,
    target: opportunity.target,
    rr: opportunity.rr,
    risk_atr: opportunity.risk_atr,
    reward_atr: opportunity.reward_atr,
    opportunity,
  }));
}

/** Human label for a setup key: `FADE_CALL_WALL` -> "Fade call wall". */
export function setupLabel(key: string): string {
  const words = key.toLowerCase().split('_');
  return words.map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w)).join(' ');
}
