/**
 * T14 — key levels card. A plain HTML table/definition card, not a chart, so it needs no
 * ECharts and no canvas: every value here is already reachable without hovering anything,
 * which is exactly the "table view" the dataviz skill asks a *chart* to also provide.
 *
 * `net_gex` is signed and the sign is meaningful (positive = dealers net long gamma, which
 * is read as a volatility-dampening regime; negative = net short, amplifying) — never take
 * an absolute value. Per the dataviz skill ("text never wears the data color"), the sign is
 * cued with a small dot + a written label, not by coloring the number itself.
 *
 * T66: the section/heading/net-GEX row/footer that used to carry 9 inline `style={{...}}`
 * clusters now render through `Surface` (the card background/border/padding) plus dedicated
 * `.key-levels__*` classes in index.css, all reading T63's semantic text/space tokens —
 * same values, same null-vs-zero handling (CLAUDE.md invariant #3: a `null` open-interest
 * upstream is already excluded before this component ever sees a level, and every null level
 * here still renders as an honest em dash via `formatStrike`/`formatDistance`, never a
 * fabricated 0). The one color that stays computed in JS rather than moved to a CSS class is
 * the net-GEX sign dot: it must keep exactly the blue/red pair `theme/vizPalette.ts` already
 * defines for this chart-adjacent sign cue (`divergingPositive`/`divergingNegative`), not the
 * app's generic green/red status tokens, which are a different semantic pairing (see this
 * component's own history — the values are unchanged, only their container is).
 */
import type { KeyLevels as KeyLevelsData, SnapshotInfo } from '../api/types';
import { formatFreshness } from '../lib/time';
import { formatDistance, formatDistancePct, formatGex, formatStrike } from '../lib/format';
import { useTheme } from '../theme/ThemeContext';
import { vizPaletteFor } from '../theme/vizPalette';
import { Surface } from './ui/Surface';

export interface KeyLevelsProps {
  levels: KeyLevelsData;
  snapshot: SnapshotInfo;
}

interface LevelRow {
  label: string;
  value: number | null;
}

export function KeyLevels({ levels, snapshot }: KeyLevelsProps) {
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

  const rows: LevelRow[] = [
    { label: 'Gamma flip', value: levels.flip_point },
    { label: 'Call wall', value: levels.call_wall },
    { label: 'Put wall', value: levels.put_wall },
    { label: 'Max |gamma| strike', value: levels.max_abs_strike },
  ];

  const netSign = levels.net_gex > 0 ? 'positive' : levels.net_gex < 0 ? 'negative' : 'flat';
  const netDotColor =
    netSign === 'positive' ? palette.divergingPositive : netSign === 'negative' ? palette.divergingNegative : palette.textMuted;
  const netLabel =
    netSign === 'positive'
      ? 'Dealers net long gamma — hedging tends to dampen moves'
      : netSign === 'negative'
        ? 'Dealers net short gamma — hedging tends to amplify moves'
        : 'Net gamma flat';

  return (
    <Surface as="section" aria-label="Key levels" className="key-levels" level="raised">
      <h2 className="key-levels__title">{snapshot.underlying} key levels</h2>

      <div className="key-levels__net">
        <span aria-hidden="true" className="key-levels__net-dot" style={{ background: netDotColor }} />
        <span className="key-levels__net-label">Net GEX</span>
        <strong className="key-levels__net-value">{formatGex(levels.net_gex)}</strong>
      </div>
      <p className="key-levels__net-note">{netLabel}</p>

      {/* T68: a bare table with no scroll container let its 4 columns push the whole page
          wider than the viewport at 390px (page-level horizontal overflow/clipping, not a
          contained table scroll) -- wrapped in the same `.scan-table-container` (overflow-x:
          auto) every scan-family table already uses, so a narrow viewport scrolls the table
          only, per the plan's "contained horizontal scrolling" rule. No column/row/value
          changed. */}
      <div className="scan-table-container" tabIndex={0}>
      <table>
        <thead>
          <tr>
            <th scope="col">Level</th>
            <th scope="col">Value</th>
            <th scope="col">Distance (pts)</th>
            <th scope="col">Distance (%)</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Spot</th>
            <td>{formatStrike(levels.spot)}</td>
            <td>—</td>
            <td>—</td>
          </tr>
          {rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              <td>{formatStrike(row.value)}</td>
              <td>{formatDistance(row.value, levels.spot)}</td>
              <td>{formatDistancePct(row.value, levels.spot)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      {levels.flip_point == null && (
        <p className="key-levels__note">
          No sign change within the profile grid, so there is no gamma flip level to show.
        </p>
      )}

      {/* Same wording as the TopBar freshness badge (components/layout/ContextBar.tsx
          `DataFreshnessBadge`, `lib/time.ts` `formatFreshness`) — one visual language for
          staleness across the app, not a second one invented here. T34: after the close this
          reads as "At Friday's close (4:00 PM ET)" rather than a rolling "Delayed 15m" that
          gets less honest the longer the page sits open in the evening. */}
      <p className="key-levels__freshness" aria-live="polite">
        {formatFreshness(snapshot)}
        {snapshot.is_eod ? ' · EOD' : ''}
      </p>
    </Surface>
  );
}
