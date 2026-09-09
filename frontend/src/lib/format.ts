/**
 * Shared number formatting for GEX values, strikes and prices.
 *
 * GEX is dollars of dealer gamma per 1% move and routinely runs to tens of
 * billions, so raw numbers are unreadable and `Intl` currency formatting is too
 * verbose for chart tooltips and axis labels. Everything here is null-tolerant:
 * `flip_point` is legitimately null when the gamma profile has no sign change in
 * the ±10% grid, and `open_interest` distinguishes 0 from unknown.
 */

const DASH = "—";

/** Compact signed dollar magnitude: 52_300_000_000 -> "$52.3B", -4.5e8 -> "-$450.0M". */
export function formatGex(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(digits)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(digits)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(digits)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(digits)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

/** Strike or index level with thousands separators: 7710 -> "7,710", 770.5 -> "770.5". */
export function formatStrike(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
  });
}

/** Price with two decimals: 7710.17 -> "7,710.17". */
export function formatPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Signed distance from spot in points: (7750, 7710.17) -> "+39.83".
 * `spot` is nullable because `KeyLevels.spot` is: an empty selection (e.g. ZERO_DTE after
 * the close) has no spot to measure against, and no distance exists to report. */
export function formatDistance(
  level: number | null | undefined,
  spot: number | null | undefined,
): string {
  if (level == null || !Number.isFinite(level)) return DASH;
  if (spot == null || !Number.isFinite(spot)) return DASH;
  const diff = level - spot;
  return `${diff >= 0 ? "+" : ""}${diff.toFixed(2)}`;
}

/** Signed distance from spot in percent: (7750, 7710.17) -> "+0.52%". */
export function formatDistancePct(
  level: number | null | undefined,
  spot: number | null | undefined,
): string {
  if (level == null || !Number.isFinite(level)) return DASH;
  if (spot == null || !Number.isFinite(spot) || spot === 0) return DASH;
  const pct = ((level - spot) / spot) * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

/** Decimal-fraction IV to percent: 0.1061 -> "10.61%". Never assumes a percent input. */
export function formatIv(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `${(value * 100).toFixed(2)}%`;
}

/** Integer count, preserving the 0-vs-unknown distinction the schema guarantees. */
export function formatCount(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return value.toLocaleString("en-US");
}

// ---------------------------------------------------------------------------------------
// T55 additions -- the scan family's own three formatters (continuation rate, ATR-relative
// follow-through, IV/RV and similar ratios). Same null-tolerance discipline as the rest of
// this file: `rate=null` is the common case at short lookbacks (46 of 47 symbols at
// `lookback=40`), not an edge case, so every one of these renders a dash rather than a zero.
// ---------------------------------------------------------------------------------------

/** Fraction to a whole-percent figure: 0.667 -> "67%". Used for continuation rate and the
 * cross-sectional percentiles (`adx_pct`, `er_pct`, ...) that feed `PercentileBar`. */
export function formatPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `${Math.round(value * 100)}%`;
}

/** Signed ATR multiple: 1.04 -> "+1.04 ATR", -0.5 -> "-0.50 ATR". Used for follow-through
 * and excursion figures in the breakout ledger, which are always relative to a bar's ATR. */
export function formatAtr(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)} ATR`;
}

/** Ratio with a trailing multiplication sign: 1.72 -> "1.72×". Used for IV/RV and similar
 * dimensionless ratios in the trend/chop table. */
export function formatRatio(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return DASH;
  return `${value.toFixed(2)}×`;
}
