/**
 * Wire types for `/api/research/*` (T78) — the mirror of
 * `backend/app/modules/research/api/*.py`'s Pydantic models.
 *
 * Note what is **not** optional here. `noiseCeiling`, `totalTrials` and every row's
 * `aboveCeiling` are required fields on the response, because they are required fields of the
 * answer: a leaderboard row without the ceiling it is measured against is not a smaller truth,
 * it is a misleading one. Making them non-nullable means a component cannot render rows while
 * forgetting the ceiling — TypeScript will not let it have the first without the second.
 */

export interface LeaderboardRow {
  hash: string;
  market: string;
  strategy: string;
  symbol: string;
  timeframe: string;
  params: Record<string, unknown>;
  is_sharpe: number | null;
  oos_sharpe: number | null;
  oos_cagr: number | null;
  oos_max_dd: number | null;
  oos_fills: number | null;
  oos_exposure: number | null;
  oos_years: number | null;
  run_date: string;
  /** This row's own ceiling, at its own OOS span — not the headline figure. */
  noise_ceiling: number;
  /** False means indistinguishable from luck. Computed server-side, never inferred here. */
  above_ceiling: boolean;
}

export interface LeaderboardResponse {
  rows: LeaderboardRow[];
  /** Rows matching the filters, before limit/offset. */
  total: number;
  /** Every trial in the registry, losers included — the ceiling's denominator. */
  total_trials: number;
  /** Best OOS Sharpe pure noise would produce, at the median OOS span of these rows. */
  noise_ceiling: number;
  median_oos_years: number;
  limit: number;
  offset: number;
}

export interface Trial {
  hash: string;
  market: string;
  strategy: string;
  symbol: string;
  timeframe: string;
  params: Record<string, unknown>;
  run_date: string;
  is_sharpe: number | null;
  is_cagr: number | null;
  is_max_dd: number | null;
  is_fills: number | null;
  oos_sharpe: number | null;
  oos_cagr: number | null;
  oos_max_dd: number | null;
  oos_fills: number | null;
  oos_exposure: number | null;
  oos_bars: number | null;
  oos_years: number | null;
}

export interface PaperCandidate {
  hash: string;
  promoted_at: string;
  market: string;
  strategy: string;
  symbol: string;
  timeframe: string;
  params: Record<string, unknown>;
  promoted_oos_sharpe: number | null;
  sharpe_2x: number | null;
  neighbor_med: number | null;
  wf_pos: number | null;
  wf_active: number | null;
  wf_med: number | null;
  corr_max: number | null;
}

export interface ResearchStatus {
  total_trials: number;
  paper_candidates: number;
  cycles: number;
  last_run_date: string | null;
  trials_last_cycle: number;
  markets: string[];
  strategies: string[];
  timeframes: string[];
}

export interface LeaderboardQuery {
  market?: string;
  strategy?: string;
  timeframe?: string;
  minTradesOos?: number;
  minExposure?: number;
  limit?: number;
  offset?: number;
}
