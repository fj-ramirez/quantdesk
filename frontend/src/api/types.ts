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

/** The five symbols the 16:20 EOD job (and its 20:00 safety net) captures -- read fact,
 * mirrors `Settings.SYMBOLS` (`backend/app/config.py`). This list drives nothing the core
 * capture doesn't already guarantee: it is the TopBar's first symbol-switcher group. */
export const CORE_UNDERLYINGS = ['SPX', 'SPY', 'QQQ', 'GLD', 'DIA'] as const;

/** T47's sector/industry ETFs, captured by the separate 16:45 ET job -- a UI-side mirror of
 * `Settings.EXTENDED_SYMBOLS`'s default (`backend/app/config.py`), all 23 of which were
 * verified live against Cboe on 2026-09-09 (see `app/models/chain.py`'s `Underlying` enum).
 * `GET /api/symbols` returns the same split at runtime for any consumer that needs to read it
 * off the server rather than this static copy (e.g. a future regime-board fetch); this array
 * exists so the TopBar's second switcher group and the URL-state validator do not have to wait
 * on a network round trip just to know their own symbol list. Keep the two in sync -- adding a
 * symbol here without adding it to `EXTENDED_SYMBOLS` (or vice versa) makes `?symbol=` accept
 * or reject a value the backend disagrees with. */
export const EXTENDED_UNDERLYINGS = [
  'XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLP', 'XLU', 'XLB', 'XLRE', 'XLC',
  'IWM', 'SMH', 'XBI', 'KRE', 'XOP', 'TLT', 'HYG', 'EEM', 'FXI', 'SLV', 'USO', 'GDX',
] as const;

export const UNDERLYINGS = [...CORE_UNDERLYINGS, ...EXTENDED_UNDERLYINGS] as const;
export type Underlying = (typeof UNDERLYINGS)[number];

/** `GET /api/symbols` response -- the core/extended split, read fresh from the server. Mirrors
 * `backend/app/api/symbols.py`'s `SymbolsResponse`. Typed as `string[]`, not `Underlying[]`:
 * this is the one place the backend's own symbol lists are the source of truth, so narrowing
 * to the frontend's static `Underlying` union here would silently hide a drift between the
 * two instead of surfacing it. */
export interface SymbolsResponse {
  core: string[];
  extended: string[];
}

/** Underlying -> the CFD instrument the user actually trades (T41), for labelling the report
 * page's CFD-spot input. A UI-side mirror of the backend's own `CFD_INSTRUMENTS`
 * (`backend/app/gex/report.py`), which owns the canonical mapping and does the actual
 * conversion; this copy exists only so the field can be labelled before any report data has
 * loaded. Keep the two in sync -- adding an instrument is one line in each.
 *
 * `Partial` since T47: no broker CFD instrument is configured for any sector/industry ETF
 * today, and `app/api/report.py` degrades a `cfd_spot` request for one of those symbols to
 * "no CFD mapping" (`cfd: null`) rather than raising -- a caller here must handle the `Record`
 * legitimately not having every `Underlying` as a key, the same way the backend does. */
export const CFD_INSTRUMENTS: Partial<Record<Underlying, string>> = {
  GLD: 'XAUUSD',
  DIA: 'US30',
  SPX: 'US500',
  SPY: 'US500',
  QQQ: 'NAS100',
};

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

// ---------------------------------------------------------------------------------------
// Scan (T43 breakouts, T45 trend), bars (T42), universe (T42) and the `bars` capture-health
// block (T42/T47) -- T55.
//
// Hand-written, mirrored field-for-field from the backend's own Pydantic response models
// rather than generated (project convention -- see the notice at the top of this file):
// `backend/app/api/scan.py` (`BreakoutsResponse`, `SymbolEventsResponse`, `TrendResponse`,
// `SymbolTrendResponse`), `backend/app/api/bars.py` (`BarOut`, `UniverseResponse`), and
// `backend/app/api/health.py`'s `CaptureHealthResponse` (its `bars` block, plus the existing
// `SymbolCaptureHealth` shape reused for `extended`). Verified against the live backend on
// 2026-09-09 -- see `mocks/fixtures/scan/README.md` for the exact curl commands and what was
// hand-edited versus recorded as-is. Every response key here matched `07-ui.md`'s "Verified
// facts" list; the one addition not spelled out there is `bars.stale_count` sitting alongside
// `bars.symbols[]`, which is consistent with (not a contradiction of) that list.
// ---------------------------------------------------------------------------------------

export type BreakoutDirection = 'up' | 'down';
export type BreakoutOutcome = 'continued' | 'failed' | 'pending';

/** One breakout/breakdown event -- mirrors `app.scan.breakouts.BreakoutEvent`. `resolved_at`
 * and every `*_atr` field are null exactly while `outcome` is `'pending'` (the event hasn't
 * reached `k` bars yet) -- see that dataclass's own docstring for the full state machine. */
export interface BreakoutEvent {
  date: string;
  direction: BreakoutDirection;
  level: number;
  close: number;
  outcome: BreakoutOutcome;
  resolved_at: string | null;
  bars_elapsed: number;
  follow_through_atr: number | null;
  excursion_atr: number | null;
  mfe_atr: number | null;
  mae_atr: number | null;
}

/** One row of the breakouts summary table. `rate` is `null` below the five-event floor --
 * the common case at short lookbacks (46 of 47 universe symbols at `lookback=40`), not an
 * edge case. `status` is a plain read of `last_event.outcome` (`null` for a symbol with zero
 * events in the window), never a separately computed value. */
export interface SymbolBreakoutSummary {
  symbol: string;
  events: number;
  continued: number;
  failed: number;
  pending: number;
  rate: number | null;
  mean_follow_through_atr: number | null;
  last_event: BreakoutEvent | null;
  status: BreakoutOutcome | null;
}

/** One row of the "Open breakouts" panel -- a still-`pending` event from any universe
 * symbol. */
export interface OpenBreakout {
  symbol: string;
  direction: BreakoutDirection;
  level: number;
  date: string;
  bars_elapsed: number;
  excursion_atr: number | null;
}

/** A symbol dropped from the universe scan for having too gappy a bars history to trust --
 * see `app/api/scan.py`'s `_MAX_MISSING_BAR_FRACTION`. */
export interface ExcludedSymbol {
  symbol: string;
  reason: string;
}

/** `GET /api/scan/breakouts?n=&k=&lookback=` response. */
export interface BreakoutsResponse {
  n: number;
  k: number;
  lookback: number;
  summaries: SymbolBreakoutSummary[];
  open_breakouts: OpenBreakout[];
  excluded: ExcludedSymbol[];
}

/** `GET /api/scan/breakouts/{symbol}?n=&k=&lookback=` response. Never 404s -- a symbol with
 * no stored bars at all (a typo, or one newly added to `SCAN_UNIVERSE` before its first bars
 * job run) returns a clean empty `events` list. */
export interface SymbolBreakoutsResponse {
  symbol: string;
  n: number;
  k: number;
  lookback: number;
  events: BreakoutEvent[];
}

/** Mirrors `app.scan.trend.TrendComponents`. `iv30`/`iv_rv_ratio` are `null` for the 19 of 47
 * `SCAN_UNIVERSE` symbols with no option chain at all (see `CORE_UNDERLYINGS` /
 * `EXTENDED_UNDERLYINGS`) -- a real, permanent answer for those symbols, not a loading
 * state. */
export interface TrendComponents {
  adx14: number | null;
  er20: number | null;
  chop14: number | null;
  vr: number | null;
  vr_z: number | null;
  rv20: number | null;
  iv30: number | null;
  iv_rv_ratio: number | null;
}

/** One row of `GET /api/scan/trend`: every `TrendComponents` field plus each component's
 * cross-sectional percentile and the composite (the mean of the four rank percentiles;
 * IV/RV is shown but not part of it). Flat, not nested -- mirrors `TrendRowOut`, which merges
 * `TrendComponents` in beside `symbol`/`*_pct`/`composite` for exactly this reason. */
export interface TrendRow extends TrendComponents {
  symbol: string;
  adx_pct: number | null;
  er_pct: number | null;
  chop_pct: number | null;
  vr_pct: number | null;
  composite: number | null;
}

export interface TrendResponse {
  rows: TrendRow[];
}

/** One day of the detail panel's sparkline history. `vr`/`vr_z`/`iv30`/`iv_rv_ratio` are
 * deliberately absent -- `app.scan.indicators.variance_ratio` has no rolling per-day reading
 * to plot, and no IV history is ever persisted past the latest snapshot. Both still appear
 * once, as the current value, in `SymbolTrendResponse.current`. */
export interface TrendHistoryPoint {
  date: string;
  adx14: number | null;
  er20: number | null;
  chop14: number | null;
  rv20: number | null;
}

/** `GET /api/scan/trend/{symbol}` response. Same "never 404s, a clean empty/`null` result
 * for a symbol with no stored bars" contract as `SymbolBreakoutsResponse`. */
export interface SymbolTrendResponse {
  symbol: string;
  current: TrendComponents;
  history: TrendHistoryPoint[];
}

/** `GET /api/bars/{symbol}` row -- mirrors `app.models.bars.DailyBar`. `volume` distinguishes
 * a genuine `0` from `null` (unknown), the same open-interest discipline `ContractDto`
 * already applies. */
export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  source: string;
}

/** `GET /api/universe` response -- the configured `SCAN_UNIVERSE`, in order. Typed as
 * `string[]`, not `Underlying[]`: most of this universe (19 of 47 symbols) has no option
 * chain at all, so narrowing here would silently hide that gap rather than surface it (same
 * reasoning `SymbolsResponse` above already gives for its own `core`/`extended` fields). */
export interface UniverseResponse {
  symbols: string[];
}

/** One symbol's row in the `bars` block of `GET /api/health/capture` (T42) -- keyed on a
 * `SCAN_UNIVERSE` ticker, which may not have an option chain, unlike `SymbolCaptureHealth`
 * below which is keyed on an `Underlying`. */
export interface SymbolBarsHealth {
  symbol: string;
  last_bar_date: string | null;
  stale: boolean;
}

/** T42 addition to `CaptureHealthResponse` -- a bars-job outage must be visible the same way
 * a broken option capture already is. */
export interface BarsHealthBlock {
  symbols: SymbolBarsHealth[];
  stale_count: number;
}

/** One underlying's row in `GET /api/health/capture`'s `symbols` (the core five, T29) and
 * `extended` (T47's sector/industry ETFs) blocks. */
export interface SymbolCaptureHealth {
  underlying: string;
  last_capture_at: string | null;
  last_eod_capture_at: string | null;
  eod_captured_today: boolean;
  stale: boolean;
}

/** `GET /api/health/capture` response. `bars` (T42) and `extended` (T47) are additive to the
 * original `generated_at`/`symbols` shape (T29); nothing here narrows or removes a field. */
export interface CaptureHealth {
  generated_at: string;
  symbols: SymbolCaptureHealth[];
  bars: BarsHealthBlock;
  extended: SymbolCaptureHealth[];
}

// ---------------------------------------------------------------------------------------
// Report (T39/T40) -- `GET /api/report/{underlying}?filter=`
//
// GENERATED, not hand-written. Every interface below was emitted by a script from the live
// FastAPI `/openapi.json` `components.schemas` (`ReportOut` and its dependencies) and pasted
// verbatim, with only the two documented narrowings noted under `Report`. This matters: the
// last time this project split an API task from its frontend task, the handoff was described
// as "purely additive" when it actually widened several fields to nullable, and that hid nine
// real type errors. Machine transcription cannot make that mistake.
//
// Regenerate the same way after any change to `backend/app/api/schemas.py`'s report models:
// dump `/openapi.json` and re-emit these from `ReportOut`.
//
// Note the nullability, which is load-bearing and easy to get wrong by hand:
// `DealerPositioning`'s `net_gex`, `abs_gex` and `ratio_floor` are all nullable (they pass
// through the engine's NaN-to-null conversion), as are `MaxPain.strike` and every
// `ReportLevel.strike`.
// ---------------------------------------------------------------------------------------

/** The strike minimising total intrinsic value of all open contracts at expiry.
 *
 * Every field is null together when the filter admitted no open contracts -- the daily
 * `ZERO_DTE`-after-the-close case. Render a dash, never a zero. */
export interface MaxPain {
  strike: number | null;
  distance: number | null;
  distance_pct: number | null;
  total_pain: number | null;
  strikes_evaluated: number;
  contracts: number;
  open_interest: number;
}

/** Both ratios are **puts / calls**. The example report that seeded this feature inverted
 * exactly this and derived a bearish reading from the inversion; label it explicitly in the
 * UI so a reader can tell which way round it is. Null (not `Infinity`) when there are no
 * calls. */
export interface PutCallRatios {
  call_open_interest: number;
  put_open_interest: number;
  total_open_interest: number;
  open_interest_ratio: number | null;
  call_volume: number;
  put_volume: number;
  total_volume: number;
  volume_ratio: number | null;
  call_contracts: number;
  put_contracts: number;
  missing_open_interest: number;
  missing_volume: number;
}

/** ATM implied vol at a constant ~30-day maturity, and a regime label only if earned.
 *
 * `atm_iv` is a decimal fraction (0.229 = 22.9%), so render it with `formatIv`.
 *
 * **`label` is null on every deployment today** and that is the correct, intended state:
 * nothing persists past ATM IVs, so there is no distribution to place the current number in.
 * Render "insufficient history" -- never substitute "NORMAL". Inventing the band is the
 * specific failure the source report made (it printed "NORMAL VOLATILITY (17.9%)" against a
 * chain whose real ATM vol was 22.9%). */
export interface IvRegime {
  atm_iv: number | null;
  target_dte: number;
  lower_dte: number | null;
  upper_dte: number | null;
  interpolated: boolean;
  contracts: number;
  label: string | null;
  history_observations: number;
  min_history_required: number;
}

/** Direction of dealer gamma, gated on `ratio` (= |net GEX| / gross GEX) clearing
 * `ratio_floor`.
 *
 * When `noise_dominated` is true, `direction` is null and `label` reads "NOISE-DOMINATED".
 * That is DIA's everyday case (0.9% against a 3% floor) and the UI must show the label, not
 * fall back to reading the sign of `net_gex` itself -- `docs/validation.md` section 9
 * establishes that DIA's sign flips under a plausible carry correction. */
export interface DealerPositioning {
  net_gex: number | null;
  abs_gex: number | null;
  ratio: number | null;
  ratio_floor: number | null;
  noise_dominated: boolean;
  direction: string | null;
  label: string;
  description: string;
}

/** One support or resistance strike. `side` is `RESISTANCE`, `SUPPORT` or `STRADDLING`. */
export interface ReportLevel {
  strike: number | null;
  net_gex: number | null;
  abs_gex: number | null;
  open_interest: number;
  distance: number | null;
  distance_pct: number | null;
  side: string;
  above_spot: boolean;
}

/** `resistance` and `support` are guaranteed disjoint and correctly ordered by the backend:
 * every resistance strike is above every support strike, always. Strikes that would break
 * that invariant (negative net gamma above spot, or positive below it) are a real market
 * condition and arrive in `straddling` with `overlapping` set and `overlap_note` explaining
 * it. Render that note -- do not merge `straddling` into either list. */
export interface LevelSet {
  resistance: ReportLevel[];
  support: ReportLevel[];
  straddling: ReportLevel[];
  overlapping: boolean;
  overlap_note: string | null;
  call_wall: number | null;
  put_wall: number | null;
  flip_point: number | null;
}

/** One screened contract. `mid` is null unless both sides are quoted. */
export interface PremiumCandidate {
  occ_symbol: string;
  strike: number | null;
  right: string;
  /** ISO date. */
  expiry: string;
  dte: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  iv: number | null;
  open_interest: number;
  distance_pct: number | null;
}

/** Screening output, never a recommendation -- label it as such wherever it renders.
 * Either side is legitimately empty when its wall sits far from spot; `note` says why, and
 * must be shown rather than leaving an unexplained blank section. */
export interface PremiumSelling {
  calls: PremiumCandidate[];
  puts: PremiumCandidate[];
  dte_min: number;
  dte_max: number;
  call_boundary: number | null;
  put_boundary: number | null;
  note: string | null;
}

/** `trigger` / `target` / `invalidation` are computed levels or null. A null means no
 * computed level sits there: render a dash, never a derived number. */
export interface PlaybookEntry {
  key: string;
  name: string;
  trigger: number | null;
  trigger_label: string;
  target: number | null;
  target_label: string;
  invalidation: number | null;
  invalidation_label: string;
  strategy: string;
}

/** The range fields are populated only when spot actually sits between the two walls. */
export interface Playbook {
  entries: PlaybookEntry[];
  range_low: number | null;
  range_high: number | null;
  range_magnet: number | null;
  spot_in_range: boolean;
}

/** `severity` is `INFO` or `WARNING`. */
export interface RiskAlert {
  code: string;
  severity: string;
  message: string;
  level: number | null;
}

// ---------------------------------------------------------------------------------------
// CFD translation (T41) -- `?cfd_spot=` re-expresses the report in the instrument the user
// actually trades. `null` on every field below whenever the request omitted `cfd_spot`;
// GENERATED the same way as the rest of this section, from `ReportOut`'s `CfdTranslationOut`.
// ---------------------------------------------------------------------------------------

/** One support/resistance/wall/max-pain level translated into CFD terms. `strike` is the
 * translated price; `native_strike` is the underlying's own, for a renderer that wants both.
 * `distance_pct` is **identical** to the native level's -- a pure scaling never changes a
 * percentage distance from spot, which is the T41 invariant to check this against. */
export interface CfdLevel {
  side: string;
  native_strike: number | null;
  strike: number | null;
  distance_pct: number | null;
}

/** One playbook entry's trigger/target/invalidation translated into CFD terms. `key` matches
 * the native `PlaybookEntry.key` it was translated from, for zipping the two tuples together. */
export interface CfdPlaybookEntry {
  key: string;
  trigger: number | null;
  target: number | null;
  invalidation: number | null;
}

/** One premium-screen row's *strike* translated into CFD terms -- never the premium, IV, bid
 * or ask, which stay the underlying's. `occ_symbol` is the join key back to the native
 * `PremiumCandidate`. */
export interface CfdPremiumCandidate {
  occ_symbol: string;
  strike: number | null;
}

/** The whole report re-expressed in the CFD instrument the user actually trades, anchored on
 * `ratio = cfd_spot / underlying_spot` -- derived fresh from the two spots every request,
 * never a stored constant (GLD's gold backing erodes with the trust's expense ratio, and any
 * cached ratio would silently rot). Carries no GEX magnitude, premium price or IV anywhere in
 * its shape: those never convert (see `backend/app/gex/report.py`'s `translate_to_cfd`).
 * `note` is the honesty text that must render wherever these numbers do. */
export interface CfdTranslation {
  underlying: Underlying;
  instrument: string;
  cfd_spot: number;
  underlying_spot: number;
  ratio: number;
  call_wall: CfdLevel | null;
  put_wall: CfdLevel | null;
  flip_point: CfdLevel | null;
  max_pain: CfdLevel | null;
  resistance: CfdLevel[];
  support: CfdLevel[];
  straddling: CfdLevel[];
  playbook: CfdPlaybookEntry[];
  premium_calls: CfdPremiumCandidate[];
  premium_puts: CfdPremiumCandidate[];
  note: string;
}

/** `GET /api/report/{underlying}?filter=&cfd_spot=` response body.
 *
 * Two narrowings from the generated output, both deliberate and both matching what
 * `GexResult` above already does:
 *   - `underlying: Underlying` rather than `string` -- the backend canonicalizes it through
 *     the same `Underlying` enum before it ever reaches a response.
 *   - `snapshot: SnapshotInfo` rather than a duplicate `SnapshotMeta` -- the generated
 *     `SnapshotMetaOut` is field-for-field identical to `SnapshotInfo` above, so reusing it
 *     keeps one staleness-badge type (T34) across the dashboard and the report.
 *
 * Nothing else was touched. `filter` stays a plain string because an explicit expiry list
 * echoes back as `"EXPIRIES:2026-09-18"`, outside the enum.
 *
 * `cfd` (T41) is `null` unless the request supplied `cfd_spot` -- the default, and every
 * fixture below's state, since none of them were captured with a CFD spot attached. Render
 * the converted levels *alongside* the native ones when it is present, never in their place. */
export interface Report {
  underlying: Underlying;
  filter: string;
  spot: number;
  /** ISO 8601 UTC. The snapshot's own instant, not the time the report was requested. */
  generated_at: string;
  snapshot: SnapshotInfo;
  max_pain: MaxPain;
  ratios: PutCallRatios;
  iv_regime: IvRegime;
  positioning: DealerPositioning;
  levels: LevelSet;
  premium: PremiumSelling;
  playbook: Playbook;
  alerts: RiskAlert[];
  summary: string[];
  cfd: CfdTranslation | null;
}
