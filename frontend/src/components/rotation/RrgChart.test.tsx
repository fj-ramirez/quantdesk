import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { RotationSymbol } from '../../api/types';
import { RrgChart } from './RrgChart';
import { buildRrgOption } from './rrgOption';
import { VIZ_PALETTE_DARK, VIZ_PALETTE_LIGHT } from '../../theme/vizPalette';
import { ThemeProvider } from '../../theme/ThemeContext';
import rotationAssetsFixture from '../../mocks/fixtures/scan/rotation_assets.json';
import rotationSectorsNullFixture from '../../mocks/fixtures/scan/rotation_sectors_null.json';
import rotationSectorsFixture from '../../mocks/fixtures/scan/rotation_sectors.json';

// Same reasoning and the same house pattern as `GexByStrike.test.tsx`'s echarts mock: jsdom
// has no <canvas>, so a rendered-chart test would test the charting library, not this
// component. The mock captures the option object the component hands over.
let lastOption: unknown;
vi.mock('echarts-for-react', () => ({
  default: (props: { option: unknown }) => {
    lastOption = props.option;
    return <div data-testid="echarts-stub" />;
  },
}));

const assets = rotationAssetsFixture as unknown as { symbols: RotationSymbol[] };
const sectorsNull = rotationSectorsNullFixture as unknown as { symbols: RotationSymbol[] };
const sectors = rotationSectorsFixture as unknown as { symbols: RotationSymbol[] };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyOption = any;

function findSeries(option: AnyOption, name: string, type: 'line' | 'scatter') {
  return option.series.find((s: AnyOption) => s.name === name && s.type === type);
}

describe('buildRrgOption', () => {
  it('renders SPY, benchmarked against itself, exactly on the (100, 100) crosshair', () => {
    // group=assets&benchmark=SPY is a real live case (07-ui.md's acceptance line names it
    // by name), not a contrived fixture: SPY vs. its own benchmark sits at (100, 100) on
    // every trail week.
    const option = buildRrgOption({ symbols: assets.symbols, palette: VIZ_PALETTE_LIGHT }) as AnyOption;
    const dot = findSeries(option, 'SPY', 'scatter');
    expect(dot.data).toHaveLength(1);
    expect(dot.data[0].value).toEqual([100, 100]);

    const quadrants = option.series.find((s: AnyOption) => s.name === '__quadrants__');
    const crosshair = quadrants.markLine.data;
    expect(crosshair).toEqual(expect.arrayContaining([{ xAxis: 100 }, { yAxis: 100 }]));
  });

  it('fades trail points from faint (oldest) to solid (newest), and never draws a marker on the trail line for the terminal point (the dedicated dot series draws it instead)', () => {
    const option = buildRrgOption({ symbols: sectors.symbols, palette: VIZ_PALETTE_LIGHT }) as AnyOption;
    const xlk = findSeries(option, 'XLK', 'line');
    expect(xlk.data.length).toBeGreaterThan(1);
    const opacities = xlk.data.map((d: AnyOption) => d.itemStyle.opacity);
    for (let i = 1; i < opacities.length; i++) {
      expect(opacities[i]).toBeGreaterThanOrEqual(opacities[i - 1]);
    }
    expect(opacities[opacities.length - 1]).toBeGreaterThan(opacities[0]);
    // The terminal trail point is invisible (symbolSize 0) -- the dot series draws the
    // emphasised, labelled marker at the same coordinate instead.
    expect(xlk.data[xlk.data.length - 1].symbolSize).toBe(0);

    const dot = findSeries(option, 'XLK', 'scatter');
    expect(dot.data[0].symbolSize).toBeGreaterThan(0);
    expect(dot.data[0].label.show).toBe(true);
    expect(dot.data[0].label.formatter).toBe('XLK');
    // The dot sits at the exact same coordinate as the trail's last (invisible) point.
    expect(dot.data[0].value).toEqual(xlk.data[xlk.data.length - 1].value);
  });

  it('omits a null-coordinate trail point entirely rather than plotting it at (0, 0)', () => {
    const xlre = sectorsNull.symbols.find((s) => s.symbol === 'XLRE')!;
    expect(xlre.trail[0].rs_ratio_approx).toBeNull();
    expect(xlre.trail[1].rs_ratio_approx).toBeNull();

    const option = buildRrgOption({ symbols: sectorsNull.symbols, palette: VIZ_PALETTE_LIGHT }) as AnyOption;
    const line = findSeries(option, 'XLRE', 'line');
    // Two of six trail weeks are null -- only four points reach the chart.
    expect(line.data).toHaveLength(4);
    for (const point of line.data) {
      expect(point.value[0]).not.toBe(0);
      expect(point.value[1]).not.toBe(0);
    }
  });

  it('gives every symbol a scatter "dot" series even when its trail is entirely null, with empty data rather than a crash', () => {
    const allNull: RotationSymbol = {
      symbol: 'NEWETF',
      trail: [
        { date: '2026-08-28', rs_ratio_approx: null, rs_momentum_approx: null },
        { date: '2026-09-04', rs_ratio_approx: null, rs_momentum_approx: null },
      ],
      return_5: null,
      return_20: null,
      return_65: null,
    };
    expect(() => buildRrgOption({ symbols: [allNull], palette: VIZ_PALETTE_LIGHT })).not.toThrow();
    const option = buildRrgOption({ symbols: [allNull], palette: VIZ_PALETTE_LIGHT }) as AnyOption;
    const dot = findSeries(option, 'NEWETF', 'scatter');
    const line = findSeries(option, 'NEWETF', 'line');
    expect(dot.data).toEqual([]);
    expect(line.data).toEqual([]);
  });

  it('sets an explicit alpha on every quadrant markArea colour, and the colour differs between light and dark theme', () => {
    const light = buildRrgOption({ symbols: sectors.symbols, palette: VIZ_PALETTE_LIGHT }) as AnyOption;
    const dark = buildRrgOption({ symbols: sectors.symbols, palette: VIZ_PALETTE_DARK }) as AnyOption;
    const lightAreas = light.series.find((s: AnyOption) => s.name === '__quadrants__').markArea.data;
    const darkAreas = dark.series.find((s: AnyOption) => s.name === '__quadrants__').markArea.data;
    expect(lightAreas).toHaveLength(4);
    for (const [start] of lightAreas) {
      // 6-digit hex + explicit 2-digit alpha suffix -- 8 hex digits after the `#`, never a
      // bare colour relying on echarts' own (opaque-in-dark-theme) markArea default.
      expect(start.itemStyle.color).toMatch(/^#[0-9a-fA-F]{8}$/);
    }
    const lightColors = lightAreas.map(([start]: AnyOption) => start.itemStyle.color);
    const darkColors = darkAreas.map(([start]: AnyOption) => start.itemStyle.color);
    expect(lightColors).not.toEqual(darkColors);
    expect(lightAreas.map(([start]: AnyOption) => start.name).sort()).toEqual([
      'Improving',
      'Lagging',
      'Leading',
      'Weakening',
    ]);
  });

  it('does not throw and returns an empty-safe option when symbols is empty', () => {
    expect(() => buildRrgOption({ symbols: [], palette: VIZ_PALETTE_LIGHT })).not.toThrow();
  });

  it('labels the axes with "(approx.)", never claiming parity with JdK RS-Ratio/RS-Momentum', () => {
    const option = buildRrgOption({ symbols: sectors.symbols, palette: VIZ_PALETTE_LIGHT }) as AnyOption;
    expect(option.xAxis.name).toBe('RS-ratio (approx.)');
    expect(option.yAxis.name).toBe('RS-momentum (approx.)');
  });
});

describe('<RrgChart />', () => {
  it('renders without throwing on real fixture data', () => {
    render(
      <ThemeProvider>
        <RrgChart symbols={sectors.symbols} />
      </ThemeProvider>,
    );
    expect(screen.getByRole('img', { name: /Relative rotation graph/ })).toBeInTheDocument();
    expect(screen.getByTestId('echarts-stub')).toBeInTheDocument();
  });

  it('passes theme-specific palette colours into the option so light and dark render distinct quadrant fills', () => {
    window.localStorage.setItem('gex-theme', 'light');
    render(
      <ThemeProvider>
        <RrgChart symbols={sectors.symbols} />
      </ThemeProvider>,
    );
    const lightQuadrants = (lastOption as AnyOption).series.find((s: AnyOption) => s.name === '__quadrants__');
    const lightColor = lightQuadrants.markArea.data[0][0].itemStyle.color;

    window.localStorage.setItem('gex-theme', 'dark');
    render(
      <ThemeProvider>
        <RrgChart symbols={sectors.symbols} />
      </ThemeProvider>,
    );
    const darkQuadrants = (lastOption as AnyOption).series.find((s: AnyOption) => s.name === '__quadrants__');
    const darkColor = darkQuadrants.markArea.data[0][0].itemStyle.color;

    expect(darkColor).not.toBe(lightColor);
  });
});
