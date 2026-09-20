/**
 * MSW handlers for `/api/terminal/*` (T80).
 *
 * **The fixtures honour `as_of`** rather than returning the same payload whatever is asked. That
 * is deliberate: the as-of control is this module's entire product, and a mock that ignored it
 * would let a broken control look perfectly fine in development and in every test. So the board
 * below returns fewer scored rows at an early `as_of`, and the edges return none before the
 * night they were estimated — which is exactly how the real data behaves.
 */
import { http, HttpResponse } from 'msw';
import type {
  BoardResponse,
  BriefResponse,
  EdgesResponse,
  PolicyResponse,
  RegimeResponse,
  TerminalSeries,
} from '../api/types';

/** Edges were first estimated on this date in the fixture, mirroring the real store. */
const EDGES_ESTIMATED_FROM = new Date('2026-09-13T02:07:20Z');

/** Before this, the fixture's slower series have no vintage yet. */
const LATE_SERIES_FROM = new Date('2024-01-01T00:00:00Z');

function asOfFrom(request: Request): Date {
  const value = new URL(request.url).searchParams.get('as_of');
  return value ? new Date(value) : new Date();
}

const SCORED = [
  { series_id: 'ust_cc.3m', display_name: '3m T-bill', asset_class: 'rates', change: 7, z: 3.22, percentile: 98.8, vol_flag: null, stale_days: 1 },
  { series_id: 'ust.5y.nominal', display_name: '5y nominal', asset_class: 'rates', change: 14, z: 3.05, percentile: 100, vol_flag: 'compressed', stale_days: 2 },
  { series_id: 'ust.10y.nominal', display_name: '10y nominal', asset_class: 'rates', change: 12, z: 2.92, percentile: 99.6, vol_flag: 'compressed', stale_days: 2 },
  { series_id: 'spread.2s10s', display_name: '2s10s', asset_class: 'rates', change: -6, z: -2.28, percentile: 1.6, vol_flag: 'compressed', stale_days: 1 },
  { series_id: 'vol.skew', display_name: 'CBOE SKEW', asset_class: 'vol', change: 7.47, z: 2.08, percentile: 98.8, vol_flag: null, stale_days: 1 },
  { series_id: 'fx.eurusd', display_name: 'EURUSD', asset_class: 'fx', change: -0.004, z: -0.42, percentile: 34.1, vol_flag: null, stale_days: 6 },
];

/** The other half of the point: series that legitimately cannot be scored, each with a reason. */
const UNSCORED = [
  { series_id: 'credit.hy.oas', display_name: 'HY OAS', asset_class: 'credit', status: 'no_data' },
  { series_id: 'eq.msci_em', display_name: 'MSCI EM', asset_class: 'equity', status: 'insufficient_history' },
];

export const terminalHandlers = [
  http.get('*/api/terminal/board', ({ request }) => {
    const asOf = asOfFrom(request);
    const assetClass = new URL(request.url).searchParams.get('asset_class');
    // Early as-ofs have less data, same as the real store.
    const scored = asOf < LATE_SERIES_FROM ? SCORED.slice(0, 2) : SCORED;

    let rows = [
      ...scored.map((r) => ({
        ...r,
        value_date: '2026-09-11',
        change_unit: 'bp',
        window_n: 250,
        trailing_sd: 4.35,
        vol_percentile: r.vol_flag === 'compressed' ? 6.2 : 48.0,
        status: 'ok' as const,
        as_of_basis: 'source_vintage',
      })),
      ...UNSCORED.map((r) => ({
        ...r,
        value_date: null,
        change: null,
        change_unit: null,
        z: null,
        percentile: null,
        window_n: null,
        trailing_sd: null,
        vol_percentile: null,
        vol_flag: null,
        stale_days: null,
        status: r.status as BoardResponse['rows'][number]['status'],
        as_of_basis: null,
      })),
    ];
    if (assetClass) rows = rows.filter((r) => r.asset_class === assetClass);

    return HttpResponse.json<BoardResponse>({
      as_of: asOf.toISOString(),
      rows: rows as BoardResponse['rows'],
      params: {
        zscore_window: 250,
        min_observations: 60,
        max_gap_days: 5,
        vol_percentile_window: 750,
        stale_warn_days: 3,
      },
      ok_count: rows.filter((r) => r.status === 'ok').length,
    });
  }),

  http.get('*/api/terminal/regime', ({ request }) => {
    const asOf = asOfFrom(request);
    return HttpResponse.json<RegimeResponse>({
      as_of: asOf.toISOString(),
      value_date: '2026-09-10',
      state: 'quiet',
      detail: 'no leg moved by 0.75sd over 20 days; no regime claimed',
      window_days: 20,
      evidence: {
        'ust.10y.real': 0.71,
        'credit.hy.oas': -0.01,
        'fx.usd.broad': -0.63,
      },
    });
  }),

  http.get('*/api/terminal/edges', ({ request }) => {
    const asOf = asOfFrom(request);
    const estimated = asOf >= EDGES_ESTIMATED_FROM;
    const definitions = [
      { from_series: 'be.5y', to_series: 'ust.2y.nominal', expected_sign: 1, typical_lag_days: 1, chain: 'feedback' },
      { from_series: 'cmdty.wti', to_series: 'be.5y', expected_sign: 1, typical_lag_days: 1, chain: 'inflation' },
      // expected_sign 0: genuinely regime-dependent, not unknown.
      { from_series: 'ust.10y.real', to_series: 'eq.spx', expected_sign: 0, typical_lag_days: 0, chain: 'discount' },
    ];

    const edges = definitions.map((d, i) => ({
      ...d,
      note: null,
      as_of: estimated ? EDGES_ESTIMATED_FROM.toISOString() : null,
      value_date: estimated ? '2026-09-10' : null,
      beta: estimated ? [-0.115, 0.402, -0.233][i] : null,
      beta_window: estimated ? 250 : null,
      beta_t_stat: estimated ? [-1.1, 3.4, -2.2][i] : null,
      r_squared: estimated ? [0.005, 0.121, 0.048][i] : null,
      corr: estimated ? [-0.07, 0.35, -0.22][i] : null,
      corr_percentile: estimated ? [36.5, 88.1, 12.4][i] : null,
      corr_history_n: estimated ? 756 : null,
      // One conflict, so the banner's active branch is reachable in development.
      sign_conflict: estimated ? i === 0 : null,
      significant: estimated ? i !== 0 : null,
      n_obs: estimated ? 250 : null,
    }));

    return HttpResponse.json<EdgesResponse>({
      as_of: asOf.toISOString(),
      edges,
      conflicts: edges.filter((e) => e.sign_conflict).length,
      unestimated: edges.filter((e) => e.beta == null).map((e) => `${e.from_series}->${e.to_series}`),
    });
  }),

  http.get('*/api/terminal/policy', ({ request }) => {
    const asOf = asOfFrom(request);
    // Before the curve was ingested there is no path, and the note says so rather than the
    // page drawing a flat line.
    if (asOf < LATE_SERIES_FROM) {
      return HttpResponse.json<PolicyResponse>({
        as_of: asOf.toISOString(),
        steps: [],
        note: 'No implied path is stored at this as_of. This is an absence of data, not a flat path.',
      });
    }
    return HttpResponse.json<PolicyResponse>({
      as_of: asOf.toISOString(),
      steps: [
        { meeting_date: '2026-10-28', implied_rate: 3.875, change_bp: -12.5 },
        { meeting_date: '2026-12-09', implied_rate: 3.688, change_bp: -18.7 },
        { meeting_date: '2027-01-27', implied_rate: 3.625, change_bp: -6.3 },
      ],
      note: null,
    });
  }),

  http.get('*/api/terminal/brief', ({ request }) => {
    const asOf = asOfFrom(request);
    return HttpResponse.json<BriefResponse>({
      as_of: asOf.toISOString(),
      unanswered: 1,
      markdown: [
        '# Cross-asset brief',
        '',
        '> **1 of the 5 standing questions could not be answered** from stored data.',
        '',
        '## What moved',
        '',
        'Rates led, with the 5y nominal up 14bp — a 3.05 sigma move against a **compressed** window.',
        '',
        '| series | change | z |',
        '|---|---:|---:|',
        '| ust.5y.nominal | 14bp | 3.05 |',
        '| spread.2s10s | -6bp | -2.28 |',
        '',
        '---',
        '',
        '## Provenance',
        '',
        'How the `as_of` on each stored observation was established:',
        '',
        '- `source_vintage` — the publisher’s own vintage date',
        '- `archive_floor` — known *by* that time; true publication date unrecoverable',
      ].join('\n'),
    });
  }),

  http.get('*/api/terminal/series', () =>
    HttpResponse.json<TerminalSeries[]>([
      {
        series_id: 'ust.10y.nominal',
        display_name: '10y nominal',
        source: 'treasury',
        asset_class: 'rates',
        category: 'actual',
        unit: 'bp',
        frequency: 'd',
        revisable: false,
        vintage_source: 'source_vintage',
        notes: null,
      },
    ]),
  ),
];
