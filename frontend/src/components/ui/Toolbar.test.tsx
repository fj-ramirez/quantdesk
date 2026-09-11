import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SegmentedControl, Toolbar } from './Toolbar';

describe('SegmentedControl', () => {
  it('renders one button per value, marks the active one with aria-pressed, and fires onChange', () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        label="Min grade"
        values={[0, 45, 60, 75] as const}
        active={45}
        render={(v) => ({ 0: 'All', 45: 'C+', 60: 'B+', 75: 'A' })[v]}
        onChange={onChange}
      />,
    );
    const group = screen.getByRole('group', { name: 'Min grade' });
    expect(within(group).getByRole('button', { name: 'C+' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group).getByRole('button', { name: 'A' })).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(within(group).getByRole('button', { name: 'A' }));
    expect(onChange).toHaveBeenCalledWith(75);
  });
});

describe('Toolbar', () => {
  it('wraps its children in the shared toolbar row', () => {
    render(
      <Toolbar>
        <span>a control</span>
      </Toolbar>,
    );
    expect(screen.getByText('a control')).toBeInTheDocument();
  });
});
