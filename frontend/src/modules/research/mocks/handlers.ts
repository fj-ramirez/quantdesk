/**
 * MSW handlers for `/api/research/*` (T78).
 *
 * The fixture data is chosen to make the module's central fact visible in development: with a
 * realistic trial count the noise ceiling sits around 5.6, and **the best row in the fixture is
 * still below it**. That is not a placeholder oddity — it is what the real registry looks like
 * today (134,377 trials, `candidates_above_ceiling: 0`), and building the page against
 * optimistic mock data would have produced a UI tuned for a state that has never occurred.
 *
 * One row is given an implausibly long OOS span and a high Sharpe so the `above_ceiling` branch
 * is also reachable without editing fixtures.
 *
 * Matched with a `*` origin wildcard so these work regardless of what `VITE_API_BASE_URL`
 * resolves to, same as the gex handlers.
 */
import { http, HttpResponse } from 'msw';
import type {
  LeaderboardResponse,
  LeaderboardRow,
  PaperCandidate,
  ResearchStatus,
  Trial,
} from '../api/types';

const TOTAL_TRIALS = 134_377;

/** sqrt(2 ln N) / sqrt(years) — the same formula `backtest.noise_ceiling` uses. */
function noiseCeiling(nTrials: number, oosYears: number): number {
  if (nTrials < 1 || oosYears <= 0) return 0;
  return Math.sqrt(2 * Math.log(Math.max(nTrials, 2))) / Math.sqrt(oosYears);
}

function row(partial: Partial<LeaderboardRow> & { hash: string; oos_sharpe: number; oos_years: number }): LeaderboardRow {
  const ceiling = noiseCeiling(TOTAL_TRIALS, partial.oos_years);
  return {
    market: 'futures',
    strategy: 'zscore_meanrev',
    symbol: 'ZC=F',
    timeframe: '1h',
    params: { n: 160, exit_z: 0, regime: 'high_vol', entry_z: 1, long_short: true },
    is_sharpe: 3.47,
    oos_cagr: 2.21,
    oos_max_dd: -0.058,
    oos_fills: 164,
    oos_exposure: 0.357,
    run_date: '2026-08-24',
    ...partial,
    noise_ceiling: ceiling,
    above_ceiling: partial.oos_sharpe > ceiling,
  };
}

export const MOCK_LEADERBOARD_ROWS: LeaderboardRow[] = [
  // The realistic case: a headline-looking 4.98 that does not clear its own 5.52 ceiling.
  row({ hash: '10b6af0ade6cecd64b876234', oos_sharpe: 4.98, oos_years: 0.77 }),
  row({
    hash: '15b2d48c011c0b4c2b2ed253',
    oos_sharpe: 4.82,
    oos_years: 0.73,
    params: { n: 20, exit_z: 0.25, regime: 'any', entry_z: 1, long_short: true },
  }),
  row({
    hash: 'aa11bb22cc33dd44ee55ff66',
    oos_sharpe: 2.1,
    oos_years: 3.2,
    market: 'crypto',
    strategy: 'donchian_breakout',
    symbol: 'BTC/USDT',
    params: { lookback: 55, exit_n: 20 },
  }),
  // The other branch, so `above_ceiling: true` styling is reachable in development.
  row({
    hash: 'ff00ff00ff00ff00ff00ff00',
    oos_sharpe: 3.4,
    oos_years: 18.0,
    market: 'stocks',
    strategy: 'ts_momentum',
    symbol: 'SPY',
    timeframe: '1d',
    params: { lookback: 200, skip: 20 },
  }),
];

const MOCK_STATUS: ResearchStatus = {
  total_trials: TOTAL_TRIALS,
  paper_candidates: 23,
  cycles: 66,
  last_run_date: '2026-09-19',
  trials_last_cycle: 2130,
  markets: ['crypto', 'forex', 'futures', 'stocks'],
  strategies: [
    'day_of_week',
    'donchian_breakout',
    'ema_cross',
    'rsi_meanrev',
    'shock_reversal',
    'time_of_day',
    'ts_momentum',
    'xs_momentum',
    'zscore_meanrev',
  ],
  timeframes: ['1d', '1h', '4h'],
};

const MOCK_PAPER: PaperCandidate[] = [
  {
    hash: 'cand00000000000000000001',
    promoted_at: '2026-07-04T02:11:00+00:00',
    market: 'crypto',
    strategy: 'donchian_breakout',
    symbol: 'ETH/USDT',
    timeframe: '4h',
    params: { lookback: 55, exit_n: 20 },
    promoted_oos_sharpe: 1.42,
    sharpe_2x: 0.81,
    neighbor_med: 0.55,
    wf_pos: 5,
    wf_active: 6,
    wf_med: 0.7,
    corr_max: 0.31,
  },
  {
    hash: 'cand00000000000000000002',
    promoted_at: '2026-08-12T02:09:00+00:00',
    market: 'stocks',
    strategy: 'ts_momentum',
    symbol: 'QQQ',
    timeframe: '1d',
    params: { lookback: 120, skip: 10 },
    promoted_oos_sharpe: 1.05,
    sharpe_2x: 0.62,
    neighbor_med: 0.48,
    wf_pos: 4,
    wf_active: 5,
    wf_med: 0.51,
    corr_max: 0.44,
  },
];

export const researchHandlers = [
  http.get('*/api/research/leaderboard', ({ request }) => {
    const url = new URL(request.url);
    const market = url.searchParams.get('market');
    const strategy = url.searchParams.get('strategy');
    const timeframe = url.searchParams.get('timeframe');
    const limit = Number(url.searchParams.get('limit') ?? 40);
    const offset = Number(url.searchParams.get('offset') ?? 0);

    const matching = MOCK_LEADERBOARD_ROWS.filter(
      (r) =>
        (!market || r.market === market) &&
        (!strategy || r.strategy === strategy) &&
        (!timeframe || r.timeframe === timeframe),
    );
    const page = matching.slice(offset, offset + limit);
    const spans = page.map((r) => r.oos_years ?? 0).filter(Boolean).sort((a, b) => a - b);
    const median = spans.length ? spans[Math.floor(spans.length / 2)] : 0;

    return HttpResponse.json<LeaderboardResponse>({
      rows: page,
      total: matching.length,
      // Never the filtered count: narrowing to one market does not mean fewer experiments
      // were run, and a ceiling that fell with a filter would let anyone filter their way to
      // a green row. The real API enforces this; the mock must not teach otherwise.
      total_trials: TOTAL_TRIALS,
      noise_ceiling: noiseCeiling(TOTAL_TRIALS, median),
      median_oos_years: median,
      limit,
      offset,
    });
  }),

  http.get('*/api/research/trials/:hash', ({ params }) => {
    const hash = params.hash as string;
    const found = MOCK_LEADERBOARD_ROWS.find((r) => r.hash === hash);
    if (!found) {
      return HttpResponse.json({ detail: `no trial with hash '${hash}'` }, { status: 404 });
    }
    return HttpResponse.json<Trial>({
      hash: found.hash,
      market: found.market,
      strategy: found.strategy,
      symbol: found.symbol,
      timeframe: found.timeframe,
      params: found.params,
      run_date: found.run_date,
      is_sharpe: found.is_sharpe,
      is_cagr: 1.9,
      is_max_dd: -0.09,
      is_fills: 220,
      oos_sharpe: found.oos_sharpe,
      oos_cagr: found.oos_cagr,
      oos_max_dd: found.oos_max_dd,
      oos_fills: found.oos_fills,
      oos_exposure: found.oos_exposure,
      oos_bars: 6800,
      oos_years: found.oos_years,
    });
  }),

  http.get('*/api/research/paper', () => HttpResponse.json<PaperCandidate[]>(MOCK_PAPER)),

  http.get('*/api/research/status', () => HttpResponse.json<ResearchStatus>(MOCK_STATUS)),
];
