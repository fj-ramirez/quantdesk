/**
 * The noise ceiling, in one sentence a human can act on (T78).
 *
 * **This is the most important component in the module.** EdgeLab's leaderboard is the output
 * of a search over 134,377 combinations, and searching that hard guarantees a good-looking
 * result whether or not any edge exists: the expected best OOS Sharpe from pure luck across N
 * trials is ~sqrt(2 ln N)/sqrt(years), which at this N is about 5.6. A row at 3.0 is therefore
 * not "a decent edge" — it is *below the noise floor*, and presenting it without that context
 * is worse than presenting nothing, because a table of ranked numbers reads as authoritative.
 *
 * So the number is rendered as prose, not as a statistic in a corner. "With 134,377 trials,
 * pure noise would produce a best OOS Sharpe around 5.60" is a sentence; a `MetricCard` reading
 * `5.60 / noise ceiling` is a decoration that gets skimmed past.
 *
 * The value always comes from the API (`plans/quantdesk/02-research-module.md`: the ceiling is
 * "returned alongside the rows, never as a client-side guess"), so the page and the static HTML
 * report cannot disagree.
 */
import { Surface } from '../../../components/ui/Surface';

export interface NoiseCeilingBannerProps {
  noiseCeiling: number;
  totalTrials: number;
  medianOosYears: number;
  /** How many rows on this page actually clear their own ceiling. */
  aboveCount: number;
  rowCount: number;
}

export function NoiseCeilingBanner({
  noiseCeiling,
  totalTrials,
  medianOosYears,
  aboveCount,
  rowCount,
}: NoiseCeilingBannerProps) {
  return (
    <Surface className="noise-ceiling" level="subtle" bordered>
      <p className="noise-ceiling__headline">
        <strong>Noise ceiling: {noiseCeiling.toFixed(2)}</strong> — with{' '}
        {totalTrials.toLocaleString()} trials searched, pure noise alone would be expected to
        produce a best out-of-sample Sharpe around {noiseCeiling.toFixed(2)} (at the median OOS
        span of {medianOosYears.toFixed(1)} years). Each row below is judged against a ceiling
        for its <em>own</em> span.
      </p>
      <p className="noise-ceiling__verdict">
        {rowCount === 0
          ? 'No rows match these filters.'
          : aboveCount === 0
            ? `None of these ${rowCount} rows clears its own ceiling. On this evidence, nothing here is distinguishable from luck.`
            : `${aboveCount} of ${rowCount} rows clear their own ceiling.`}
      </p>
    </Surface>
  );
}

/**
 * The two standing caveats, on the page rather than only in the README.
 *
 * The brief is explicit that both must reach the UI. They are not footnotes: each one changes
 * how a specific row should be read, and a reader who never opens the repo would otherwise
 * never learn either.
 */
export function ResearchCaveats({ hasFutures }: { hasFutures: boolean }) {
  return (
    <ul className="research-caveats">
      <li>
        <strong>The out-of-sample split has been reused.</strong> Every cycle scores its
        candidates on the same held-out segment, so that segment has been looked at thousands of
        times and is no longer truly unseen. The noise ceiling is what accounts for this; the
        paper watchlist, where performance is measured only <em>after</em> promotion, is the only
        genuinely out-of-sample evidence here.
      </li>
      {hasFutures && (
        <li>
          <strong>Futures rows carry roll gaps.</strong> The <code>=F</code> tickers are
          front-month continuous contracts, so each roll injects a price jump that is not a
          tradeable return. Treat futures Sharpes as noisier than they look.
        </li>
      )}
    </ul>
  );
}
