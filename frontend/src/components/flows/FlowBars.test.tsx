import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { FlowSymbol } from '../../api/types';
import { FlowBars } from './FlowBars';
import { buildFlowBarsOption } from './flowBarsOption';
import { VIZ_PALETTE_LIGHT } from '../../theme/vizPalette';
import { ThemeProvider } from '../../theme/ThemeContext';
import flows20Fixture from '../../mocks/fixtures/scan/flows_20.json';
import flowsSyntheticFixture from '../../mocks/fixtures/scan/flows_synthetic.json';

// Same house pattern as `RrgChart.test.tsx`/`Sparkline.test.tsx`: jsdom has no real <canvas>,
// so the mock captures the option object handed to echarts-for-react without ever painting.
let lastOption: unknown;
vi.mock('echarts-for-react', () => ({
  default: (props: { option: unknown }) => {
    lastOption = props.option;
    return <div data-testid="echarts-stub" />;
  },
}));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyOption = any;

const live = flows20Fixture as unknown as { symbols: FlowSymbol[] };
const synthetic = flowsSyntheticFixture as unknown as { symbols: FlowSymbol[] };

function renderFlowBars(symbols: FlowSymbol[]) {
  return render(
    <ThemeProvider>
      <FlowBars symbols={symbols} />
    </ThemeProvider>,
  );
}

describe('buildFlowBarsOption', () => {
  it('sorts descending (largest inflow first) and inverts the category axis so it renders at the top', () => {
    const option = buildFlowBarsOption({
      rows: [
        { symbol: 'A', flowPct: -0.01 },
        { symbol: 'B', flowPct: 0.02 },
        { symbol: 'C', flowPct: 0.005 },
      ],
      palette: VIZ_PALETTE_LIGHT,
    }) as AnyOption;
    expect(option.yAxis.data).toEqual(['B', 'C', 'A']);
    expect(option.yAxis.inverse).toBe(true);
    expect(option.series[0].data.map((d: AnyOption) => d.value)).toEqual([0.02, 0.005, -0.01]);
  });

  it('colours a positive bar with the diverging-positive palette slot and a negative bar with diverging-negative, and puts each label at the bar\'s own end', () => {
    const option = buildFlowBarsOption({
      rows: [
        { symbol: 'UP', flowPct: 0.01 },
        { symbol: 'DOWN', flowPct: -0.01 },
      ],
      palette: VIZ_PALETTE_LIGHT,
    }) as AnyOption;
    const up = option.series[0].data.find((d: AnyOption) => d.value === 0.01);
    const down = option.series[0].data.find((d: AnyOption) => d.value === -0.01);
    expect(up.itemStyle.color).toBe(VIZ_PALETTE_LIGHT.divergingPositive);
    expect(up.label.position).toBe('right');
    expect(down.itemStyle.color).toBe(VIZ_PALETTE_LIGHT.divergingNegative);
    expect(down.label.position).toBe('left');
  });
});

describe('FlowBars', () => {
  it('renders an honest empty state, never an empty chart canvas, when every fund is null today (the live case)', () => {
    expect(live.symbols.every((s) => s.flow_pct == null)).toBe(true);
    renderFlowBars(live.symbols);
    expect(
      screen.getByRole('region', { name: /No fund has a computed flow yet/i }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('echarts-stub')).not.toBeInTheDocument();
  });

  it('draws real bars, never a fabricated symbol, once at least one fund has a computed flow', () => {
    expect(synthetic.symbols.some((s) => s.flow_pct != null)).toBe(true);
    renderFlowBars(synthetic.symbols);
    expect(screen.getByTestId('echarts-stub')).toBeInTheDocument();
    const option = lastOption as AnyOption;
    const drawnSymbols: string[] = option.yAxis.data;
    const expectedSymbols = synthetic.symbols
      .filter((s) => s.flow_pct != null)
      .map((s) => s.symbol)
      .sort();
    expect([...drawnSymbols].sort()).toEqual(expectedSymbols);
    // XLK has real history but no computed flow yet (`history since ...`) -- it must never
    // appear as a bar, synthetic or not.
    expect(drawnSymbols).not.toContain('XLK');
  });
});
