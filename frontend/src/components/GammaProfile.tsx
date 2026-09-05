/**
 * T14 — gamma profile chart. Total dealer GEX as a function of a *hypothetical* spot
 * (PLAN.md §3.4): for every point on a grid around the current spot, the engine re-prices
 * every contract's gamma at that price and sums it. The zero crossing — the gamma flip
 * point — is the single most important feature (PLAN.md §3.3), so it gets the strongest
 * visual treatment on the chart: a bold dashed threshold line plus a ringed marker dot
 * exactly at (flip, 0), where the zero baseline and the flip line intersect.
 *
 * Two series (All, Ex-0DTE) are nominal-categorical identity, not magnitude or polarity —
 * see dataviz skill `references/choosing-a-form.md` "series-count ladder" and
 * `color-formula.md` "categorical". They get the palette's fixed slot-1/slot-2 hues
 * (blue/orange) plus a *non-color* cue (solid vs. dashed stroke) so the pair is legible
 * without relying on hue alone. The zero line, flip line and spot line are chart chrome —
 * not series — so they deliberately do NOT borrow a categorical or status color; they use
 * ink/baseline tokens, which also keeps them visually distinct from the two data series.
 */
import { useMemo } from 'react';
import type { EChartsOption } from 'echarts';
import ReactECharts from 'echarts-for-react';
import type { GammaProfilePoint } from '../api/types';
import { formatDistance, formatDistancePct, formatGex, formatStrike } from '../lib/format';
import { useTheme } from '../theme/ThemeContext';
import { vizPaletteFor, type VizPalette } from '../theme/vizPalette';

export interface GammaProfileProps {
  /** Profile grid for every expiry (T11 `GexResult.profile` with `filter=ALL`). */
  allProfile: GammaProfilePoint[];
  /** Same grid, 0DTE contracts excluded (T11 `GexResult.profile` with `filter=EX_ZERO_DTE`)
   * — 0DTE gamma dominates near the money and can make a same-day artifact look like a
   * structural level; this series is how the reader tells the two apart. */
  exZeroDteProfile: GammaProfilePoint[];
  /** The snapshot's actual current spot (`KeyLevels.spot` / `SnapshotInfo.spot`) — distinct
   * from the hypothetical grid values inside the profile arrays. */
  spot: number;
  /** `KeyLevels.flip_point`. Legitimately `null` when the profile has no sign change across
   * the grid (verified against the real QQQ fixture) — omit the marker entirely, never
   * coerce to 0. */
  flipPoint: number | null;
}

/** Builds the ECharts `option` from plain data + a resolved palette. Exported (and kept
 * free of React/DOM) so tests can assert on its structure — e.g. "no flip markLine/markPoint
 * when flipPoint is null" — without needing a canvas, which jsdom doesn't implement. */
export function buildGammaProfileOption(
  allProfile: GammaProfilePoint[],
  exZeroDteProfile: GammaProfilePoint[],
  spot: number,
  flipPoint: number | null,
  palette: VizPalette,
): EChartsOption {
  const toPairs = (points: GammaProfilePoint[]) => points.map((p) => [p.spot, p.total_gex] as [number, number]);

  const zeroLine = {
    yAxis: 0,
    lineStyle: { color: palette.baseline, width: 1.5, type: 'solid' as const },
    label: {
      formatter: () => '0',
      color: palette.textMuted,
      position: 'insideEndTop' as const,
    },
    symbol: 'none' as const,
  };

  const spotLine = {
    xAxis: spot,
    lineStyle: { color: palette.textSecondary, width: 1.5, type: 'dotted' as const },
    label: {
      formatter: () => `Spot ${formatStrike(spot)}`,
      color: palette.textSecondary,
      position: 'insideEndTop' as const,
    },
    symbol: 'none' as const,
  };

  // The whole point of this chart: make the flip line the loudest reference on it — full
  // ink-primary contrast and a heavier dashed stroke, versus the muted zero/spot lines.
  const flipLine =
    flipPoint == null
      ? null
      : {
          xAxis: flipPoint,
          lineStyle: { color: palette.textPrimary, width: 2, type: 'dashed' as const },
          label: {
            formatter: () => `Flip ${formatStrike(flipPoint)}`,
            color: palette.textPrimary,
            fontWeight: 'bold' as const,
            // T36 fallout: with the axis now actually fitted to the grid (see xAxis.scale
            // above) the flip line sits close enough to the spot line -- both legitimately
            // can, since flip is typically a few tens of points from spot -- that two labels
            // anchored to the same "insideEnd" (top) corner overlapped into an illegible
            // stack of characters. Anchoring this one to the *start* (bottom) of the line
            // instead keeps both readable regardless of how close together spot and flip
            // land.
            position: 'insideStartBottom' as const,
          },
          symbol: 'none' as const,
        };

  const markLineData = [zeroLine, spotLine, ...(flipLine ? [flipLine] : [])];

  // The literal zero-crossing marker: a ringed dot at (flip, 0), i.e. where the zero
  // baseline and the flip threshold line actually meet.
  const markPointData =
    flipPoint == null
      ? []
      : [
          {
            name: 'Gamma flip',
            coord: [flipPoint, 0],
            symbol: 'circle',
            symbolSize: 12,
            itemStyle: { color: palette.textPrimary, borderColor: palette.surface, borderWidth: 2 },
            label: { show: false },
          },
        ];

  return {
    backgroundColor: 'transparent',
    grid: { left: 72, right: 24, top: 56, bottom: 48, containLabel: true },
    legend: {
      data: ['All', 'Ex-0DTE'],
      top: 0,
      left: 'center',
      textStyle: { color: palette.textSecondary },
      icon: 'roundRect',
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { formatter: (p) => formatStrike(Number(p.value)) } },
      backgroundColor: palette.surface,
      borderColor: palette.gridline,
      textStyle: { color: palette.textPrimary },
      formatter: (params) => {
        const rows = Array.isArray(params) ? params : [params];
        const first = rows[0];
        const spotLabel = first && Array.isArray(first.value) ? formatStrike(Number(first.value[0])) : '';
        const lines = rows
          .filter((row) => row.seriesType === 'line')
          .map((row) => {
            const val = Array.isArray(row.value) ? Number(row.value[1]) : NaN;
            return `<div style="display:flex;justify-content:space-between;gap:12px;"><span>${row.marker ?? ''}${row.seriesName}</span><strong>${formatGex(val)}</strong></div>`;
          })
          .join('');
        return `<div style="min-width:160px;"><div style="margin-bottom:4px;color:${palette.textMuted};">Spot ${spotLabel}</div>${lines}</div>`;
      },
    },
    xAxis: {
      type: 'value',
      // T36: ECharts value axes default to `scale: false`, which forces the axis to include
      // 0 regardless of where the data actually sits. The profile grid is only ±10% around
      // spot (e.g. ~6,947-8,490 for a 7,718 SPX) -- forcing it through 0 stretched the axis
      // to 0-10,000 and squashed the entire curve, flip point included, into a sliver at the
      // right edge. `scale: true` fits the axis to the data's own extent instead.
      scale: true,
      name: 'Hypothetical spot',
      nameLocation: 'middle',
      nameGap: 32,
      nameTextStyle: { color: palette.textMuted },
      // `hideOverlap` (T36): a ~1,500-point grid still gets echarts' full default tick count
      // regardless of how narrow the container is -- on a 390px viewport the tick labels sit
      // closer together than their own text width and run together with no gap
      // ("7,2007,5007,8008,0008,400"). Hiding whichever overlaps keeps the remaining labels
      // legible instead of shrinking or rotating text that still has to share an axis with
      // the wide chart at 1280px.
      axisLabel: { color: palette.textMuted, formatter: (value: number) => formatStrike(value), hideOverlap: true },
      axisLine: { lineStyle: { color: palette.baseline } },
      splitLine: { lineStyle: { color: palette.gridline } },
    },
    yAxis: {
      type: 'value',
      // Same reasoning as the x-axis above: total gamma exposure at the *edges* of a ±10%
      // grid is dominated by whichever side is further from the flip, so the true range can
      // sit well clear of 0 on one side -- `scale: true` lets the axis fit that range instead
      // of always padding out to include 0.
      scale: true,
      name: 'Total dealer gamma exposure',
      nameLocation: 'middle',
      nameGap: 56,
      nameTextStyle: { color: palette.textMuted },
      // GEX magnitudes reach ±5e10 — a raw axis label would be unreadable, so every tick
      // goes through the same $B/$M compact formatter the rest of the app uses.
      axisLabel: { color: palette.textMuted, formatter: (value: number) => formatGex(value, 0) },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: palette.gridline } },
    },
    series: [
      {
        name: 'All',
        type: 'line',
        data: toPairs(allProfile),
        showSymbol: false,
        lineStyle: { color: palette.seriesAll, width: 2, type: 'solid' },
        itemStyle: { color: palette.seriesAll },
        z: 3,
        markLine: {
          silent: true,
          symbol: 'none',
          data: markLineData,
        },
        markPoint: {
          data: markPointData,
        },
      },
      {
        name: 'Ex-0DTE',
        type: 'line',
        data: toPairs(exZeroDteProfile),
        showSymbol: false,
        // Dashed stroke is the non-color cue distinguishing the two series — see file
        // header; a colorblind reader (or a grayscale printout) can still tell them apart.
        lineStyle: { color: palette.seriesExZeroDte, width: 2, type: 'dashed' },
        itemStyle: { color: palette.seriesExZeroDte },
        z: 2,
      },
    ],
  };
}

/** Merges two profile grids into one row set keyed by the hypothetical spot value, for the
 * table-view accessibility twin (dataviz skill: every chart needs a WCAG-clean table
 * equivalent — a canvas chart's data is otherwise reachable only by hovering it). Grids are
 * expected to share the same spot values (T11's `filter=ALL` vs `filter=EX_ZERO_DTE` calls
 * evaluate the same grid); a spot present in only one array still renders its own row with
 * a dash for the other column rather than being dropped. */
function mergeProfilesForTable(
  allProfile: GammaProfilePoint[],
  exZeroDteProfile: GammaProfilePoint[],
): { spot: number; all: number | null; exZeroDte: number | null }[] {
  const exByS = new Map(exZeroDteProfile.map((p) => [p.spot, p.total_gex]));
  const seen = new Set<number>();
  const rows: { spot: number; all: number | null; exZeroDte: number | null }[] = allProfile.map((p) => {
    seen.add(p.spot);
    return { spot: p.spot, all: p.total_gex, exZeroDte: exByS.get(p.spot) ?? null };
  });
  for (const p of exZeroDteProfile) {
    if (!seen.has(p.spot)) rows.push({ spot: p.spot, all: null, exZeroDte: p.total_gex });
  }
  return rows.sort((a, b) => a.spot - b.spot);
}

export function GammaProfile({ allProfile, exZeroDteProfile, spot, flipPoint }: GammaProfileProps) {
  const { theme } = useTheme();
  const palette = vizPaletteFor(theme);

  const option = useMemo(
    () => buildGammaProfileOption(allProfile, exZeroDteProfile, spot, flipPoint, palette),
    [allProfile, exZeroDteProfile, spot, flipPoint, palette],
  );

  const tableRows = useMemo(() => mergeProfilesForTable(allProfile, exZeroDteProfile), [allProfile, exZeroDteProfile]);

  return (
    <section aria-label="Gamma profile" style={{ background: palette.surface, color: palette.textPrimary, padding: 16 }}>
      <h2 style={{ margin: 0, fontSize: 16 }}>Gamma profile</h2>
      <p style={{ margin: '4px 0 0', color: palette.textSecondary, fontSize: 13 }}>
        Total dealer gamma exposure vs. hypothetical spot, all expiries vs. ex-0DTE.
      </p>
      {/* T14/T36: this chart always plots its own All / Ex-0DTE pair regardless of the
          Expiry filter selected in the top bar -- deliberately, since the filter's other
          options (e.g. ZERO_DTE after the close) can legitimately admit no contracts at all,
          which would make the one chart whose entire purpose is showing the flip point go
          blank. Called out here so that not reacting to the filter reads as a designed
          scope, not a bug -- see KeyLevels for the levels that DO honor the filter. */}
      <p style={{ margin: '2px 0 0', color: palette.textMuted, fontSize: 12 }}>
        Always shows all expiries — independent of the Expiry filter above.
      </p>
      <p style={{ margin: '4px 0 12px', fontSize: 13 }}>
        {flipPoint == null ? (
          <span style={{ color: palette.textMuted }}>No gamma flip point within the profile grid (no sign change).</span>
        ) : (
          <span>
            Flip point <strong>{formatStrike(flipPoint)}</strong> ({formatDistance(flipPoint, spot)} pts /{' '}
            {formatDistancePct(flipPoint, spot)} from spot).
          </span>
        )}
      </p>
      <ReactECharts option={option} notMerge style={{ height: 360, width: '100%' }} />
      <details>
        <summary>Table view</summary>
        <div style={{ maxHeight: 240, overflow: 'auto', marginTop: 8 }}>
          <table>
            <thead>
              <tr>
                <th scope="col">Hypothetical spot</th>
                <th scope="col">All</th>
                <th scope="col">Ex-0DTE</th>
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row) => (
                <tr key={row.spot}>
                  <td>{formatStrike(row.spot)}</td>
                  <td>{formatGex(row.all)}</td>
                  <td>{formatGex(row.exZeroDte)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
