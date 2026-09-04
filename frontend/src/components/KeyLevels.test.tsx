import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider } from '../theme/ThemeContext';
import { KeyLevels } from './KeyLevels';
import type { KeyLevels as KeyLevelsData, SnapshotInfo } from '../api/types';

// Numbers lifted from the real SPX fixture (src/mocks/fixtures/gex-spx.json) so the signed
// distances below are checked against genuine data, not fabricated round numbers.
const SPX_LEVELS: KeyLevelsData = {
  net_gex: 48_912_826_098,
  call_wall: 7850,
  put_wall: 7550,
  max_abs_strike: 7850,
  flip_point: 7649.71,
  spot: 7711.4,
  computed_at: '2026-09-04T15:45:00Z',
};
const SPX_SNAPSHOT: SnapshotInfo = {
  id: 1001,
  underlying: 'SPX',
  captured_at: '2026-09-04T15:45:00Z',
  source: 'cboe',
  delayed_minutes: 15,
  is_eod: false,
  spot: 7711.4,
};

// The real QQQ fixture: profile never crosses zero across the ±10% grid, so `flip_point`
// is legitimately `null` — not a placeholder, not a bug.
const QQQ_LEVELS: KeyLevelsData = {
  net_gex: 13_523_110_420,
  call_wall: 620,
  put_wall: 585,
  max_abs_strike: 620,
  flip_point: null,
  spot: 600.18,
  computed_at: '2026-09-04T15:45:00Z',
};
const QQQ_SNAPSHOT: SnapshotInfo = {
  id: 1003,
  underlying: 'QQQ',
  captured_at: '2026-09-04T15:45:00Z',
  source: 'cboe',
  delayed_minutes: 15,
  is_eod: false,
  spot: 600.18,
};

function renderKeyLevels(levels: KeyLevelsData, snapshot: SnapshotInfo) {
  return render(
    <ThemeProvider>
      <KeyLevels levels={levels} snapshot={snapshot} />
    </ThemeProvider>,
  );
}

describe('KeyLevels', () => {
  it('shows every level with distances signed correctly relative to spot', () => {
    renderKeyLevels(SPX_LEVELS, SPX_SNAPSHOT);

    // Call wall (7850) is above spot (7711.4) -> positive distance, both units.
    const callWallRow = screen.getByText('Call wall').closest('tr')!;
    expect(callWallRow.textContent).toContain('+138.60');
    expect(callWallRow.textContent).toContain('+1.80%');

    // Put wall (7550) is below spot -> negative distance, both units.
    const putWallRow = screen.getByText('Put wall').closest('tr')!;
    expect(putWallRow.textContent).toContain('-161.40');
    expect(putWallRow.textContent).toContain('-2.09%');

    // Flip point (7649.71) is also below spot here -> negative distance.
    const flipRow = screen.getByText('Gamma flip').closest('tr')!;
    expect(flipRow.textContent).toContain('-61.69');
    expect(flipRow.textContent).toContain('-0.80%');
  });

  it('renders net GEX signed (never absolute-valued) with the positive-gamma label', () => {
    renderKeyLevels(SPX_LEVELS, SPX_SNAPSHOT);
    expect(screen.getByText('$48.9B')).toBeInTheDocument();
    expect(screen.getByText(/Dealers net long gamma/)).toBeInTheDocument();
  });

  it('renders a dash for a null flip point and an explanation, without crashing', () => {
    renderKeyLevels(QQQ_LEVELS, QQQ_SNAPSHOT);
    const flipRow = screen.getByText('Gamma flip').closest('tr')!;
    // formatStrike/formatDistance/formatDistancePct all null-tolerate to an em dash.
    expect(flipRow.textContent).toContain('—');
    expect(flipRow.textContent).not.toMatch(/NaN/);
    expect(screen.getByText(/No sign change within the profile grid/)).toBeInTheDocument();
  });

  it('shows the snapshot time and the delay badge, matching the TopBar wording', () => {
    renderKeyLevels(SPX_LEVELS, SPX_SNAPSHOT);
    expect(screen.getByText(/As of .* ET/)).toBeInTheDocument();
    expect(screen.getByText(/Delayed 15m/)).toBeInTheDocument();
  });

  it('shows "Real-time" instead of a delay figure when delayed_minutes is 0', () => {
    renderKeyLevels(SPX_LEVELS, { ...SPX_SNAPSHOT, delayed_minutes: 0 });
    expect(screen.getByText(/Real-time/)).toBeInTheDocument();
  });
});
