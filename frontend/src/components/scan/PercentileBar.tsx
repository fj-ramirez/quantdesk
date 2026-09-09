/**
 * T55 — a 0..1 inline bar with its number beside it, for a cross-sectional percentile or
 * rate (`ScanTable`'s Rate and Composite columns, the regime board's Trend pct column).
 * Theme-aware via `vizPaletteFor` — never an inline hex, same rule every chart in this app
 * already follows (`theme/vizPalette.ts`).
 *
 * `null` (the common case at short lookbacks — 46 of 47 symbols at `lookback=40` have no
 * quoted rate) renders as an em dash, not an empty or zero-width bar: a zero-width bar reads
 * as "measured at 0%", which is a different, wrong fact from "not enough events to measure".
 */
import { useTheme } from '../../theme/ThemeContext';
import { vizPaletteFor } from '../../theme/vizPalette';
import { formatPct } from '../../lib/format';

const DASH = '—';

export interface PercentileBarProps {
  /** A 0..1 fraction, or `null` for "not enough data to measure". Values outside `[0, 1]`
   * are clamped for the bar's width only -- the displayed number is never altered. */
  value: number | null;
  /** Overrides the text beside the bar; defaults to `formatPct(value)`. */
  label?: string;
}

export function PercentileBar({ value, label }: PercentileBarProps) {
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

  if (value == null || !Number.isFinite(value)) {
    return (
      <span className="percentile-bar percentile-bar--empty">
        <span aria-hidden="true" className="percentile-bar__track" style={{ borderColor: palette.gridline }} />
        <span className="percentile-bar__value">{DASH}</span>
      </span>
    );
  }

  const clamped = Math.max(0, Math.min(1, value));

  return (
    <span className="percentile-bar">
      <span
        aria-hidden="true"
        className="percentile-bar__track"
        style={{ borderColor: palette.gridline, background: palette.gridline }}
      >
        <span
          className="percentile-bar__fill"
          style={{ width: `${clamped * 100}%`, background: palette.divergingPositive }}
        />
      </span>
      <span className="percentile-bar__value">{label ?? formatPct(value)}</span>
    </span>
  );
}
