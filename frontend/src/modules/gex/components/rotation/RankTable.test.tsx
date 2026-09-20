import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { RankTable } from './RankTable';
import { quadrantOf, toRankRows } from './rankRows';
import type { RotationSymbol } from '../../api/types';
import { ThemeProvider } from '../../../../theme/ThemeContext';
import rotationSectorsFixture from '../../mocks/fixtures/scan/rotation_sectors.json';
import rotationSectorsNullFixture from '../../mocks/fixtures/scan/rotation_sectors_null.json';

const sectors = rotationSectorsFixture as unknown as { symbols: RotationSymbol[] };
const sectorsNull = rotationSectorsNullFixture as unknown as { symbols: RotationSymbol[] };

function renderTable(symbols: RotationSymbol[]) {
  const onSort = vi.fn();
  render(
    <ThemeProvider>
      <MemoryRouter>
        <RankTable symbols={symbols} sort={null} dir="desc" onSort={onSort} />
      </MemoryRouter>
    </ThemeProvider>,
  );
  return onSort;
}

describe('quadrantOf', () => {
  it('matches the boundaries RrgChart draws its markArea at, crossing at (100, 100)', () => {
    expect(quadrantOf(101, 101)).toBe('leading');
    expect(quadrantOf(101, 99)).toBe('weakening');
    expect(quadrantOf(99, 99)).toBe('lagging');
    expect(quadrantOf(99, 101)).toBe('improving');
    // The crosshair itself belongs to the >= 100 side on both axes, per the >= comparisons.
    expect(quadrantOf(100, 100)).toBe('leading');
  });

  it('returns null when either coordinate is null, rather than guessing a quadrant', () => {
    expect(quadrantOf(null, 101)).toBeNull();
    expect(quadrantOf(101, null)).toBeNull();
    expect(quadrantOf(null, null)).toBeNull();
  });
});

describe('toRankRows', () => {
  it('derives the quadrant from each symbol\'s latest valid trail point, not the API directly', () => {
    const rows = toRankRows(sectors.symbols);
    expect(rows).toHaveLength(sectors.symbols.length);
    for (const row of rows) {
      expect(row.quadrant).not.toBeUndefined();
    }
  });

  it('produces a null quadrant for a symbol whose entire trail is still in warm-up, never a guessed value', () => {
    const allWarmup: RotationSymbol = {
      symbol: 'NEWETF',
      trail: [{ date: '2026-09-04', rs_ratio_approx: null, rs_momentum_approx: null }],
      return_5: null,
      return_20: null,
      return_65: null,
    };
    const [row] = toRankRows([allWarmup]);
    expect(row.quadrant).toBeNull();
  });
});

describe('<RankTable />', () => {
  it('renders one row per symbol with a signed 1w/4w/13w return and a quadrant chip', () => {
    renderTable(sectors.symbols);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(sectors.symbols.length + 1); // +1 header row
    // XLK's 4-week (return_20) relative return is positive in the fixture.
    const xlkReturn20 = sectors.symbols.find((s) => s.symbol === 'XLK')!.return_20!;
    expect(xlkReturn20).toBeGreaterThan(0);
    expect(screen.getAllByText(/^\+/).length).toBeGreaterThan(0);
  });

  it('renders the neutral "n/a" chip for a symbol with no valid trail point at all, never a guessed quadrant', () => {
    const allWarmup: RotationSymbol = {
      symbol: 'NEWETF',
      trail: [{ date: '2026-09-04', rs_ratio_approx: null, rs_momentum_approx: null }],
      return_5: null,
      return_20: null,
      return_65: null,
    };
    renderTable([allWarmup]);
    expect(screen.getByText('n/a')).toBeInTheDocument();
  });

  it("still resolves a real quadrant for a symbol whose warm-up is only its earliest weeks (XLRE in the null fixture), since its latest point is valid", () => {
    const xlre = sectorsNull.symbols.find((s) => s.symbol === 'XLRE')!;
    expect(xlre.trail[xlre.trail.length - 1].rs_ratio_approx).not.toBeNull();
    renderTable(sectorsNull.symbols);
    expect(screen.queryAllByText('n/a')).toHaveLength(0);
  });

  it('calls onSort with the column key when a sortable header is activated', () => {
    const onSort = renderTable(sectors.symbols);
    screen.getByRole('button', { name: /4w/ }).click();
    expect(onSort).toHaveBeenCalledWith('return_20');
  });
});
