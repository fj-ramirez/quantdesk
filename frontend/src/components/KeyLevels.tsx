/**
 * T14 — key levels card. A plain HTML table/definition card, not a chart, so it needs no
 * ECharts and no canvas: every value here is already reachable without hovering anything,
 * which is exactly the "table view" the dataviz skill asks a *chart* to also provide.
 *
 * `net_gex` is signed and the sign is meaningful (positive = dealers net long gamma, which
 * is read as a volatility-dampening regime; negative = net short, amplifying) — never take
 * an absolute value. Per the dataviz skill ("text never wears the data color"), the sign is
 * cued with a small dot + a written label, not by coloring the number itself.
 */
import type { KeyLevels as KeyLevelsData, SnapshotInfo } from '../api/types';
import { formatFreshness } from '../lib/time';
import { formatDistance, formatDistancePct, formatGex, formatStrike } from '../lib/format';
import { useTheme } from '../theme/ThemeContext';
import { vizPaletteFor } from '../theme/vizPalette';

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
    <section aria-label="Key levels" style={{ background: palette.surface, color: palette.textPrimary, padding: 16 }}>
      <h2 style={{ margin: 0, fontSize: 16 }}>
        {snapshot.underlying} key levels
      </h2>

      <div style={{ margin: '12px 0', display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span
          aria-hidden="true"
          style={{
            display: 'inline-block',
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: netDotColor,
          }}
        />
        <span style={{ fontSize: 13, color: palette.textSecondary }}>Net GEX</span>
        <strong style={{ fontSize: 20 }}>{formatGex(levels.net_gex)}</strong>
      </div>
      <p style={{ margin: '0 0 12px', fontSize: 13, color: palette.textSecondary }}>{netLabel}</p>

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

      {levels.flip_point == null && (
        <p style={{ margin: '8px 0 0', fontSize: 12, color: palette.textMuted }}>
          No sign change within the profile grid, so there is no gamma flip level to show.
        </p>
      )}

      {/* Same wording as the TopBar freshness badge (components/layout/TopBar.tsx
          `DataFreshnessBadge`, `lib/time.ts` `formatFreshness`) — one visual language for
          staleness across the app, not a second one invented here. T34: after the close this
          reads as "At Friday's close (4:00 PM ET)" rather than a rolling "Delayed 15m" that
          gets less honest the longer the page sits open in the evening. */}
      <p style={{ margin: '12px 0 0', fontSize: 12, color: palette.textMuted }} aria-live="polite">
        {formatFreshness(snapshot)}
        {snapshot.is_eod ? ' · EOD' : ''}
      </p>
    </section>
  );
}
