import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '../../theme/ThemeContext';
import { VIZ_PALETTE_LIGHT } from '../../theme/vizPalette';
import { FlowSparkline, type FlowSparklinePoint } from './FlowSparkline';

let lastOption: { series?: Array<{ data?: unknown[] }> } | undefined;
vi.mock('echarts-for-react', () => ({
  default: (props: { option: { series?: Array<{ data?: unknown[] }> } }) => {
    lastOption = props.option;
    return <div data-testid="echarts-stub" />;
  },
}));

function renderFlowSparkline(points: FlowSparklinePoint[]) {
  return render(
    <ThemeProvider>
      <FlowSparkline points={points} ariaLabel="XLK flow trend" />
    </ThemeProvider>,
  );
}

describe('FlowSparkline', () => {
  it('renders the em-dash empty state when every window is null -- no computed flow at all', () => {
    renderFlowSparkline([
      { label: '5d', value: null },
      { label: '20d', value: null },
      { label: '60d', value: null },
    ]);
    expect(screen.getByRole('img', { name: 'XLK flow trend: no data' })).toHaveTextContent('—');
    expect(screen.queryByTestId('echarts-stub')).not.toBeInTheDocument();
  });

  it('omits a null window entirely -- never plots it as a zero-height bar', () => {
    renderFlowSparkline([
      { label: '5d', value: null },
      { label: '20d', value: 0.012 },
      { label: '60d', value: -0.004 },
    ]);
    expect(screen.getByTestId('echarts-stub')).toBeInTheDocument();
    expect(lastOption?.series?.[0]?.data).toHaveLength(2);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const values = (lastOption?.series?.[0]?.data as any[]).map((d) => d.value);
    expect(values).toEqual([0.012, -0.004]);
  });

  it('colours a positive point with divergingPositive and a negative point with divergingNegative', () => {
    renderFlowSparkline([
      { label: '20d', value: 0.02 },
      { label: '60d', value: -0.02 },
    ]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data = lastOption?.series?.[0]?.data as any[];
    expect(data[0].itemStyle.color).toBe(VIZ_PALETTE_LIGHT.divergingPositive);
    expect(data[1].itemStyle.color).toBe(VIZ_PALETTE_LIGHT.divergingNegative);
  });
});
