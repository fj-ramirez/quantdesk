import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '../../theme/ThemeContext';
import { Sparkline, type SparklinePoint } from './Sparkline';

// Same rationale as GexByStrike.test.tsx / App.test.tsx: `echarts-for-react` paints to a
// <canvas>, which jsdom does not implement. The stub lets this file assert wiring (the
// option it was handed) without a working canvas.
let lastOption: { series?: Array<{ data?: unknown[] }> } | undefined;
vi.mock('echarts-for-react', () => ({
  default: (props: { option: { series?: Array<{ data?: unknown[] }> } }) => {
    lastOption = props.option;
    return <div data-testid="echarts-stub" />;
  },
}));

function renderSparkline(data: SparklinePoint[], referenceValue?: number | null) {
  return render(
    <ThemeProvider>
      <Sparkline data={data} ariaLabel="ADX14 history" referenceValue={referenceValue} />
    </ThemeProvider>,
  );
}

describe('Sparkline', () => {
  it('renders the em-dash empty state when every point is null -- no data at all', () => {
    renderSparkline([
      { x: '2026-09-01', y: null },
      { x: '2026-09-02', y: null },
    ]);
    expect(screen.getByRole('img', { name: 'ADX14 history: no data' })).toHaveTextContent('—');
    expect(screen.queryByTestId('echarts-stub')).not.toBeInTheDocument();
  });

  it('renders the chart when at least one point is finite, passing nulls through unchanged', () => {
    renderSparkline([
      { x: '2026-09-01', y: 20 },
      { x: '2026-09-02', y: null },
      { x: '2026-09-03', y: 25 },
    ]);
    expect(screen.getByRole('img', { name: 'ADX14 history' })).toBeInTheDocument();
    expect(screen.getByTestId('echarts-stub')).toBeInTheDocument();
    expect(lastOption?.series?.[0]?.data).toEqual([20, null, 25]);
  });

  it('omits the reference markLine when referenceValue is not given', () => {
    renderSparkline([{ x: '2026-09-01', y: 20 }]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((lastOption?.series?.[0] as any).markLine).toBeUndefined();
  });

  it('draws a reference markLine at the given value', () => {
    renderSparkline([{ x: '2026-09-01', y: 20 }], 25);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const markLine = (lastOption?.series?.[0] as any).markLine;
    expect(markLine.data).toEqual([{ yAxis: 25 }]);
  });
});
