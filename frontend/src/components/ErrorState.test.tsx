import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ErrorState } from './ErrorState';

describe('ErrorState', () => {
  it('renders the message inside a role="alert" element', () => {
    render(<ErrorState message="Failed to load SPY GEX: network error" />);
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load SPY GEX: network error');
  });

  it('renders whatever it is given verbatim -- it does not parse or reformat the message', () => {
    render(<ErrorState message={'snapshot 3 is indexed but its Parquet file is missing'} />);
    expect(screen.getByRole('alert').textContent).toBe('snapshot 3 is indexed but its Parquet file is missing');
  });
});
