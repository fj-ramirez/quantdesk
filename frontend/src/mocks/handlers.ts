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
import { CFD_INSTRUMENTS, CORE_UNDERLYINGS, EXTENDED_UNDERLYINGS } from '../api/types';
import type {
  Bar,
  BreakoutsResponse,
  CaptureHealth,
  CfdLevel,
  CfdPlaybookEntry,
  CfdPremiumCandidate,
  CfdTranslation,
  ChainResponse,
  ExpiryFilter,
  GexResult,
  LevelHistoryRow,
  Report,
  RotationResponse,
  SnapshotSummary,
  SymbolBreakoutsResponse,
  SymbolTrendResponse,
  TrendResponse,
  Underlying,
  UniverseResponse,
} from '../api/types';
import gexSpxFixture from './fixtures/gex-spx.json';
import gexSpyFixture from './fixtures/gex-spy.json';
import gexQqqFixture from './fixtures/gex-qqq.json';
import gexGldFixture from './fixtures/gex-gld.json';
import gexDiaFixture from './fixtures/gex-dia.json';
import gexSpxZeroDteFixture from './fixtures/gex-spx-zero-dte.json';
import gexSpyZeroDteFixture from './fixtures/gex-spy-zero-dte.json';
import gexQqqZeroDteFixture from './fixtures/gex-qqq-zero-dte.json';
import gexGldZeroDteFixture from './fixtures/gex-gld-zero-dte.json';
import gexDiaZeroDteFixture from './fixtures/gex-dia-zero-dte.json';
import snapshotsFixture from './fixtures/snapshots.json';
import levelsHistoryFixture from './fixtures/levels-history.json';
import chainLatestFixture from './fixtures/chain-latest.json';
import reportSpxFixture from './fixtures/report-spx.json';
import reportSpyFixture from './fixtures/report-spy.json';
import reportQqqFixture from './fixtures/report-qqq.json';
import reportGldFixture from './fixtures/report-gld.json';
import reportDiaFixture from './fixtures/report-dia.json';
import reportSpxZeroDteFixture from './fixtures/report-spx-zero-dte.json';
import reportSpyZeroDteFixture from './fixtures/report-spy-zero-dte.json';
import reportQqqZeroDteFixture from './fixtures/report-qqq-zero-dte.json';
import reportGldZeroDteFixture from './fixtures/report-gld-zero-dte.json';
import reportDiaZeroDteFixture from './fixtures/report-dia-zero-dte.json';
import breakoutsFixture from './fixtures/scan/breakouts.json';
import breakoutsSpyFixture from './fixtures/scan/breakouts_SPY.json';
import trendFixture from './fixtures/scan/trend.json';
import trendSpyFixture from './fixtures/scan/trend_SPY.json';
import barsSpyFixture from './fixtures/scan/bars_SPY.json';
import universeFixture from './fixtures/scan/universe.json';
import healthCaptureFixture from './fixtures/scan/health_capture.json';
import rotationSectorsFixture from './fixtures/scan/rotation_sectors.json';
import rotationIndustriesRspFixture from './fixtures/scan/rotation_industries_rsp.json';
import rotationAssetsFixture from './fixtures/scan/rotation_assets.json';

// T47 added 23 more `Underlying` members (sector/industry ETFs), none of which has a mock
// fixture -- these handlers were built and are tested against exactly the original five, and
// growing 23 more fixture pairs is out of scope for a capture-pipeline task. `MockedUnderlying`
// is the honest type for what these handlers actually serve; a request for an extended symbol
// falls through `isUnderlying` below the same way a request for any other never-mocked value
// would, and gets a 404. The real backend has no such gap once a symbol is captured -- this is
// a mock-data limitation, not a product one.
type MockedUnderlying = 'SPX' | 'SPY' | 'QQQ' | 'GLD' | 'DIA';

const GEX_BY_UNDERLYING: Record<MockedUnderlying, GexResult> = {
  SPX: gexSpxFixture as GexResult,
  SPY: gexSpyFixture as GexResult,
  QQQ: gexQqqFixture as GexResult,
  GLD: gexGldFixture as GexResult,
  DIA: gexDiaFixture as GexResult,
};

// The real EOD capture runs at 16:20 ET, after every same-day contract has expired, so
// `filter=ZERO_DTE` returns null walls/spot/max-strike every evening -- the daily case, not
// an edge case (see api/types.ts's `KeyLevels` docstring). The generic `scaled()` helper
// below cannot produce this shape (it just multiplies numbers by a scale factor), so
// ZERO_DTE gets its own dedicated fixture instead of going through that path.
const GEX_ZERO_DTE_BY_UNDERLYING: Record<MockedUnderlying, GexResult> = {
  SPX: gexSpxZeroDteFixture as GexResult,
  SPY: gexSpyZeroDteFixture as GexResult,
  QQQ: gexQqqZeroDteFixture as GexResult,
  GLD: gexGldZeroDteFixture as GexResult,
  DIA: gexDiaZeroDteFixture as GexResult,
};

const FILTER_SCALE: Record<ExpiryFilter, number> = {
  ALL: 1,
  ZERO_DTE: 0.22,
  EX_ZERO_DTE: 0.78,
  THIS_WEEK: 0.4,
  MONTHLY_ONLY: 0.6,
};

function isUnderlying(value: string): value is MockedUnderlying {
  return value === 'SPX' || value === 'SPY' || value === 'QQQ' || value === 'GLD' || value === 'DIA';
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

// ---------------------------------------------------------------------------------------
// Report (T39/T40)
//
// Unlike the GEX handlers above, these fixtures are NOT approximations. Each one is the real
// `GET /api/report/{underlying}` response body, produced by running a real Cboe chain through
// the real provider, engine and report module and serializing it through the same `ReportOut`
// Pydantic model the endpoint returns. So the numbers on the report page under MSW are the
// numbers the live API would send for that chain, and the shape — every nullable field, the
// "Z" datetime format — is the wire shape rather than a hand-written guess at it.
//
// GLD, SPY and DIA came from the full live 2026-09-05 captures, so they carry the real
// figures: GLD reads +42.7 % net/gross (LONG GAMMA) with a 0.45 put/call ratio, and **DIA
// reads NOISE-DOMINATED at 0.9 %**, which is the case T40 has to render visibly and which a
// trimmed chain does not reproduce. SPX and QQQ came from the committed trimmed test
// fixtures, since no full capture of those exists.
//
// The one thing still faked here is the *filter* dimension: only `ALL` and `ZERO_DTE` have
// fixtures, and any other filter falls back to the `ALL` body with its `filter` field
// rewritten. Treat a filter-dependent number under any other filter as illustrative.
// ---------------------------------------------------------------------------------------

const REPORT_BY_UNDERLYING: Record<MockedUnderlying, Report> = {
  SPX: reportSpxFixture as unknown as Report,
  SPY: reportSpyFixture as unknown as Report,
  QQQ: reportQqqFixture as unknown as Report,
  GLD: reportGldFixture as unknown as Report,
  DIA: reportDiaFixture as unknown as Report,
};

const REPORT_ZERO_DTE_BY_UNDERLYING: Record<MockedUnderlying, Report> = {
  SPX: reportSpxZeroDteFixture as unknown as Report,
  SPY: reportSpyZeroDteFixture as unknown as Report,
  QQQ: reportQqqZeroDteFixture as unknown as Report,
  GLD: reportGldZeroDteFixture as unknown as Report,
  DIA: reportDiaZeroDteFixture as unknown as Report,
};

function reportFor(underlying: MockedUnderlying, filter: ExpiryFilter): Report {
  const base = filter === 'ZERO_DTE' ? REPORT_ZERO_DTE_BY_UNDERLYING[underlying] : REPORT_BY_UNDERLYING[underlying];
  // None of the committed fixtures were captured with a CFD spot attached (T41 postdates
  // them), so the base fixture always carries `cfd: null` -- `translateToCfd` below is what
  // attaches a converted block when the request actually asks for one.
  return { ...base, filter, cfd: null };
}

/** Mirrors `app.gex.report.translate_to_cfd` (T41) closely enough to exercise the report
 * page's CFD UI end to end under MSW. NOT the real conversion -- the live API's own
 * `app/gex/report.py` is the source of truth; this only has to reproduce the same *shape* and
 * the same pure scaling (`level * ratio`, `distance_pct` passed through unchanged) so a test
 * asserting the percentage invariant against these mocks is asserting something real. Returns
 * `null` exactly when the real endpoint would leave `ReportOut.cfd` `null`: no spot, a
 * non-positive/non-finite one, or (T47) an underlying `CFD_INSTRUMENTS` has no entry for --
 * every sector/industry ETF today. That last case mirrors `app/api/report.py`'s degrade: no
 * broker mapping means `cfd` stays `null`, not a block with an `undefined` instrument name. */
function translateToCfd(report: Report, cfdSpot: number | null): CfdTranslation | null {
  if (cfdSpot == null || !Number.isFinite(cfdSpot) || cfdSpot <= 0) return null;
  const instrument = CFD_INSTRUMENTS[report.underlying];
  if (instrument === undefined) return null;
  const ratio = cfdSpot / report.spot;

  function level(side: string, nativeStrike: number | null, distancePct: number | null = null): CfdLevel | null {
    if (nativeStrike == null) return null;
    return { side, native_strike: nativeStrike, strike: nativeStrike * ratio, distance_pct: distancePct };
  }

  function levels(rows: { strike: number | null; side: string; distance_pct: number | null }[]): CfdLevel[] {
    return rows
      .map((row) => level(row.side, row.strike, row.distance_pct))
      .filter((row): row is CfdLevel => row !== null);
  }

  const playbook: CfdPlaybookEntry[] = report.playbook.entries.map((entry) => ({
    key: entry.key,
    trigger: entry.trigger == null ? null : entry.trigger * ratio,
    target: entry.target == null ? null : entry.target * ratio,
    invalidation: entry.invalidation == null ? null : entry.invalidation * ratio,
  }));

  function candidates(rows: { occ_symbol: string; strike: number | null }[]): CfdPremiumCandidate[] {
    return rows.map((row) => ({ occ_symbol: row.occ_symbol, strike: row.strike == null ? null : row.strike * ratio }));
  }

  return {
    underlying: report.underlying,
    instrument,
    cfd_spot: cfdSpot,
    underlying_spot: report.spot,
    ratio,
    call_wall: level('CALL_WALL', report.levels.call_wall),
    put_wall: level('PUT_WALL', report.levels.put_wall),
    flip_point: level('FLIP_POINT', report.levels.flip_point),
    max_pain: level('MAX_PAIN', report.max_pain.strike, report.max_pain.distance_pct),
    resistance: levels(report.levels.resistance),
    support: levels(report.levels.support),
    straddling: levels(report.levels.straddling),
    playbook,
    premium_calls: candidates(report.premium.calls),
    premium_puts: candidates(report.premium.puts),
    note:
      `These are ${report.underlying} option levels expressed in ${instrument} terms via a snapshot ratio ` +
      `(${instrument} ${cfdSpot.toFixed(2)} / ${report.underlying} ${report.spot.toFixed(2)} = ${ratio.toFixed(6)}) -- ` +
      'not levels with their own gamma, because there is no dealer gamma in a CFD.',
  };
}

/** Mirrors `app.gex.report.render_text` closely enough for the panel to be exercised, but is
 * NOT that renderer — reimplementing it in TypeScript is exactly the drift the real endpoint
 * avoids by serving the backend's own output. Only the headings and the honesty strings are
 * reproduced, because those are what the tests assert on. */
function renderReportText(report: Report): string {
  const rule = '='.repeat(78);
  const lines = [
    rule,
    `${report.underlying} OPTIONS INTELLIGENCE`,
    rule,
    `Filter:          ${report.filter}`,
    `Current price:   ${report.spot.toFixed(2)}`,
    `Max pain:        ${report.max_pain.strike ?? '--'}`,
    `IV regime:       ${report.iv_regime.label ?? `insufficient history (${report.iv_regime.history_observations} of ${report.iv_regime.min_history_required} prior observations needed)`}`,
    '',
    'DEALER POSITIONING',
    `Status: ${report.positioning.label}`,
    report.positioning.description,
    '',
    'GAMMA EXPOSURE LANDSCAPE',
    ...report.levels.resistance.map((level, i) => `  ${i + 1}. ${level.strike} (resistance)`),
    ...report.levels.support.map((level, i) => `  ${i + 1}. ${level.strike} (support)`),
    '',
    ...(report.cfd
      ? [
          `${report.cfd.instrument} TRANSLATION`,
          `Anchor: ${report.cfd.instrument} ${report.cfd.cfd_spot.toFixed(2)} / ${report.underlying} ${report.cfd.underlying_spot.toFixed(2)} = ratio ${report.cfd.ratio.toFixed(6)}`,
          report.cfd.note,
          '',
        ]
      : []),
    'MARKET SENTIMENT',
    `P/C ratio (open interest): ${report.ratios.open_interest_ratio?.toFixed(2) ?? '--'}`,
    '',
    'PREMIUM SELLING SCREEN',
    'Screening output computed from the current chain, not a recommendation.',
    '',
    'PLAYBOOK',
    ...report.playbook.entries.map((entry, i) => {
      const cfdEntry = report.cfd?.playbook[i];
      const base = `${entry.name}: trigger ${entry.trigger ?? '--'}`;
      return cfdEntry ? `${base}  [${report.cfd!.instrument} ${cfdEntry.trigger?.toFixed(2) ?? '--'}]` : base;
    }),
    '',
    'RISK ALERTS',
    ...report.alerts.map((alert) => `[${alert.severity}] ${alert.code}`),
    '',
    'EXECUTIVE SUMMARY',
    ...report.summary,
  ];
  return `${lines.join('\n')}\n`;
}

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

  http.get('*/api/report/:underlying', ({ params, request }) => {
    const underlying = String(params.underlying).toUpperCase();
    if (!isUnderlying(underlying)) return notFound(`unsupported underlying '${underlying}'`);
    const url = new URL(request.url);
    const filterParam = url.searchParams.get('filter');
    const filter = isExpiryFilter(filterParam) ? filterParam : 'ALL';
    const base = reportFor(underlying, filter);

    // T41: a zero, negative or non-numeric `cfd_spot` is rejected with 422, matching the real
    // endpoint's `Query(..., gt=0)` -- never silently ignored or turned into an infinity.
    const cfdSpotParam = url.searchParams.get('cfd_spot');
    let cfdSpot: number | null = null;
    if (cfdSpotParam !== null) {
      const parsed = Number(cfdSpotParam);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        return HttpResponse.json({ detail: `cfd_spot must be a positive number, got '${cfdSpotParam}'` }, { status: 422 });
      }
      cfdSpot = parsed;
    }
    const report: Report = { ...base, cfd: translateToCfd(base, cfdSpot) };

    // `format=text` returns the rendered report as plain text, exactly as the real endpoint
    // does -- the collapsible panel fetches this rather than reassembling it client-side.
    if (url.searchParams.get('format') === 'text') {
      return HttpResponse.text(renderReportText(report));
    }
    return HttpResponse.json(report);
  }),

  http.post('*/api/snapshots/capture', ({ request }) => {
    // T37's "Capture now" affordance. The real endpoint returns 201 with the new snapshot row.
    const underlying = new URL(request.url).searchParams.get('underlying') ?? 'SPX';
    return HttpResponse.json(
      { id: 999, underlying, captured_at: new Date().toISOString(), source: 'cboe', spot: 0, contract_count: 0, is_eod: false },
      { status: 201 },
    );
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

  // ---------------------------------------------------------------------------------------
  // T55: scan (T43 breakouts, T45 trend), bars (T42), universe (T42), capture health's `bars`
  // block, and `/api/symbols`. `breakouts.json`/`trend.json` are the live, unedited universe
  // response (47 symbols) -- see `fixtures/scan/README.md` for exactly what was hand-edited
  // and why. Only `SPY` has a dedicated per-symbol fixture; every other symbol gets the same
  // clean-empty-result contract the real backend gives a symbol with no stored bars (never a
  // 404), which also exercises `^VIX`'s percent-encoding round trip without needing a VIX-
  // shaped fixture.
  // ---------------------------------------------------------------------------------------

  http.get('*/api/scan/breakouts', ({ request }) => {
    const url = new URL(request.url);
    const base = breakoutsFixture as BreakoutsResponse;
    const n = Number(url.searchParams.get('n') ?? base.n);
    const k = Number(url.searchParams.get('k') ?? base.k);
    const lookback = Number(url.searchParams.get('lookback') ?? base.lookback);
    return HttpResponse.json({ ...base, n, k, lookback });
  }),

  http.get('*/api/scan/breakouts/:symbol', ({ params, request }) => {
    const symbol = decodeURIComponent(String(params.symbol)).trim().toUpperCase();
    const url = new URL(request.url);
    const spyBase = breakoutsSpyFixture as SymbolBreakoutsResponse;
    const n = Number(url.searchParams.get('n') ?? spyBase.n);
    const k = Number(url.searchParams.get('k') ?? spyBase.k);
    const lookback = Number(url.searchParams.get('lookback') ?? spyBase.lookback);
    if (symbol === 'SPY') {
      return HttpResponse.json({ ...spyBase, n, k, lookback });
    }
    // Mirrors `app/api/scan.py`'s "never 404s" contract: a symbol with no stored bars
    // returns a clean, empty events list.
    return HttpResponse.json({ symbol, n, k, lookback, events: [] } satisfies SymbolBreakoutsResponse);
  }),

  http.get('*/api/scan/trend', () => HttpResponse.json(trendFixture as TrendResponse)),

  http.get('*/api/scan/trend/:symbol', ({ params }) => {
    const symbol = decodeURIComponent(String(params.symbol)).trim().toUpperCase();
    if (symbol === 'SPY') {
      return HttpResponse.json(trendSpyFixture as SymbolTrendResponse);
    }
    return HttpResponse.json({
      symbol,
      current: {
        adx14: null,
        er20: null,
        chop14: null,
        vr: null,
        vr_z: null,
        rv20: null,
        iv30: null,
        iv_rv_ratio: null,
      },
      history: [],
    } satisfies SymbolTrendResponse);
  }),

  http.get('*/api/bars/:symbol', ({ params }) => {
    // `params.symbol` arrives already percent-decoded (MSW/path-to-regexp decode a route
    // param before handing it to the handler) -- `^VIX` and `%5EVIX` both land here as the
    // same string, mirroring `app.api.bars.get_bars`'s own normalization.
    const symbol = decodeURIComponent(String(params.symbol)).trim().toUpperCase();
    if (symbol === 'SPY') {
      return HttpResponse.json(barsSpyFixture as Bar[]);
    }
    return HttpResponse.json([] satisfies Bar[]);
  }),

  http.get('*/api/universe', () => HttpResponse.json(universeFixture as UniverseResponse)),

  // T51: `/rotation`. Three fixtures, one per `(group, benchmark)` combination the page and
  // its tests actually exercise -- see `fixtures/scan/README.md`'s "Rotation fixtures"
  // section. Selection is by `group` alone (each group was only ever recorded against one
  // benchmark), which is enough for the deep-link and pinned-crosshair acceptance cases; the
  // response's own `group`/`benchmark`/`weeks` fields are the live-recorded values, not
  // echoed from the request, so the fixture's data never mismatches the trail length it
  // actually contains (same "illustrative, not correctness reference" caveat this file's own
  // docstring already states for filter-dependent numbers elsewhere).
  http.get('*/api/scan/rotation', ({ request }) => {
    const url = new URL(request.url);
    const group = url.searchParams.get('group');
    if (group === 'industries') {
      return HttpResponse.json(rotationIndustriesRspFixture as RotationResponse);
    }
    if (group === 'assets') {
      return HttpResponse.json(rotationAssetsFixture as RotationResponse);
    }
    return HttpResponse.json(rotationSectorsFixture as RotationResponse);
  }),

  http.get('*/api/health/capture', () => HttpResponse.json(healthCaptureFixture as CaptureHealth)),

  http.get('*/api/symbols', () =>
    HttpResponse.json({ core: [...CORE_UNDERLYINGS], extended: [...EXTENDED_UNDERLYINGS] }),
  ),
];
