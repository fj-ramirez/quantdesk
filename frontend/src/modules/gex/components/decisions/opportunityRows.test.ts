import { describe, expect, it } from 'vitest';
import type { DecisionsResponse } from '../../api/types';
import decisionsFixture from '../../mocks/fixtures/scan/decisions.json';
import { rowIdOf, setupLabel, toOpportunityRows } from './opportunityRows';

const fixture = decisionsFixture as DecisionsResponse;

describe('opportunityRows', () => {
  it('keeps API order as rank and builds a unique id per row', () => {
    const rows = toOpportunityRows(fixture.ranked);
    expect(rows.map((r) => r.rank)).toEqual(rows.map((_, i) => i + 1));
    expect(new Set(rows.map((r) => r.id)).size).toBe(rows.length);
    expect(rows[0].id).toBe(rowIdOf(fixture.ranked[0]));
  });

  it('the API ranking is active, then watch, then rejected, score descending within a status', () => {
    const order = { active: 0, watch: 1, rejected: 2 } as const;
    const rows = toOpportunityRows(fixture.ranked);
    for (let i = 1; i < rows.length; i += 1) {
      const a = rows[i - 1];
      const b = rows[i];
      const byStatus = order[a.status] - order[b.status];
      expect(byStatus <= 0).toBe(true);
      if (byStatus === 0) expect(a.score >= b.score).toBe(true);
    }
  });

  it('labels setup keys for humans', () => {
    expect(setupLabel('FADE_CALL_WALL')).toBe('Fade call wall');
    expect(setupLabel('CONTINUATION_DOWN')).toBe('Continuation down');
  });

  it('labels a key it has never seen, without a code change', () => {
    // T99 added GAMMA_PIN backend-side. Nothing here enumerates keys, and nothing should:
    // a new setup that needs a frontend edit before it renders is a setup that silently
    // disappears from the desk on the day it starts emitting.
    expect(setupLabel('GAMMA_PIN')).toBe('Gamma pin');
  });
});
