/**
 * The property that matters here is not "a panel appears on hover" — it is that deferring the
 * text visually never removes it. If a future change swaps the `opacity` hide for
 * `display: none`, or drops `aria-describedby`, the explanation silently stops existing for a
 * screen reader and for Ctrl+F, which is exactly the failure this component was built to avoid
 * (see its docstring). Those two are asserted first.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { InfoTip } from './InfoTip';

const TEXT = 'A fixed-window correlation is an average over regimes, not a fact about today.';

describe('InfoTip', () => {
  it('keeps the explanation in the DOM even while it is visually hidden', () => {
    render(<InfoTip label="trailing-window estimates">{TEXT}</InfoTip>);
    expect(screen.getByText(TEXT)).toBeInTheDocument();
  });

  it('describes the button with the explanation, so a screen reader reads it as one thing', () => {
    render(<InfoTip label="trailing-window estimates">{TEXT}</InfoTip>);
    const button = screen.getByRole('button', { name: 'About trailing-window estimates' });
    expect(button).toHaveAccessibleDescription(TEXT);
  });

  it('is a real button, not a title attribute — reachable by keyboard and by tap', () => {
    render(<InfoTip label="the win column">{TEXT}</InfoTip>);
    const button = screen.getByRole('button', { name: 'About the win column' });
    button.focus();
    expect(button).toHaveFocus();
    expect(button).not.toHaveAttribute('title');
  });

  it('Escape dismisses the panel, and leaving re-arms it', () => {
    const { container } = render(<InfoTip label="the win column">{TEXT}</InfoTip>);
    const tip = container.querySelector('.info-tip')!;
    expect(tip).not.toHaveAttribute('data-dismissed');

    fireEvent.keyDown(screen.getByRole('button'), { key: 'Escape' });
    expect(tip).toHaveAttribute('data-dismissed', 'true');

    fireEvent.mouseLeave(tip);
    expect(tip).not.toHaveAttribute('data-dismissed');
  });

  it('anchors to the right edge when asked, for a tip near the end of a table', () => {
    const { container } = render(
      <InfoTip label="the win column" align="end">
        {TEXT}
      </InfoTip>,
    );
    expect(container.querySelector('.info-tip')).toHaveClass('info-tip--end');
  });
});
