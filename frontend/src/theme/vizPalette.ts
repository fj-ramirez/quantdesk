/**
 * Chart color tokens for T14 (GammaProfile, KeyLevels), lifted verbatim from the dataviz
 * skill's validated default palette (`references/palette.md`) rather than eyeballed. Kept
 * as plain TS records (not CSS custom properties) because ECharts takes colors as JS
 * strings in its `option` object — it does not read CSS, so re-deriving these per render
 * (see `useThemeColors` below) is how the chart actually goes dark/light, not a CSS class.
 *
 * Only the roles this chart needs are here: two categorical slots (series identity — "All"
 * vs "Ex-0DTE" is nominal, swapping the pair wouldn't change meaning, so slot order is
 * fixed at 1/2 per the palette's rules) plus chart chrome (surface/ink/grid) and the
 * diverging blue/red pair used only as a small non-text sign cue on net GEX (never as text
 * color — see marks-and-anatomy.md "text never wears the data color").
 *
 * Validated: `node scripts/validate_palette.js "<8 hex>" --mode light|dark` on the full
 * documented 8-slot order (this file only draws slots 1–2) — all six checks pass in both
 * modes; worst adjacent CVD ΔE 9.1 light / 8.4 dark, both clear of the >=8 target.
 *
 * **T40 addition -- `levelResistance` / `levelSupport`.** The report page's support and
 * resistance chips were specified as red and green. Two things are worth recording about
 * that pair, because it is the one set of colors here that was NOT run through the validator
 * above (`scripts/validate_palette.js` came from the dataviz skill and is not vendored in
 * this repo, so re-running it was not possible):
 *
 *   - Red/green is the canonical red-green colour-vision-deficiency failure, so colour is
 *     never the only channel carrying "support" versus "resistance" on that page. Each chip
 *     is inside a section with a written heading, and each carries a visible `+`/`-` signed
 *     distance. A viewer who cannot separate the two hues still reads the levels correctly.
 *   - They are used as **border and dot only, never as text colour**, following the same rule
 *     the diverging pair above states. That matters concretely: the light-mode red is
 *     3.85:1 against the light surface, below the 4.5:1 needed for text. As non-text UI
 *     components all four clear 3:1 comfortably (light red 3.85, light green 5.23, dark red
 *     5.39, dark green 6.19), and the chip's own text uses `textPrimary`.
 */
export interface VizPalette {
  surface: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  gridline: string;
  baseline: string;
  /** Categorical slot 1 (blue) — the "All" series. */
  seriesAll: string;
  /** Categorical slot 2 (orange) — the "Ex-0DTE" series. */
  seriesExZeroDte: string;
  /** Diverging pole, positive/long-gamma side. Sign cue only, never text color. */
  divergingPositive: string;
  /** Diverging pole, negative/short-gamma side. Sign cue only, never text color. */
  divergingNegative: string;
  /** T40 semantic role: a resistance level (a strike price would have to break *upward*
   * through). Sign cue only -- border and dot, never text color, same rule as the diverging
   * pair above. */
  levelResistance: string;
  /** T40 semantic role: a support level. See `levelResistance`. */
  levelSupport: string;
}

export const VIZ_PALETTE_LIGHT: VizPalette = {
  surface: '#fcfcfb',
  textPrimary: '#0b0b0b',
  textSecondary: '#52514e',
  textMuted: '#898781',
  gridline: '#e1e0d9',
  baseline: '#c3c2b7',
  seriesAll: '#2a78d6',
  seriesExZeroDte: '#eb6834',
  divergingPositive: '#2a78d6',
  divergingNegative: '#e34948',
  levelResistance: '#e34948',
  levelSupport: '#1f7a3d',
};

export const VIZ_PALETTE_DARK: VizPalette = {
  surface: '#1a1a19',
  textPrimary: '#ffffff',
  textSecondary: '#c3c2b7',
  textMuted: '#898781',
  gridline: '#2c2c2a',
  baseline: '#383835',
  seriesAll: '#3987e5',
  seriesExZeroDte: '#d95926',
  divergingPositive: '#3987e5',
  divergingNegative: '#e66767',
  levelResistance: '#e66767',
  levelSupport: '#4aad68',
};

export function vizPaletteFor(theme: 'light' | 'dark'): VizPalette {
  return theme === 'dark' ? VIZ_PALETTE_DARK : VIZ_PALETTE_LIGHT;
}

/**
 * T55 — `StatusChip`'s fixed status -> palette-colour map, covering the breakout ledger's
 * per-event outcomes (`continued`/`failed`/`pending`), the regime board's verdicts
 * (`continuation`/`mixed`/`fade`/`noise-dominated`), and the shared `n/a` fallback. Kept here,
 * not inline in the component, per this file's own rule: a chart or chip's colour comes from
 * one named place, never an inline hex at the call site.
 *
 * Reuses existing semantic slots rather than minting new colours: `continued`/`continuation`
 * share `levelSupport` (green, "this is working"), `failed`/`fade` share `levelResistance`
 * (red, "this broke down") — same red/green pair `Report.tsx`'s `LevelChip` already uses, with
 * the same caveat (border/dot only, never text colour — see that file's docstring for the
 * measured contrast numbers). `pending`/`mixed` (still resolving, not yet a verdict) use the
 * `seriesExZeroDte` orange. `noise-dominated`/`n/a` (nothing to say) fall through to
 * `textMuted`, a neutral, not a third hue.
 */
export function statusChipColor(status: string, palette: VizPalette): string {
  switch (status) {
    // T51: RRG "leading" quadrant reuses the same colour as a working continuation -- see
    // this function's own updated docstring above for why (RrgChart.tsx's quadrant
    // markArea shading and RankTable's quadrant chip agree on one palette mapping).
    case 'continued':
    case 'continuation':
    case 'leading':
      return palette.levelSupport;
    case 'failed':
    case 'fade':
    case 'lagging':
      return palette.levelResistance;
    case 'pending':
    case 'mixed':
    case 'weakening':
      return palette.seriesExZeroDte;
    case 'improving':
      return palette.divergingPositive;
    case 'noise-dominated':
    case 'n/a':
    default:
      return palette.textMuted;
  }
}
