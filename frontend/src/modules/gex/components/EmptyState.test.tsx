import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EmptyState } from './EmptyState';

describe('EmptyState', () => {
  it('renders the heading as the section\'s accessible name', () => {
    render(<EmptyState heading="No SPY snapshot captured yet" />);
    expect(screen.getByRole('region', { name: 'No SPY snapshot captured yet' })).toBeInTheDocument();
  });

  it('renders an explanation when given one, and omits the paragraph when not', () => {
    const { rerender } = render(<EmptyState heading="Scan is not built yet" />);
    expect(screen.queryByText(/./, { selector: '.empty-state__description' })).not.toBeInTheDocument();

    rerender(
      <EmptyState heading="No SPY snapshot captured yet">
        The end-of-day job runs at 16:20 ET on trading days.
      </EmptyState>,
    );
    expect(screen.getByText('The end-of-day job runs at 16:20 ET on trading days.')).toBeInTheDocument();
  });

  it('renders no action button when none is given', () => {
    render(<EmptyState heading="Scan is not built yet" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders and fires the action, and reflects its pending state', () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <EmptyState heading="No SPY snapshot captured yet" action={{ label: 'Capture now', onClick }} />,
    );
    const button = screen.getByRole('button', { name: 'Capture now' });
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <EmptyState
        heading="No SPY snapshot captured yet"
        action={{ label: 'Capture now', onClick, pending: true, pendingLabel: 'Capturing…' }}
      />,
    );
    expect(screen.getByRole('button', { name: 'Capturing…' })).toBeDisabled();
  });

  it('surfaces an action error message as an alert, never silently', () => {
    render(
      <EmptyState
        heading="No SPY snapshot captured yet"
        action={{ label: 'Capture now', onClick: () => {} }}
        errorMessage="Capture failed: network error"
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Capture failed: network error');
  });
});
