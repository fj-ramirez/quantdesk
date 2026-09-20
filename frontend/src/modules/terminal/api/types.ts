/**
 * Wire types for `/api/terminal/*` (T80) — the mirror of the module's Pydantic models.
 *
 * Note how much is nullable. That is not defensiveness, it is the subject matter: at a
 * historical `as_of` most series legitimately have no value yet, and this module's entire
 * purpose is to say so rather than to show you today's number in yesterday's clothes. Every
 * nullable field below is a place the UI must render "—" and never "0".
 */

/** Why a board row has no numbers. Anything but `ok` means every numeric field is null. */
export type BoardStatus =
  | 'ok'
  | 'no_data'
  | 'insufficient_history'
  | 'zero_variance'
  | 'not_daily'
  | 'transform_error';

export interface BoardRow {
  series_id: string;
  display_name: string | null;
  asset_class: string | null;
  value_date: string | null;
  change: number | null;
  change_unit: string | null;
  /** Standard deviations against this series' own trailing window, which excludes this change. */
  z: number | null;
  percentile: number | null;
  window_n: number | null;
  trailing_sd: number | null;
  vol_percentile: number | null;
  /** 'compressed' | 'elevated' — a z of 2 against a compressed window means something else. */
  vol_flag: string | null;
  stale_days: number | null;
  status: BoardStatus;
  /** source_vintage | derived_lag | archive_floor — how this row's as_of was established. */
  as_of_basis: string | null;
}

export interface BoardResponse {
  as_of: string;
  rows: BoardRow[];
  /** Echoed so any figure on screen is reproducible from stored inputs + these + the as_of. */
  params: Record<string, number>;
  ok_count: number;
}

export interface RegimeResponse {
  as_of: string;
  value_date: string | null;
  /** risk_off | risk_on | rates_led | quiet. `quiet` is a real answer, not a failure. */
  state: string;
  detail: string;
  window_days: number;
  /** The three legs as z-scores. A state without its evidence is an opinion you cannot check. */
  evidence: Record<string, number | null>;
}

export interface EdgeRow {
  from_series: string;
  to_series: string;
  /** +1 / -1 / 0 from theory. **0 is a real value**: genuinely regime-dependent, not unknown. */
  expected_sign: number;
  typical_lag_days: number;
  chain: string | null;
  note: string | null;
  as_of: string | null;
  value_date: string | null;
  beta: number | null;
  beta_window: number | null;
  beta_t_stat: number | null;
  r_squared: number | null;
  corr: number | null;
  /** Where this correlation sits in its own history — one of the graph's two key outputs. */
  corr_percentile: number | null;
  corr_history_n: number | null;
  /** The empirical sign disagrees with theory — the other key output. */
  sign_conflict: boolean | null;
  significant: boolean | null;
  n_obs: number | null;
}

export interface EdgesResponse {
  as_of: string;
  edges: EdgeRow[];
  conflicts: number;
  /** Named, not silently dropped: a missing edge and an unestimated one look alike otherwise. */
  unestimated: string[];
}

export interface PolicyStep {
  meeting_date: string;
  implied_rate: number | null;
  change_bp: number | null;
}

export interface PolicyResponse {
  as_of: string;
  steps: PolicyStep[];
  /** Set when no path is stored at this as_of — an absence of data, not a flat path. */
  note: string | null;
}

export interface BriefResponse {
  as_of: string;
  markdown: string;
  unanswered: number;
}

export interface TerminalSeries {
  series_id: string;
  display_name: string;
  source: string;
  asset_class: string;
  category: string;
  unit: string;
  frequency: string;
  revisable: boolean;
  vintage_source: string;
  notes: string | null;
}
