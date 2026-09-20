import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ThemeProvider } from '../../../../theme/ThemeContext';
import { StatusChip } from './StatusChip';

function renderChip(status: string | null, label?: string) {
  return render(
    <ThemeProvider>
      <StatusChip status={status} label={label} />
    </ThemeProvider>,
  );
}

describe('StatusChip', () => {
  it('renders "n/a" for a null status rather than an em dash or blank', () => {
    renderChip(null);
    expect(screen.getByText('n/a')).toBeInTheDocument();
  });

  it.each(['continued', 'failed', 'pending', 'continuation', 'mixed', 'fade', 'noise-dominated'])(
    'renders the %s status label verbatim',
    (status) => {
      renderChip(status);
      expect(screen.getByText(status)).toBeInTheDocument();
    },
  );

  it('accepts a label override for the displayed text', () => {
    renderChip('noise-dominated', 'NOISE-DOMINATED');
    expect(screen.getByText('NOISE-DOMINATED')).toBeInTheDocument();
  });

  it('carries colour on the border/dot, never as the text colour', () => {
    const { container } = renderChip('failed');
    const chip = container.querySelector('.status-chip') as HTMLElement;
    const label = container.querySelector('.status-chip__label') as HTMLElement;
    const dot = container.querySelector('.status-chip__dot') as HTMLElement;
    expect(chip.style.borderColor).not.toBe('');
    expect(dot.style.background).not.toBe('');
    // The label's own colour is the palette's text-primary token, not the status colour.
    expect(label.style.color).not.toBe(chip.style.borderColor);
  });
});
