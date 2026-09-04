import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider } from '../theme/ThemeContext';
import { VIZ_PALETTE_LIGHT } from '../theme/vizPalette';
import { GammaProfile, buildGammaProfileOption } from './GammaProfile';
import type { GammaProfilePoint } from '../api/types';

// Small synthetic grid that actually crosses zero, so `flipPoint` below is a real feature
// of the curve rather than an arbitrary number — mirrors the shape of the real SPX/SPY
// fixtures (negative at the low end, positive at the high end).
const ALL_PROFILE: GammaProfilePoint[] = [
  { spot: 90, total_gex: -20_000_000_000 },
  { spot: 95, total_gex: -5_000_000_000 },
  { spot: 100, total_gex: 10_000_000_000 },
  { spot: 105, total_gex: 25_000_000_000 },
  { spot: 110, total_gex: 40_000_000_000 },
];
const EX_ZERO_DTE_PROFILE: GammaProfilePoint[] = ALL_PROFILE.map((p) => ({ spot: p.spot, total_gex: p.total_gex * 0.8 }));

// The real QQQ fixture (src/mocks/fixtures/gex-qqq.json): profile stays positive across the
// whole ±10% grid, so `flip_point` is legitimately `null` — this is the exact shape that
// broke a naive "always draw a marker at flip_point" implementation.
const QQQ_LIKE_PROFILE: GammaProfilePoint[] = [
  { spot: 540, total_gex: 12_954_145_168 },
  { spot: 600, total_gex: 13_523_110_420 },
  { spot: 660, total_gex: 14_092_075_672 },
];

function renderGammaProfile(props: Parameters<typeof GammaProfile>[0]) {
  return render(
    <ThemeProvider>
      <GammaProfile {...props} />
    </ThemeProvider>,
  );
}

describe('buildGammaProfileOption', () => {
  it('renders both series, distinguished by more than just color', () => {
    const option = buildGammaProfileOption(ALL_PROFILE, EX_ZERO_DTE_PROFILE, 100, 97.5, VIZ_PALETTE_LIGHT);
    const series = option.series as { name: string; lineStyle: { color: string; type: string } }[];
    expect(series).toHaveLength(2);
    expect(series.map((s) => s.name)).toEqual(['All', 'Ex-0DTE']);
    // Different hues (categorical identity)...
    expect(series[0].lineStyle.color).not.toBe(series[1].lineStyle.color);
    // ...AND a non-color cue (stroke pattern), so a colorblind reader or a grayscale
    // printout can still tell them apart.
    expect(series[0].lineStyle.type).toBe('solid');
    expect(series[1].lineStyle.type).toBe('dashed');
  });

  it('adds a zero line, a spot line and a flip line + marker when flipPoint is a number', () => {
    const option = buildGammaProfileOption(ALL_PROFILE, EX_ZERO_DTE_PROFILE, 100, 97.5, VIZ_PALETTE_LIGHT);
    const firstSeries = (option.series as Array<{ markLine: { data: unknown[] }; markPoint: { data: unknown[] } }>)[0];
    expect(firstSeries.markLine.data).toHaveLength(3); // zero, spot, flip
    expect(firstSeries.markPoint.data).toHaveLength(1);
    const markPoint = firstSeries.markPoint.data[0] as { coord: [number, number] };
    expect(markPoint.coord).toEqual([97.5, 0]);
  });

  it('omits the flip line and marker entirely when flipPoint is null — never draws at 0', () => {
    const option = buildGammaProfileOption(QQQ_LIKE_PROFILE, QQQ_LIKE_PROFILE, 600, null, VIZ_PALETTE_LIGHT);
    const firstSeries = (option.series as Array<{ markLine: { data: unknown[] }; markPoint: { data: unknown[] } }>)[0];
    expect(firstSeries.markLine.data).toHaveLength(2); // zero, spot only
    expect(firstSeries.markPoint.data).toHaveLength(0);
  });
});

describe('GammaProfile component', () => {
  it('renders without throwing and shows the flip point caption when present', () => {
    renderGammaProfile({ allProfile: ALL_PROFILE, exZeroDteProfile: EX_ZERO_DTE_PROFILE, spot: 100, flipPoint: 97.5 });
    expect(screen.getByText(/Flip point/)).toBeInTheDocument();
    expect(screen.getByText('97.5')).toBeInTheDocument();
  });

  it('renders without throwing and shows a no-flip explanation for the QQQ-shaped null case', () => {
    renderGammaProfile({ allProfile: QQQ_LIKE_PROFILE, exZeroDteProfile: QQQ_LIKE_PROFILE, spot: 600, flipPoint: null });
    expect(screen.getByText(/No gamma flip point within the profile grid/)).toBeInTheDocument();
  });

  it('table view (the accessibility twin) lists both series for every grid point', () => {
    renderGammaProfile({ allProfile: ALL_PROFILE, exZeroDteProfile: EX_ZERO_DTE_PROFILE, spot: 100, flipPoint: 97.5 });
    expect(screen.getByRole('columnheader', { name: 'All' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Ex-0DTE' })).toBeInTheDocument();
    // spot=110 -> All total_gex is $40.0B; row must exist and be reachable without hovering
    // the canvas.
    const row = screen.getByText('110').closest('tr');
    expect(row).not.toBeNull();
    expect(row!.textContent).toContain('$40.0B');
  });
});
