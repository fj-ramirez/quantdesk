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
  /** T122 semantic role: a strike straddling spot (gamma concentrated on both sides), neither
   * support nor resistance. Blue, the palette's neutral data hue, so it reads as "a level,
   * with no side" rather than as either of the red/green pair. */
  levelStraddling: string;
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
  levelStraddling: '#2a78d6',
};

export const VIZ_PALETTE_DARK: VizPalette = {
  // Chart *chrome* follows the shell: these five track index.css's dark `--code-bg`,
  // `--text-h`, `--text`, `--text-muted` and `--border` so a chart reads as part of the panel
  // it sits in rather than as a grey rectangle pasted onto a navy one. The data colours below
  // them are deliberately NOT re-tinted -- the categorical pair, the diverging pair and the
  // support/resistance pair each carry a documented meaning and a measured contrast figure
  // (see this file's header), and none of that is a theme decision.
  surface: '#0d1728',
  textPrimary: '#f4f7fb',
  textSecondary: '#9aaac2',
  textMuted: '#7c8ba3',
  gridline: '#1c2b45',
  baseline: '#28395c',
  seriesAll: '#3987e5',
  seriesExZeroDte: '#d95926',
  divergingPositive: '#3987e5',
  divergingNegative: '#e66767',
  levelResistance: '#e66767',
  levelSupport: '#4aad68',
  levelStraddling: '#3987e5',
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
 *
 * **T49 addition -- `stale`.** The regime board's `verdict: null` has two distinct causes
 * (`plans/continuation/03-regime-board.md`'s "Verified facts") and they must not look the
 * same: `noise-dominated` (a fresh chain with too little net gamma to trust a direction from)
 * keeps the neutral `textMuted` above, while `stale` (the chain itself predates its trading
 * day's close and the verdict is suppressed outright -- a data-quality fact, not a market
 * reading) takes `seriesAll`, the chart's neutral primary blue. Neither red/green/orange
 * verdict hue is reused for it, specifically so a reader never mistakes "this data is too old
 * to trust" for a market judgment.
 *
 * **T54 addition -- `contango`/`backwardation`.** `RegimeStrip`'s term-structure tiles reuse
 * the same red/green pair rather than a third hue: `contango` (the normal, upward-sloping
 * curve) shares `levelSupport` with `continuation`, `backwardation` (the curve inverted --
 * near-term stress) shares `levelResistance` with `fade`. `mixed` already shares the
 * `seriesExZeroDte` orange with `pending` above -- no new case needed for it.
 */
export function statusChipColor(status: string, palette: VizPalette): string {
  switch (status) {
    // T51: RRG "leading" quadrant reuses the same colour as a working continuation -- see
    // this function's own updated docstring above for why (RrgChart.tsx's quadrant
    // markArea shading and RankTable's quadrant chip agree on one palette mapping).
    case 'continued':
    case 'continuation':
    case 'leading':
    case 'contango':
      return palette.levelSupport;
    case 'failed':
    case 'fade':
    case 'lagging':
    case 'backwardation':
      return palette.levelResistance;
    case 'pending':
    case 'mixed':
    case 'weakening':
      return palette.seriesExZeroDte;
    // T60: an `active` opportunity (entry reachable now) shares `improving`'s blue -- a
    // "go" state that is deliberately *not* the red/green verdict pair, since an active fade
    // and an active continuation are both "active" and neither is a bullish/bearish claim.
    // `watch` (a fade whose wall is not yet within reach) is "still resolving", the same
    // reading `pending`/`mixed` already carry in orange (the case above). `rejected` falls
    // through to the neutral default below with `noise-dominated`: nothing to act on.
    case 'improving':
    case 'active':
      return palette.divergingPositive;
    case 'watch':
      return palette.seriesExZeroDte;
    // T61: outcome chips on the track record. `target` reuses the green `continued` already
    // means ("it worked"), `stop` the red `failed` means; `expired`/`untriggered` are the
    // neutral "nothing to say" grey with `noise-dominated`; `pending` keeps its orange above.
    case 'target':
      return palette.levelSupport;
    case 'stop':
      return palette.levelResistance;
    case 'stale':
      return palette.seriesAll;
    case 'noise-dominated':
    case 'n/a':
    default:
      return palette.textMuted;
  }
}
