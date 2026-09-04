/**
 * MSW request handlers backing every T11 endpoint against the static fixtures in
 * `./fixtures/`, so the whole frontend (this shell, and T13-T16's charts) can be built and
 * tested before the real API exists. Matched with a `*` origin wildcard so this works
 * regardless of what `VITE_API_BASE_URL` resolves to (dev, test, or a future preview env).
 *
 * These handlers do NOT reimplement the GEX engine (T08) — they read pre-computed fixture
 * JSON and apply a crude scale factor per expiry filter so switching filters visibly
 * changes the numbers during development. The real per-filter aggregation only exists once
 * T11 is live; treat any filter-dependent number from these mocks as illustrative, not
 * correct.
 */
import { http, HttpResponse } from 'msw';
import type { ChainResponse, ExpiryFilter, GexResult, LevelHistoryRow, SnapshotSummary, Underlying } from '../api/types';
import gexSpxFixture from './fixtures/gex-spx.json';
import gexSpyFixture from './fixtures/gex-spy.json';
import gexQqqFixture from './fixtures/gex-qqq.json';
import gexSpxZeroDteFixture from './fixtures/gex-spx-zero-dte.json';
import gexSpyZeroDteFixture from './fixtures/gex-spy-zero-dte.json';
import gexQqqZeroDteFixture from './fixtures/gex-qqq-zero-dte.json';
import snapshotsFixture from './fixtures/snapshots.json';
import levelsHistoryFixture from './fixtures/levels-history.json';
import chainLatestFixture from './fixtures/chain-latest.json';

const GEX_BY_UNDERLYING: Record<Underlying, GexResult> = {
  SPX: gexSpxFixture as GexResult,
  SPY: gexSpyFixture as GexResult,
  QQQ: gexQqqFixture as GexResult,
};

// The real EOD capture runs at 16:20 ET, after every same-day contract has expired, so
// `filter=ZERO_DTE` returns null walls/spot/max-strike every evening -- the daily case, not
// an edge case (see api/types.ts's `KeyLevels` docstring). The generic `scaled()` helper
// below cannot produce this shape (it just multiplies numbers by a scale factor), so
// ZERO_DTE gets its own dedicated fixture instead of going through that path.
const GEX_ZERO_DTE_BY_UNDERLYING: Record<Underlying, GexResult> = {
  SPX: gexSpxZeroDteFixture as GexResult,
  SPY: gexSpyZeroDteFixture as GexResult,
  QQQ: gexQqqZeroDteFixture as GexResult,
};

const FILTER_SCALE: Record<ExpiryFilter, number> = {
  ALL: 1,
  ZERO_DTE: 0.22,
  EX_ZERO_DTE: 0.78,
  THIS_WEEK: 0.4,
  MONTHLY_ONLY: 0.6,
};

function isUnderlying(value: string): value is Underlying {
  return value === 'SPX' || value === 'SPY' || value === 'QQQ';
}

function isExpiryFilter(value: string | null): value is ExpiryFilter {
  return value === 'ALL' || value === 'ZERO_DTE' || value === 'EX_ZERO_DTE' || value === 'THIS_WEEK' || value === 'MONTHLY_ONLY';
}

function scaled(base: GexResult, filter: ExpiryFilter): GexResult {
  const scale = FILTER_SCALE[filter];
  return {
    ...base,
    filter,
    levels: { ...base.levels, net_gex: Math.round(base.levels.net_gex * scale) },
    by_strike: base.by_strike.map((row) => ({
      ...row,
      call_gex: Math.round(row.call_gex * scale),
      put_gex: Math.round(row.put_gex * scale),
      net_gex: Math.round(row.net_gex * scale),
    })),
    by_expiry: base.by_expiry.map((row) => ({
      ...row,
      net_gex: Math.round(row.net_gex * scale),
      call_gex: Math.round(row.call_gex * scale),
      put_gex: Math.round(row.put_gex * scale),
    })),
    profile: base.profile.map((point) => ({ ...point, total_gex: Math.round(point.total_gex * scale) })),
  };
}

const notFound = (detail: string) => HttpResponse.json({ detail }, { status: 404 });

export const handlers = [
  http.get('*/api/gex/:underlying/latest', ({ params, request }) => {
    const underlying = String(params.underlying).toUpperCase();
    if (!isUnderlying(underlying)) return notFound(`unknown underlying ${underlying}`);
    const filterParam = new URL(request.url).searchParams.get('filter');
    const filter = isExpiryFilter(filterParam) ? filterParam : 'ALL';
    if (filter === 'ZERO_DTE') return HttpResponse.json(GEX_ZERO_DTE_BY_UNDERLYING[underlying]);
    return HttpResponse.json(scaled(GEX_BY_UNDERLYING[underlying], filter));
  }),

  http.get('*/api/gex/:underlying/snapshots/:snapshotId', ({ params, request }) => {
    const underlying = String(params.underlying).toUpperCase();
    if (!isUnderlying(underlying)) return notFound(`unknown underlying ${underlying}`);
    const filterParam = new URL(request.url).searchParams.get('filter');
    const filter = isExpiryFilter(filterParam) ? filterParam : 'ALL';
    const result = filter === 'ZERO_DTE' ? GEX_ZERO_DTE_BY_UNDERLYING[underlying] : scaled(GEX_BY_UNDERLYING[underlying], filter);
    const snapshotId = Number(params.snapshotId);
    return HttpResponse.json({
      ...result,
      snapshot: { ...result.snapshot, id: Number.isFinite(snapshotId) ? snapshotId : result.snapshot.id, is_eod: true },
    });
  }),

  http.get('*/api/gex/:underlying/levels/history', ({ params, request }) => {
    const underlying = String(params.underlying).toUpperCase();
    if (!isUnderlying(underlying)) return notFound(`unknown underlying ${underlying}`);
    const url = new URL(request.url);
    const start = url.searchParams.get('start');
    const end = url.searchParams.get('end');
    const eodOnly = url.searchParams.get('eod_only') !== 'false';

    // The fixture rows carry an `underlying` tag purely so this mock can filter by it; the
    // real endpoint doesn't need one since `underlying` is a path param there.
    const rows = (levelsHistoryFixture as (LevelHistoryRow & { underlying: Underlying })[]).filter((row) => {
      if (row.underlying !== underlying) return false;
      if (eodOnly && !row.is_eod) return false;
      if (start && row.captured_at < start) return false;
      if (end && row.captured_at > end) return false;
      return true;
    });
    return HttpResponse.json(rows satisfies LevelHistoryRow[]);
  }),

  http.get('*/api/chains/:underlying/latest', ({ params, request }) => {
    const underlying = String(params.underlying).toUpperCase();
    if (!isUnderlying(underlying)) return notFound(`unknown underlying ${underlying}`);
    // Only one fixture chain exists (SPX, 2026-09-18); return it regardless of the
    // requested expiry/underlying so every consumer at least gets a shape to render.
    const expiry = new URL(request.url).searchParams.get('expiry');
    const fixture = chainLatestFixture as ChainResponse;
    return HttpResponse.json({ ...fixture, underlying, expiry: expiry ?? fixture.expiry });
  }),

  http.get('*/api/snapshots', ({ request }) => {
    const url = new URL(request.url);
    const underlyingParam = url.searchParams.get('underlying');
    const limit = Number(url.searchParams.get('limit') ?? '30');
    let rows = snapshotsFixture as SnapshotSummary[];
    if (underlyingParam && isUnderlying(underlyingParam)) {
      rows = rows.filter((row) => row.underlying === underlyingParam);
    }
    return HttpResponse.json(rows.slice(0, limit));
  }),
];
