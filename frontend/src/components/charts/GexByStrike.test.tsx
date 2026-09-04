import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { StrikeGex } from '../../api/types';
import { buildGexByStrikeOption, GexByStrike, THEME_COLORS } from './GexByStrike';
import gexSpxFixture from '../../mocks/fixtures/gex-spx.json';
import gexQqqFixture from '../../mocks/fixtures/gex-qqq.json';
import { ThemeProvider } from '../../theme/ThemeContext';

// `echarts-for-react` renders to <canvas>, which jsdom does not implement (no `canvas`
// package in devDependencies, and adding one just to satisfy a test render would be
// testing echarts' own rendering, not this component's behavior). The option-builder tests
// below cover behavior directly; this mock lets the *component* tests assert wiring — that
// `GexByStrike` calls into the chart with a real, well-formed option and doesn't throw —
// without needing a working canvas.
let lastOption: unknown;
vi.mock('echarts-for-react', () => ({
  default: (props: { option: unknown }) => {
    lastOption = props.option;
    return <div data-testid="echarts-stub" />;
  },
}));

const spx = gexSpxFixture as unknown as {
  levels: { spot: number; call_wall: number; put_wall: number; flip_point: number | null };
  by_strike: StrikeGex[];
};
const qqq = gexQqqFixture as unknown as {
  levels: { spot: number; call_wall: number; put_wall: number; flip_point: number | null };
  by_strike: StrikeGex[];
};

// Reaching into a genuinely loosely-typed echarts option (tooltip can be an object or
// array; series is a large discriminated union) isn't worth fighting in test code.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyOption = any;

describe('buildGexByStrikeOption', () => {
  it('defaults the zoom window to spot ± 5%, in strike-points, when the chain covers that range', () => {
    const option = buildGexByStrikeOption({
      rows: spx.by_strike,
      spot: spx.levels.spot,
      callWall: spx.levels.call_wall,
      putWall: spx.levels.put_wall,
      flipPoint: spx.levels.flip_point,
      colors: THEME_COLORS.light,
    }) as AnyOption;

    const [inside, slider] = option.dataZoom;
    const expectedStart = spx.levels.spot * 0.95;
    const expectedEnd = spx.levels.spot * 1.05;
    expect(inside.startValue).toBeCloseTo(expectedStart, 5);
    expect(inside.endValue).toBeCloseTo(expectedEnd, 5);
    expect(slider.startValue).toBeCloseTo(expectedStart, 5);
    expect(slider.endValue).toBeCloseTo(expectedEnd, 5);
    // Sanity: the SPX fixture's chain (6900–8500) actually contains this window, so this
    // test is exercising the unclamped path, not accidentally passing via clamping.
    expect(expectedStart).toBeGreaterThan(6900);
    expect(expectedEnd).toBeLessThan(8500);
  });

  it('clamps the zoom window to the available strikes when spot sits near the edge of a thin chain', () => {
    // A chain that only extends 2 points above spot — narrower than a real ±5% window
    // (spot=600 -> 630 would need strikes out to 630). Mirrors the situation the QQQ mock
    // fixture is built to exercise: a short chain relative to spot.
    const rows: StrikeGex[] = [
      { strike: 598, call_gex: 1, put_gex: -1, net_gex: 0 },
      { strike: 600, call_gex: 2, put_gex: -1, net_gex: 1 },
      { strike: 602, call_gex: 3, put_gex: -1, net_gex: 2 },
    ];
    const option = buildGexByStrikeOption({
      rows,
      spot: 600,
      callWall: 602,
      putWall: 598,
      flipPoint: null,
      colors: THEME_COLORS.light,
    }) as AnyOption;

    const [inside] = option.dataZoom;
    // Unclamped ±5% would be [570, 630]; the chain only spans [598, 602].
    expect(inside.startValue).toBe(598);
    expect(inside.endValue).toBe(602);
  });

  it('omits the flip marker entirely when flip_point is null, and does not throw', () => {
    expect(qqq.levels.flip_point).toBeNull(); // guards the fixture itself still exercises this case
    let option: AnyOption;
    expect(() => {
      option = buildGexByStrikeOption({
        rows: qqq.by_strike,
        spot: qqq.levels.spot,
        callWall: qqq.levels.call_wall,
        putWall: qqq.levels.put_wall,
        flipPoint: qqq.levels.flip_point,
        colors: THEME_COLORS.dark,
      });
    }).not.toThrow();

    const netSeries = option!.series.find((s: AnyOption) => s.name === 'Net GEX');
    const markLineData = netSeries.markLine.data as AnyOption[];
    // Spot's markLine is always present; flip's must be absent (never coerced to 0).
    expect(markLineData).toHaveLength(1);
    expect(markLineData[0].xAxis).toBe(qqq.levels.spot);
    expect(markLineData.some((d) => d.label.formatter === 'Flip')).toBe(false);
    expect(markLineData.some((d) => d.xAxis === 0)).toBe(false);
  });

  it('includes both spot and flip markers when flip_point is present', () => {
    const option = buildGexByStrikeOption({
      rows: spx.by_strike,
      spot: spx.levels.spot,
      callWall: spx.levels.call_wall,
      putWall: spx.levels.put_wall,
      flipPoint: spx.levels.flip_point,
      colors: THEME_COLORS.light,
    }) as AnyOption;

    const netSeries = option.series.find((s: AnyOption) => s.name === 'Net GEX');
    const markLineData = netSeries.markLine.data as AnyOption[];
    expect(markLineData).toHaveLength(2);
    expect(markLineData.map((d) => d.label.formatter).sort()).toEqual(['Flip', 'Spot']);
  });

  it('puts calls on the positive bar series and puts on the negative one, signs preserved', () => {
    const option = buildGexByStrikeOption({
      rows: spx.by_strike,
      spot: spx.levels.spot,
      callWall: spx.levels.call_wall,
      putWall: spx.levels.put_wall,
      flipPoint: spx.levels.flip_point,
      colors: THEME_COLORS.light,
    }) as AnyOption;

    const callSeries = option.series.find((s: AnyOption) => s.name === 'Call GEX');
    const putSeries = option.series.find((s: AnyOption) => s.name === 'Put GEX');
    expect(callSeries.data.every((d: AnyOption) => d.value[1] >= 0)).toBe(true);
    expect(putSeries.data.every((d: AnyOption) => d.value[1] <= 0)).toBe(true);

    // Call/put wall bars carry the highlight border; every other bar doesn't.
    const highlightedCall = callSeries.data.filter((d: AnyOption) => d.itemStyle);
    expect(highlightedCall).toHaveLength(1);
    expect(highlightedCall[0].value[0]).toBe(spx.levels.call_wall);
  });

  it('formats the axis tooltip values as compact $M/$B via the shared formatter, values leading', () => {
    const option = buildGexByStrikeOption({
      rows: spx.by_strike,
      spot: spx.levels.spot,
      callWall: spx.levels.call_wall,
      putWall: spx.levels.put_wall,
      flipPoint: spx.levels.flip_point,
      colors: THEME_COLORS.light,
    }) as AnyOption;

    const row = spx.by_strike.find((r) => r.strike === 7850)!;
    const html = option.tooltip.formatter([
      { seriesName: 'Call GEX', value: [row.strike, row.call_gex] },
      { seriesName: 'Put GEX', value: [row.strike, row.put_gex] },
      { seriesName: 'Net GEX', value: [row.strike, row.net_gex] },
    ]);

    expect(html).toContain('7,850');
    expect(html).toMatch(/Call: <strong>\$[\d.]+B<\/strong>/);
    expect(html).toMatch(/Put: <strong>-\$[\d.]+B<\/strong>/);
    expect(html).toMatch(/Net: <strong>\$[\d.]+B<\/strong>/);
  });

  it('does not throw and returns an empty-safe option when rows is empty', () => {
    expect(() =>
      buildGexByStrikeOption({
        rows: [],
        spot: 100,
        callWall: 105,
        putWall: 95,
        flipPoint: null,
        colors: THEME_COLORS.light,
      }),
    ).not.toThrow();
  });
});

describe('<GexByStrike />', () => {
  it('renders without throwing on real SPX fixture data (flip present)', () => {
    render(
      <ThemeProvider>
        <GexByStrike
          rows={spx.by_strike}
          spot={spx.levels.spot}
          callWall={spx.levels.call_wall}
          putWall={spx.levels.put_wall}
          flipPoint={spx.levels.flip_point}
          underlying="SPX"
        />
      </ThemeProvider>,
    );
    expect(screen.getByRole('img', { name: /GEX by strike for SPX/ })).toBeInTheDocument();
    expect(screen.getByTestId('echarts-stub')).toBeInTheDocument();
  });

  it('renders without throwing on the QQQ fixture, whose flip_point is null', () => {
    render(
      <ThemeProvider>
        <GexByStrike
          rows={qqq.by_strike}
          spot={qqq.levels.spot}
          callWall={qqq.levels.call_wall}
          putWall={qqq.levels.put_wall}
          flipPoint={qqq.levels.flip_point}
          underlying="QQQ"
        />
      </ThemeProvider>,
    );
    expect(screen.getByRole('img', { name: /GEX by strike for QQQ/ })).toBeInTheDocument();
  });

  it('passes theme-specific colors into the option so light and dark render distinct palettes', () => {
    window.localStorage.setItem('gex-theme', 'light');
    render(
      <ThemeProvider>
        <GexByStrike rows={spx.by_strike} spot={spx.levels.spot} callWall={spx.levels.call_wall} putWall={spx.levels.put_wall} flipPoint={spx.levels.flip_point} />
      </ThemeProvider>,
    );
    const lightCallColor = (lastOption as AnyOption).series.find((s: AnyOption) => s.name === 'Call GEX').itemStyle.color;
    expect(lightCallColor).toBe(THEME_COLORS.light.call);

    // ThemeProvider reads its initial theme from localStorage on mount (src/theme/ThemeContext.tsx);
    // a fresh mount with 'dark' stored is what actually exercises the dark branch — flipping
    // document.documentElement.dataset on an already-mounted provider wouldn't change its state.
    window.localStorage.setItem('gex-theme', 'dark');
    render(
      <ThemeProvider>
        <GexByStrike rows={spx.by_strike} spot={spx.levels.spot} callWall={spx.levels.call_wall} putWall={spx.levels.put_wall} flipPoint={spx.levels.flip_point} />
      </ThemeProvider>,
    );
    const darkCallColor = (lastOption as AnyOption).series.find((s: AnyOption) => s.name === 'Call GEX').itemStyle.color;
    expect(darkCallColor).toBe(THEME_COLORS.dark.call);
    expect(darkCallColor).not.toBe(lightCallColor);
  });
});
