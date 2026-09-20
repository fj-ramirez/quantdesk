/**
 * T51 -- the relative-rotation scatter (`/rotation`'s left panel, 07-ui.md's spec, plan
 * 04's "What the user sees"). Each symbol is a `weeks`-long trail ending in a labelled dot,
 * over four shaded quadrants crossing at (100, 100).
 *
 * **Naming discipline (07-ui.md's non-negotiables).** `rs_ratio_approx`/`rs_momentum_approx`
 * are the common *open approximation* of JdK RS-Ratio/RS-Momentum, which are proprietary and
 * patented -- this chart never claims parity. Axis names say "(approx.)" explicitly, and
 * `Rotation.tsx` renders the API's own `note` field verbatim beside the title rather than
 * paraphrasing it into a stronger claim.
 *
 * **A null coordinate is a real state, not a bug.** A symbol still inside the z-score
 * warm-up (plan 04: "fewer than `w` weeks after a new symbol is added to the universe...
 * return `None` rows") has `rs_ratio_approx`/`rs_momentum_approx` both `null` for its
 * earliest weeks. Those points are filtered out of a symbol's trail entirely (in
 * `rrgOption.ts`) -- never coerced to `(0, 0)` and never handed to echarts as a data point
 * with a null coordinate. A symbol with *no* valid point at all gets an empty trail and no
 * dot, rather than a crash.
 *
 * **Quadrant shading in dark theme (07-ui.md's "Likely first-contact failures").** ECharts
 * `markArea`'s default alpha renders opaque in dark theme; `rrgOption.ts`'s `quadrantFill`
 * always appends an explicit alpha suffix to a palette hex rather than trusting a default,
 * and the palette itself (`theme/vizPalette.ts`) is the only source of any colour here -- no
 * inline hex, same rule `GexByStrike.tsx` already follows.
 *
 * **Colour is not the identity channel.** Up to 12 symbols share this chart (plan 04's
 * "Verified facts": "twelve trails on one chart become unreadable... prefer labelling dots
 * over relying on colour alone"). Rather than trying to validate 12 mutually distinguishable,
 * CVD-safe hues, every trail cycles through a small four-colour set and leans on the
 * always-on label at each trail's terminal dot (plan 04: "each symbol drawn as a... trail
 * ending in a labelled dot") plus the legend's text for actual identification. Two symbols
 * can and do share a colour; that is an accepted trade-off, not an oversight.
 *
 * The pure option builder (`buildRrgOption`, tested directly against the produced
 * `EChartsOption` object -- `GexByStrike.tsx`'s house pattern for testing a chart under
 * jsdom, which has no `<canvas>`) lives in the sibling `rrgOption.ts`, not here: this file
 * exports only the component, which keeps it out of
 * `react-refresh/only-export-components` (the project's fixed count of 4 pre-existing
 * warnings must not grow).
 */
import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { RotationSymbol } from '../../api/types';
import { useTheme } from '../../../../theme/ThemeContext';
import { vizPaletteFor } from '../../../../theme/vizPalette';
import { buildRrgOption } from './rrgOption';

export interface RrgChartProps {
  symbols: RotationSymbol[];
  /** Plot height in px. Omitted by default, which fills 100% of the parent's height -- the
   * "square, min 480px" box is `Rotation.tsx`'s own CSS (`.rotation-layout__chart`'s
   * `aspect-ratio`/`min-width`), not a fixed number here, so this component's box always
   * matches whatever the page around it decided. */
  height?: number;
}

export function RrgChart({ symbols, height }: RrgChartProps) {
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

  const option = useMemo(() => buildRrgOption({ symbols, palette }), [symbols, palette]);

  return (
    <div style={{ width: '100%', height: '100%' }} role="img" aria-label="Relative rotation graph">
      <ReactECharts
        option={option}
        notMerge
        lazyUpdate={false}
        style={{ width: '100%', height: height ?? '100%' }}
        opts={{ renderer: 'canvas' }}
      />
    </div>
  );
}
