/**
 * T49 -- the regime board's Verdict cell: a `StatusChip` naming the row's `verdictGroup`
 * (`continuation`/`mixed`/`fade`/`noise-dominated`/`stale` -- `regimeRows.ts`'s
 * `verdictGroupOf`) behind a `<details>` disclosure that lists `reasons` verbatim, per
 * 07-ui.md's "Verdict cell is a disclosure: expanding it lists reasons verbatim from the API."
 * Every row carries at least one reason string, including a genuine verdict -- the disclosure
 * is universal, not a special case for the null-verdict rows.
 *
 * `reasons` render exactly as the API sent them: no reformatting, re-casing or truncation, per
 * the task's own instruction and the same discipline `RotationView`'s `note` already follows
 * for its own verbatim API text.
 */
import { StatusChip } from '../scan/StatusChip';
import type { RegimeRow } from './regimeRows';

export function VerdictCell({ row }: { row: RegimeRow }) {
  return (
    <details className="regime-verdict">
      <summary className="regime-verdict__summary">
        <StatusChip status={row.verdictGroup} />
      </summary>
      <ul className="regime-verdict__reasons">
        {row.reasons.map((reason, i) => (
          <li key={i}>{reason}</li>
        ))}
      </ul>
    </details>
  );
}
