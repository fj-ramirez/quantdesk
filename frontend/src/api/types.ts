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
  /** GUESS: assumed a Postgres serial integer PK; T11 may return a string/UUID instead. */
  id: number;
  underlying: Underlying;
  /** ISO 8601, tz-aware UTC — docs/schema.md: `ChainSnapshot.captured_at` is "the
   * effective time of the data", not the HTTP response time. Convert to America/New_York
   * for display; never assume it is already local. */
  captured_at: string;
  source: string;
  /** Vendor delay in minutes; 15 for the free Cboe feed, 0 for real-time (Phase 5). */
  delayed_minutes: number;
  is_eod: boolean;
  spot: number;
}

/** Mirrors the `gex_levels` table columns (TASKS.md T09) one-to-one — read fact.
 * `flip_point` is legitimately `null` when `flip_point()` (T08) finds no sign change in
 * the ±10% grid: do not coerce it to 0 or NaN anywhere downstream. */
export interface KeyLevels {
  net_gex: number;
  call_wall: number;
  put_wall: number;
  max_abs_strike: number;
  flip_point: number | null;
  spot: number;
  computed_at: string;
}

/** One row of `gex_by_strike` (T09) — read fact. */
export interface StrikeGex {
  strike: number;
  call_gex: number;
  put_gex: number;
  net_gex: number;
}

/** GUESS: TASKS.md T09 only names `gex_levels` and `gex_by_strike` tables; by-expiry
 * totals are computed by T08's `by_expiry(df)` but no persisted shape is specified. This
 * mirrors `StrikeGex`'s split (call/put/net) with `expiry` in place of `strike`. */
export interface ExpiryGex {
  /** ISO date, e.g. "2026-09-18". */
  expiry: string;
  net_gex: number;
  call_gex: number;
  put_gex: number;
}

/** GUESS: T08's `gamma_profile()` returns parallel `(spot_grid, total_gex)` arrays per its
 * docstring in TASKS.md; represented here as point objects since that's what an ECharts
 * line series (T14) wants directly. `spot` is a *hypothetical* spot on the grid, not the
 * snapshot's actual spot (that's `KeyLevels.spot` / `SnapshotInfo.spot`). */
export interface GammaProfilePoint {
  spot: number;
  total_gex: number;
}

/** `GET /gex/{underlying}/latest` and `/gex/{underlying}/snapshots/{id}` response
 * (TASKS.md T11: "GexResult JSON (levels + by_strike + by_expiry + profile)"). The
 * `levels`/`by_strike` shapes are read facts; `by_expiry`/`profile`/the envelope fields
 * around them are GUESSes — confirm against T11's actual payload first. */
export interface GexResult {
  underlying: Underlying;
  filter: ExpiryFilter;
  snapshot: SnapshotInfo;
  levels: KeyLevels;
  by_strike: StrikeGex[];
  by_expiry: ExpiryGex[];
  profile: GammaProfilePoint[];
}

/** `GET /gex/{underlying}/levels/history` row. GUESS: field set mirrors `gex_levels` plus
 * the owning snapshot's `captured_at`/`is_eod`, since the endpoint's whole job is "levels
 * over time" (TASKS.md T11, consumed by the T15 `/history` page). */
export interface LevelHistoryRow {
  snapshot_id: number;
  captured_at: string;
  is_eod: boolean;
  filter: ExpiryFilter;
  net_gex: number;
  call_wall: number;
  put_wall: number;
  max_abs_strike: number;
  flip_point: number | null;
  spot: number;
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
