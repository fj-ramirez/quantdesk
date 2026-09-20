/**
 * T51 -- `/rotation`'s breadth block (07-ui.md: "RSP/SPY ratio with 20-day change, count of
 * sectors above 20-day and 50-day averages as `7 / 11`, and the label 'sector-level breadth'
 * with the tooltip explaining why constituent breadth is not shown").
 *
 * **Must not overstate itself.** Real, constituent-level breadth (percent of the S&P 500
 * above its own 50-day average) needs 500 symbols of daily bars and is out of budget (plan
 * 04's "Design decisions"). This block only ever shows the coarser, honestly-labelled
 * sector-level reading the backend actually computes -- eleven sector ETFs, not five hundred
 * stocks -- and says so in the tooltip rather than leaving the distinction implicit.
 *
 * **The denominator is never hardcoded.** `above_20d`/`above_50d` are rendered over
 * `evaluated_20d`/`evaluated_50d` from the response, not a literal `11` -- if a sector ETF is
 * ever excluded for gappy bars (the same exclusion `ExcludedSymbol` already models for the
 * breakout/trend scans), the fraction's denominator must shrink to match, not silently keep
 * counting against a stale eleven.
 */
import { formatRatio, formatSignedPct } from '../../../../lib/format';
import type { RotationBreadth } from '../../api/types';

const BREADTH_TOOLTIP =
  'Constituent-level breadth (the percent of S&P 500 stocks above their own 50-day average) ' +
  'would need bars for all 500 constituents, which this app does not track. This reading is ' +
  'coarser: the equal-weight/cap-weight (RSP/SPY) ratio and its 20-day change, plus how many ' +
  'of the 11 tracked sector ETFs sit above their own 20- and 50-day averages.';

export interface BreadthProps {
  breadth: RotationBreadth;
}

export function Breadth({ breadth }: BreadthProps) {
  return (
    <section className="rotation-breadth" aria-label={breadth.label}>
      <h3 className="rotation-breadth__title">
        {breadth.label}{' '}
        <span
          className="rotation-breadth__info"
          role="img"
          aria-label="About this breadth reading"
          title={BREADTH_TOOLTIP}
        >
          ⓘ
        </span>
      </h3>
      <dl className="rotation-breadth__list">
        <dt>RSP / SPY ratio</dt>
        <dd>{formatRatio(breadth.equal_weight_ratio)}</dd>
        <dt>20-day change</dt>
        <dd>{formatSignedPct(breadth.equal_weight_ratio_change_20d)}</dd>
        <dt>Above 20-day avg</dt>
        <dd>
          {breadth.above_20d} / {breadth.evaluated_20d}
        </dd>
        <dt>Above 50-day avg</dt>
        <dd>
          {breadth.above_50d} / {breadth.evaluated_50d}
        </dd>
      </dl>
    </section>
  );
}
