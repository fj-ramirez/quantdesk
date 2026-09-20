import { describe, expect, it } from 'vitest';
import {
  continuationRegimeRows,
  rankBreakoutsByRate,
  rankTrendByComposite,
  scanBreakoutsHref,
  scanTrendHref,
  REGIME_HREF,
} from './overviewRows';
import type { RegimeSymbolRow, SymbolBreakoutSummary, TrendRow } from '../../api/types';

function summary(symbol: string, rate: number | null): SymbolBreakoutSummary {
  return {
    symbol,
    events: rate == null ? 2 : 8,
    continued: 0,
    failed: 0,
    pending: 0,
    rate,
    mean_follow_through_atr: null,
    last_event: null,
    status: null,
  };
}

function trendRow(symbol: string, composite: number | null): TrendRow {
  return {
    symbol,
    adx14: null,
    er20: null,
    chop14: null,
    vr: null,
    vr_z: null,
    rv20: null,
    iv30: null,
    iv_rv_ratio: null,
    adx_pct: null,
    er_pct: null,
    chop_pct: null,
    vr_pct: null,
    composite,
  };
}

const BASE_REGIME_ROW: Omit<RegimeSymbolRow, 'underlying' | 'verdict'> = {
  filter: 'ALL',
  spot: 100,
  atr14: 1,
  iv30: null,
  rv20: null,
  iv_rv_ratio: null,
  return_5d: 0,
  as_of: '2026-09-09T00:00:00Z',
  effective_at: '2026-09-09T00:00:00Z',
  chain_age_minutes: 5,
  stale: false,
  positioning: {
    net_gex: 1,
    abs_gex: 2,
    ratio: 0.5,
    ratio_floor: 0.1,
    noise_dominated: false,
    direction: 'up',
    label: 'x',
    description: 'x',
  },
  flip_point: null,
  flip_distance: null,
  flip_distance_pct: null,
  flip_distance_atr: null,
  wall_below: null,
  wall_above: null,
  zero_dte_share: null,
  reasons: [],
  trend_pct: 0.5,
};

function regimeRow(underlying: string, verdict: 'continuation' | 'mixed' | 'fade' | null, stale = false): RegimeSymbolRow {
  return { ...BASE_REGIME_ROW, underlying, verdict, stale };
}

describe('rankBreakoutsByRate', () => {
  it('excludes null-rate symbols from both the top and worst lists', () => {
    const summaries = [
      summary('HIGH', 0.9),
      summary('MID', 0.5),
      summary('LOW', 0.1),
      summary('NUL1', null),
      summary('NUL2', null),
    ];
    const { top, worst, unrankedCount } = rankBreakoutsByRate(summaries, 8);
    expect(unrankedCount).toBe(2);
    expect(top.map((s) => s.symbol)).toEqual(['HIGH', 'MID', 'LOW']);
    expect(worst.map((s) => s.symbol)).toEqual(['LOW', 'MID', 'HIGH']);
    expect(top.some((s) => s.rate == null)).toBe(false);
    expect(worst.some((s) => s.rate == null)).toBe(false);
  });

  it('caps each list at n and keeps ties in a stable, mirrored order', () => {
    const summaries = ['A', 'B', 'C', 'D', 'E', 'F'].map((s) => summary(s, 0.5));
    const { top, worst } = rankBreakoutsByRate(summaries, 3);
    expect(top.map((s) => s.symbol)).toEqual(['A', 'B', 'C']);
    // worst = reverse of the full descending-sorted (stable) list, then sliced.
    expect(worst.map((s) => s.symbol)).toEqual(['F', 'E', 'D']);
  });

  it('returns empty lists, not a crash, when every symbol is unranked', () => {
    const { top, worst, unrankedCount } = rankBreakoutsByRate([summary('A', null), summary('B', null)]);
    expect(top).toEqual([]);
    expect(worst).toEqual([]);
    expect(unrankedCount).toBe(2);
  });
});

describe('rankTrendByComposite', () => {
  it('excludes null-composite symbols from the ranking', () => {
    const rows = [trendRow('HIGH', 0.9), trendRow('LOW', 0.2), trendRow('NUL', null)];
    const { top, unrankedCount } = rankTrendByComposite(rows, 8);
    expect(unrankedCount).toBe(1);
    expect(top.map((r) => r.symbol)).toEqual(['HIGH', 'LOW']);
  });
});

describe('continuationRegimeRows', () => {
  it('keeps only rows whose verdict is the genuine continuation call', () => {
    const rows = [
      regimeRow('AAA', 'continuation'),
      regimeRow('BBB', 'mixed'),
      regimeRow('CCC', 'fade'),
      regimeRow('DDD', null, true), // stale -- null verdict, must not appear
      regimeRow('EEE', null, false), // noise-dominated -- null verdict, must not appear
    ];
    const result = continuationRegimeRows(rows);
    expect(result.map((r) => r.symbol)).toEqual(['AAA']);
  });
});

describe('link builders', () => {
  it('scanBreakoutsHref carries the sort/dir that reproduces the block\'s own ranking', () => {
    expect(scanBreakoutsHref('rate', 'desc')).toBe('/scan?view=breakouts&sort=rate&dir=desc');
    expect(scanBreakoutsHref('rate', 'asc')).toBe('/scan?view=breakouts&sort=rate&dir=asc');
    expect(scanBreakoutsHref(null)).toBe('/scan?view=breakouts');
  });

  it('scanTrendHref carries the trend view and composite sort', () => {
    expect(scanTrendHref()).toBe('/scan?view=trend&sort=composite&dir=desc');
  });

  it('REGIME_HREF points at the regime board', () => {
    expect(REGIME_HREF).toBe('/regime');
  });
});
