import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ThemeProvider } from '../../../../theme/ThemeContext';
import { PercentileBar } from './PercentileBar';

function renderBar(value: number | null, label?: string) {
  return render(
    <ThemeProvider>
      <PercentileBar value={value} label={label} />
    </ThemeProvider>,
  );
}

describe('PercentileBar', () => {
  it('renders null as an em dash, not a zero-width bar', () => {
    renderBar(null);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders a value as a rounded percent by default', () => {
    renderBar(0.667);
    expect(screen.getByText('67%')).toBeInTheDocument();
  });

  it('accepts a label override instead of the default formatted percent', () => {
    renderBar(0.14, 'n<5');
    expect(screen.getByText('n<5')).toBeInTheDocument();
    expect(screen.queryByText('14%')).not.toBeInTheDocument();
  });

  it('clamps an out-of-range value for the bar width without altering the displayed number', () => {
    const { container } = renderBar(1.4);
    expect(screen.getByText('140%')).toBeInTheDocument();
    const fill = container.querySelector('.percentile-bar__fill') as HTMLElement;
    expect(fill.style.width).toBe('100%');
  });
});
