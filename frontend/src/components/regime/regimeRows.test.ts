import { describe, expect, it } from 'vitest';
import { REGIME_DEFAULT_SORT, rowTintClassFor, toRegimeRows, verdictGroupOf } from './regimeRows';
import type { RegimeSymbolRow } from '../../api/types';
import regimeFixture from '../../mocks/fixtures/scan/regime.json';
import regimeZeroDteFixture from '../../mocks/fixtures/scan/regime_zero_dte.json';

const rows = regimeFixture.rows as unknown as RegimeSymbolRow[];
const zeroDteRows = regimeZeroDteFixture.rows as unknown as RegimeSymbolRow[];

function findRow(symbol: string): RegimeSymbolRow {
  const row = rows.find((r) => r.underlying === symbol);
  if (!row) throw new Error(`fixture has no row for ${symbol}`);
  return row;
}

describe('verdictGroupOf', () => {
  it('groups a genuine verdict by that verdict', () => {
    expect(verdictGroupOf(findRow('SPY'))).toBe('continuation'); // live fixture fact
    expect(verdictGroupOf(findRow('XLE'))).toBe('fade');
  });

  it('groups a stale row as stale even though the API also reports it noise-dominated', () => {
    // XLRE is stale in the ALL fixture; in the ZERO_DTE fixture every row (stale ones
    // included) is ALSO `noise_dominated: true` -- the one live case that actually exercises
    // "stale wins" (see mocks/fixtures/scan/README.md's "Regime fixtures" note).
    const xlre = zeroDteRows.find((r) => r.underlying === 'XLRE')!;
    expect(xlre.stale).toBe(true);
    expect(xlre.positioning?.noise_dominated).toBe(true);
    expect(verdictGroupOf(xlre)).toBe('stale');
  });

  it('groups a fresh, null-verdict, noise-dominated row as noise-dominated', () => {
    const xlk = findRow('XLK');
    expect(xlk.stale).toBe(false);
    expect(xlk.positioning?.noise_dominated).toBe(true);
    expect(verdictGroupOf(xlk)).toBe('noise-dominated');
  });
});

describe('toRegimeRows', () => {
  it('builds one row per API row, carrying reasons verbatim', () => {
    const built = toRegimeRows(rows);
    expect(built).toHaveLength(rows.length);
    const spy = built.find((r) => r.symbol === 'SPY')!;
    expect(spy.reasons).toEqual(findRow('SPY').reasons);
  });

  it('sorts by defaultSortKey (descending) into continuation, mixed, fade, noise-dominated, stale order', () => {
    const built = toRegimeRows(rows).sort((a, b) => b.defaultSortKey - a.defaultSortKey);
    const groupOrder = built.map((r) => r.verdictGroup);
    const firstNoise = groupOrder.indexOf('noise-dominated');
    const firstStale = groupOrder.indexOf('stale');
    const lastFade = groupOrder.lastIndexOf('fade');
    const lastMixed = groupOrder.lastIndexOf('mixed');
    const lastContinuation = groupOrder.lastIndexOf('continuation');
    // Every continuation row precedes every mixed row, every mixed row precedes every fade
    // row, every fade row precedes every noise-dominated row, and every noise-dominated row
    // precedes every stale row -- regardless of each row's own net/abs ratio.
    expect(lastContinuation).toBeLessThan(groupOrder.indexOf('mixed'));
    expect(lastMixed).toBeLessThan(groupOrder.indexOf('fade'));
    expect(lastFade).toBeLessThan(firstNoise);
    expect(groupOrder.lastIndexOf('noise-dominated')).toBeLessThan(firstStale);
  });

  it('picks the wall in the direction of the 5-day move for roomBeyond, or null with no wall on that side', () => {
    const built = toRegimeRows(rows);
    const spy = built.find((r) => r.symbol === 'SPY')!;
    const spyApi = findRow('SPY');
    expect(spyApi.return_5d).toBeGreaterThanOrEqual(0);
    expect(spy.roomBeyond).toEqual({ strike: spyApi.wall_above!.room_beyond_strike, direction: 'up' });

    const xlre = built.find((r) => r.symbol === 'XLRE')!;
    const xlreApi = findRow('XLRE');
    expect(xlreApi.wall_below).toBeNull();
    expect(xlreApi.return_5d).toBeLessThan(0);
    expect(xlre.roomBeyond).toBeNull();
  });

  it('leaves ratio null (never coerced to 0) when the API has no ratio, e.g. every ZERO_DTE row', () => {
    const built = toRegimeRows(zeroDteRows);
    expect(built.every((r) => r.ratio === null)).toBe(true);
    // defaultSortKey still resolves to a finite number (the `ratio ?? 0` fallback), so a
    // fully-null-ratio universe still sorts deterministically rather than producing NaN.
    expect(built.every((r) => Number.isFinite(r.defaultSortKey))).toBe(true);
  });
});

describe('rowTintClassFor', () => {
  it('tints stale and noise-dominated rows distinctly, and leaves a genuine verdict untouched', () => {
    expect(rowTintClassFor('stale')).toContain('regime-row-tint--stale');
    expect(rowTintClassFor('noise-dominated')).toContain('regime-row-tint--noise');
    expect(rowTintClassFor('stale')).not.toBe(rowTintClassFor('noise-dominated'));
    expect(rowTintClassFor('continuation')).toBeUndefined();
    expect(rowTintClassFor('mixed')).toBeUndefined();
    expect(rowTintClassFor('fade')).toBeUndefined();
  });
});

describe('REGIME_DEFAULT_SORT', () => {
  it('names a real field every row carries', () => {
    const [row] = toRegimeRows(rows);
    expect(REGIME_DEFAULT_SORT).toBe('defaultSortKey');
    expect(row).toHaveProperty(REGIME_DEFAULT_SORT);
  });
});

describe('a symbol with no snapshot captured yet', () => {
  /** Exactly what `RegimeRowOut.missing` (backend `app/api/scan.py`) serializes: every
   * GEX-derived field null, `stale: false` because there is no chain whose age could exceed
   * the threshold, and the sentence the server puts in `reasons`.
   *
   * Captured verbatim from `GET /api/scan/regime` against a freshly-migrated, empty database
   * on 2026-09-19 -- the state of every first deployment, before any 16:20 capture has run.
   * This shape previously crashed the whole regime board: the wire types declared
   * `positioning` non-nullable, so `toRegimeRows` read `row.positioning.ratio` straight
   * through and threw "Cannot read properties of null (reading 'ratio')" inside the page's
   * `useMemo`, taking the route down with it. */
  const missing: RegimeSymbolRow = {
    underlying: 'SPX',
    filter: 'ALL',
    spot: null,
    atr14: null,
    iv30: null,
    rv20: null,
    iv_rv_ratio: null,
    return_5d: null,
    as_of: null,
    effective_at: null,
    chain_age_minutes: null,
    stale: false,
    positioning: null,
    flip_point: null,
    flip_distance: null,
    flip_distance_pct: null,
    flip_distance_atr: null,
    wall_below: null,
    wall_above: null,
    zero_dte_share: null,
    verdict: null,
    reasons: ['no snapshot captured yet for this symbol'],
    trend_pct: null,
  };

  it('shapes the row instead of throwing', () => {
    expect(() => toRegimeRows([missing])).not.toThrow();
    const [row] = toRegimeRows([missing]);
    expect(row.symbol).toBe('SPX');
    expect(row.ratio).toBeNull();
    expect(row.netGex).toBeNull();
    expect(row.spot).toBeNull();
  });

  it('groups as no-data, not as noise-dominated', () => {
    // The distinction is the point: "we measured and the signal was too small to trust" and
    // "we never measured" are different facts, and the fallback in `verdictGroupOf` would
    // otherwise report the first when the second is true.
    expect(verdictGroupOf(missing)).toBe('no-data');
    expect(toRegimeRows([missing])[0].noiseDominated).toBe(false);
  });

  it('sorts below every row that has data, stale ones included', () => {
    const withData = toRegimeRows(rows);
    const [noData] = toRegimeRows([missing]);
    const lowest = Math.min(...withData.map((r) => r.defaultSortKey));
    // ScanTable sorts this key descending, so "last" means strictly smallest.
    expect(noData.defaultSortKey).toBeLessThan(lowest);
  });

  it('carries the server reason through verbatim for the table to render', () => {
    expect(toRegimeRows([missing])[0].reasons).toEqual([
      'no snapshot captured yet for this symbol',
    ]);
  });

  it('is tinted, so it never reads as a plain verdict row', () => {
    expect(rowTintClassFor('no-data')).toBeTruthy();
  });
});
