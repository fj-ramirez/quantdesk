import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider } from '../../../theme/ThemeContext';
import { KeyLevels } from './KeyLevels';
import type { KeyLevels as KeyLevelsData, SnapshotInfo } from '../api/types';

// Numbers lifted from the real, enriched SPX fixture (src/mocks/fixtures/gex-spx.json) so
// the signed distances below are checked against genuine data, not fabricated round
// numbers. top_positive/top_negative aren't exercised by KeyLevels (it only reads the wall
// and flip fields), but they're still real values from that fixture, not placeholders.
const SPX_LEVELS: KeyLevelsData = {
  net_gex: 48_912_826_098,
  call_gex: 320_156_679_906,
  put_gex: -271_243_853_807,
  abs_gex: 591_400_533_713,
  call_wall: 7850,
  call_wall_gex: 31_323_957_469,
  put_wall: 7550,
  put_wall_gex: -24_838_546_097,
  max_abs_strike: 7850,
  max_abs_gex: 40_675_892_063,
  max_net_strike: 7850,
  min_net_strike: 7550,
  max_call_gex_strike: 7850,
  max_call_gex: 35_999_924_766,
  max_put_gex_strike: 7550,
  max_put_gex: -30_397_153_606,
  flip_point: 7649.71,
  spot: 7711.4,
  computed_at: '2026-09-04T15:45:00Z',
  top_positive: [
    { strike: 7850, call_gex: 35_999_924_766, put_gex: -4_675_967_297, net_gex: 31_323_957_469, abs_gex: 40_675_892_063, contracts: 3, open_interest: 1848 },
  ],
  top_negative: [
    { strike: 7550, call_gex: 5_558_607_509, put_gex: -30_397_153_606, net_gex: -24_838_546_097, abs_gex: 35_955_761_115, contracts: 2, open_interest: 1618 },
  ],
};
const SPX_SNAPSHOT: SnapshotInfo = {
  id: 1001,
  underlying: 'SPX',
  captured_at: '2026-09-04T15:45:00Z',
  effective_at: '2026-09-04T15:45:00Z',
  source: 'cboe',
  delayed_minutes: 15,
  is_eod: false,
  spot: 7711.4,
  contract_count: 28650,
};

// The real QQQ fixture: profile never crosses zero across the ±10% grid, so `flip_point`
// is legitimately `null` — not a placeholder, not a bug.
const QQQ_LEVELS: KeyLevelsData = {
  net_gex: 13_523_110_420,
  call_gex: 41_468_843_738,
  put_gex: -27_945_733_319,
  abs_gex: 69_414_577_057,
  call_wall: 620,
  call_wall_gex: 4_065_503_527,
  put_wall: 585,
  put_wall_gex: -2_300_359_966,
  max_abs_strike: 620,
  max_abs_gex: 5_045_650_715,
  max_net_strike: 620,
  min_net_strike: 585,
  max_call_gex_strike: 620,
  max_call_gex: 4_555_577_121,
  max_put_gex_strike: 585,
  max_put_gex: -3_075_963_716,
  flip_point: null,
  spot: 600.18,
  computed_at: '2026-09-04T15:45:00Z',
  top_positive: [
    { strike: 620, call_gex: 4_555_577_121, put_gex: -490_073_594, net_gex: 4_065_503_527, abs_gex: 5_045_650_715, contracts: 3, open_interest: 795 },
  ],
  top_negative: [
    { strike: 585, call_gex: 775_603_749, put_gex: -3_075_963_716, net_gex: -2_300_359_966, abs_gex: 3_851_567_465, contracts: 2, open_interest: 600 },
  ],
};
const QQQ_SNAPSHOT: SnapshotInfo = {
  id: 1003,
  underlying: 'QQQ',
  captured_at: '2026-09-04T15:45:00Z',
  effective_at: '2026-09-04T15:45:00Z',
  source: 'cboe',
  delayed_minutes: 15,
  is_eod: false,
  spot: 600.18,
  contract_count: 11006,
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

  it('T34: reads as the day\'s close, not a rolling delay, once effective_at differs from captured_at', () => {
    // A snapshot captured well after the close (e.g. an evening catch-up, T29): the backend's
    // `effective_at` is clamped to the prior close, not the vendor's still-advancing
    // `captured_at`. The footer must reflect that clamp, not fall back to "Delayed 15m".
    renderKeyLevels(SPX_LEVELS, {
      ...SPX_SNAPSHOT,
      captured_at: '2026-09-04T21:55:00Z', // 17:55 ET -- the supervisor's own repro instant
      effective_at: '2026-09-04T20:15:00Z', // 16:15 ET: close (16:00) + 15m delay, clamped
    });
    expect(screen.getByText(/At Friday's close \(4:15 PM ET\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Delayed 15m/)).not.toBeInTheDocument();
  });
});
