import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LoadingState } from './LoadingState';

describe('LoadingState', () => {
  it('renders the message inside an aria-live="polite" element, so it is announced without polling', () => {
    render(<LoadingState message="Scoring opportunities across the universe…" />);
    const node = screen.getByText('Scoring opportunities across the universe…');
    expect(node).toHaveAttribute('aria-live', 'polite');
  });

  it('renders whatever it is given verbatim', () => {
    render(<LoadingState message="Loading the track record…" />);
    expect(screen.getByText('Loading the track record…')).toBeInTheDocument();
  });
});
