/**
 * Hand-written API types — PROVISIONAL.
 *
 * T11 (the read API) has not shipped yet, so there is no OpenAPI schema to run
 * `openapi-typescript` against. Everything below is inferred from PLAN.md §3, TASKS.md
 * (T08 engine, T09 persistence, T11 endpoints) and the *committed* data contract in
 * `docs/schema.md` / `backend/app/models/chain.py`.
 *
 * Two tiers of confidence, marked inline:
 *   - "read fact" — copied from the committed backend schema, should not drift.
 *   - "GUESS" — inferred shape; the actual T11 response may differ. These are the fields
 *     most likely to break integration; grep this file for "GUESS" when T11 ships.
 *
 * When T11 ships: regenerate this file's contents from its OpenAPI JSON via
 * `openapi-typescript`, keep this file's path so `src/api/queries.ts` and every component
 * import stays valid, and delete this notice.
 */

// ---------------------------------------------------------------------------------------
// Enums shared with the backend (read facts: docs/schema.md, TASKS.md T08 filter enum).
// Modeled as `as const` string-literal unions, not TS `enum` — tsconfig.app.json sets
// `erasableSyntaxOnly`, which rejects real enums (they emit runtime code).
// ---------------------------------------------------------------------------------------

export const UNDERLYINGS = ['SPX', 'SPY', 'QQQ'] as const;
export type Underlying = (typeof UNDERLYINGS)[number];

export const EXPIRY_FILTERS = ['ALL', 'ZERO_DTE', 'THIS_WEEK', 'MONTHLY_ONLY', 'EX_ZERO_DTE'] as const;
export type ExpiryFilter = (typeof EXPIRY_FILTERS)[number];

/** Human labels for the expiry filter selector. Kept here (not in a component) so any
 * consumer renders the same wording. */
export const EXPIRY_FILTER_LABELS: Record<ExpiryFilter, string> = {
  ALL: 'All',
  ZERO_DTE: '0DTE',
  EX_ZERO_DTE: 'Ex-0DTE',
  THIS_WEEK: 'This week',
  MONTHLY_ONLY: 'Monthly',
};

export type Right = 'C' | 'P';
export type Settlement = 'AM' | 'PM';

// ---------------------------------------------------------------------------------------
// Snapshot identity + provenance (read fact: backend/app/models/db.py `snapshots` table,
// T04/T05). Echoed on every GEX response so the UI can show "as of <NY time> / delayed
// 15m" without a second round trip.
// ---------------------------------------------------------------------------------------

export interface SnapshotInfo {
  id: number;
  underlying: Underlying;
  /** ISO 8601, tz-aware UTC — the vendor's own payload timestamp (docs/schema.md T34
   * correction: this is NOT reliably "the effective time of the data" — Cboe's `timestamp`
   * keeps advancing for hours after the close while the chain itself is frozen). Convert to
   * America/New_York for display; never assume it is already local. Do not use this alone to
   * render a staleness badge once the market may have closed — use `effective_at`. */
  captured_at: string;
  source: string;
  /** Vendor delay in minutes; 15 for the free Cboe feed, 0 for real-time (Phase 5). */
  delayed_minutes: number;
  is_eod: boolean;
  spot: number;
  contract_count: number;
  /** T34, verified against `/openapi.json`: the honest "as of" instant for a staleness
   * badge, derived server-side (`app.jobs.calendar.effective_data_time`) from `captured_at`
   * and `delayed_minutes`. Equal to `captured_at` during a regular NY session; otherwise
   * clamped to the most recent 16:00 ET close plus `delayed_minutes`. Never derive this from
   * `captured_at` yourself in the frontend — the backend owns market-hours logic. */
  effective_at: string;
}

/** Verified against the live `/openapi.json` (T11).
 *
 * Every strike-valued level is nullable, and this is the daily case, not an edge case: the
 * EOD capture runs at 16:20 ET, after every same-day contract has expired, so the
 * `ZERO_DTE` result of *every* EOD snapshot carries `net_gex: 0` with null walls. Reading
 * these as plain numbers is how you get "wall at strike 0" on the dashboard.
 * `flip_point` is additionally null whenever the profile has no sign change in the grid.
 *
 * Walls are net-based (argmax/argmin of net strike GEX), which `docs/validation.md`
 * confirmed matches a real vendor exactly on all four values. `max_call_gex_strike` /
 * `max_put_gex_strike` are the per-side reading, which collapses onto one dominant strike
 * and is not a tradeable level — label it clearly if it is ever shown. */
export interface KeyLevels {
  net_gex: number;
  call_gex: number;
  put_gex: number;
  abs_gex: number;
  call_wall: number | null;
  call_wall_gex: number | null;
  put_wall: number | null;
  put_wall_gex: number | null;
  max_abs_strike: number | null;
  max_abs_gex: number | null;
  max_net_strike: number | null;
  min_net_strike: number | null;
  max_call_gex_strike: number | null;
  max_call_gex: number | null;
  max_put_gex_strike: number | null;
  max_put_gex: number | null;
  flip_point: number | null;
  spot: number | null;
  computed_at: string | null;
  top_positive: StrikeGex[];
  top_negative: StrikeGex[];
}

/** One row of `gex_by_strike` (T09) — verified against `/openapi.json`. */
export interface StrikeGex {
  strike: number;
  call_gex: number;
  put_gex: number;
  net_gex: number;
  abs_gex: number;
  contracts: number;
  open_interest: number;
}

/** Verified against `/openapi.json` (T11). */
export interface ExpiryGex {
  /** ISO date, e.g. "2026-09-18". */
  expiry: string;
  dte: number;
  net_gex: number;
  call_gex: number;
  put_gex: number;
  abs_gex: number;
  contracts: number;
  open_interest: number;
}

/** Per-snapshot counts explaining what the engine admitted and excluded. Worth surfacing:
 * `expired` is ~626 on a post-close SPX chain and `extreme_iv` explains any gap against a
 * vendor figure via `extreme_iv_gex_excluded`. */
export interface GexDiagnostics {
  contracts: number;
  included: number;
  expired: number;
  missing_open_interest: number;
  zero_open_interest: number;
  missing_iv: number;
  missing_iv_gex_vendor: number;
  extreme_iv: number;
  extreme_iv_open_interest: number;
  extreme_iv_gex_excluded: number;
}

/** GUESS: T08's `gamma_profile()` returns parallel `(spot_grid, total_gex)` arrays per its
 * docstring in TASKS.md; represented here as point objects since that's what an ECharts
 * line series (T14) wants directly. `spot` is a *hypothetical* spot on the grid, not the
 * snapshot's actual spot (that's `KeyLevels.spot` / `SnapshotInfo.spot`). */
export interface GammaProfilePoint {
  spot: number;
  total_gex: number;
}

/** `GET /gex/{underlying}/latest` and `/gex/{underlying}/snapshots/{id}` response.
 * Verified against `/openapi.json` (T11).
 *
 * `filter` is a plain string, not `ExpiryFilter`: an explicit expiry list echoes back as
 * `"EXPIRIES:2026-09-18"`, which is outside the enum. */
export interface GexResult {
  underlying: Underlying;
  filter: string;
  spot: number;
  snapshot: SnapshotInfo;
  levels: KeyLevels;
  by_strike: StrikeGex[];
  by_expiry: ExpiryGex[];
  profile: GammaProfilePoint[];
  diagnostics: GexDiagnostics;
  /** Every expiry present in the snapshot, ISO dates. */
  expiries: string[];
}

/** `GET /gex/{underlying}/levels/history` row — verified against `/openapi.json`.
 * Read straight from `gex_levels`, so the same nullability applies as `KeyLevels`; a
 * history chart must break its line at nulls rather than plotting them as zero. */
export interface LevelHistoryRow {
  snapshot_id: number;
  captured_at: string;
  is_eod: boolean;
  filter: string;
  net_gex: number | null;
  call_wall: number | null;
  call_wall_gex: number | null;
  put_wall: number | null;
  put_wall_gex: number | null;
  max_abs_strike: number | null;
  max_call_gex_strike: number | null;
  max_put_gex_strike: number | null;
  flip_point: number | null;
  spot: number | null;
  computed_at: string;
}

/** One contract as returned by `/chains/{underlying}/latest` — read fact, field names and
 * units copied from `OptionContract` in `backend/app/models/chain.py` verbatim. Note
 * `open_interest: 0` (genuinely none) vs `null` (unknown) must not be collapsed by a
 * display formatter, and `iv` is always a decimal fraction (0.18 = 18%). */
export interface ContractDto {
  occ_symbol: string;
  root: string;
  underlying: Underlying;
  expiry: string;
  settlement: Settlement;
  strike: number;
  right: Right;
  bid: number | null;
  ask: number | null;
  last: number | null;
  volume: number | null;
  open_interest: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  vega: number | null;
  theta: number | null;
  multiplier: number;
  last_trade_time: string | null;
}

/** GUESS: wraps the contract list with the snapshot context (mirrors `GexResult.snapshot`)
 * so a raw-chain view can still show "as of" + delay without a second fetch. TASKS.md T11
 * does not specify an envelope, just "raw contracts for one expiry". */
export interface ChainResponse {
  underlying: Underlying;
  expiry: string;
  snapshot: SnapshotInfo;
  contracts: ContractDto[];
}

/** `GET /snapshots?underlying=SPX&limit=30` row, for the snapshot selector — read facts
 * from the `snapshots` table (T04) plus `contract_count` which T04 stores directly. */
export interface SnapshotSummary {
  id: number;
  underlying: Underlying;
  captured_at: string;
  source: string;
  spot: number;
  contract_count: number;
  is_eod: boolean;
}
