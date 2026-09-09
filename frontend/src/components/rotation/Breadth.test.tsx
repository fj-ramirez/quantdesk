import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Breadth } from './Breadth';
import type { RotationBreadth } from '../../api/types';
import rotationSectorsFixture from '../../mocks/fixtures/scan/rotation_sectors.json';

const breadth = (rotationSectorsFixture as unknown as { breadth: RotationBreadth }).breadth;

describe('<Breadth />', () => {
  it('labels itself "sector-level breadth" and never claims constituent-level coverage', () => {
    render(<Breadth breadth={breadth} />);
    expect(screen.getByText('sector-level breadth')).toBeInTheDocument();
  });

  it('renders the above/below counts as a fraction over the evaluated denominator, not a hardcoded 11', () => {
    // A denominator that has shrunk from the usual 11 (e.g. a sector excluded for gappy
    // bars) must still render correctly -- this is the fact the plan's non-negotiables call
    // out by name ("using evaluated_20d/evaluated_50d as the denominator rather than
    // hardcoding 11").
    const shrunk: RotationBreadth = { ...breadth, above_20d: 2, evaluated_20d: 9, above_50d: 4, evaluated_50d: 9 };
    render(<Breadth breadth={shrunk} />);
    expect(screen.getByText('2 / 9')).toBeInTheDocument();
    expect(screen.getByText('4 / 9')).toBeInTheDocument();
  });

  it('renders the live fixture counts over the full 11-sector denominator', () => {
    render(<Breadth breadth={breadth} />);
    expect(screen.getByText(`${breadth.above_20d} / ${breadth.evaluated_20d}`)).toBeInTheDocument();
    expect(screen.getByText(`${breadth.above_50d} / ${breadth.evaluated_50d}`)).toBeInTheDocument();
  });

  it('carries the "why not constituent-level" explanation in a tooltip, not silently', () => {
    render(<Breadth breadth={breadth} />);
    const info = screen.getByRole('img', { name: /About this breadth reading/ });
    expect(info).toHaveAttribute('title');
    expect(info.getAttribute('title')).toMatch(/500/);
  });
});
