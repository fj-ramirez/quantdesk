/**
 * T60 -- the detail panel's content for one selected opportunity: the three levels with the
 * engine's own sentence for each, the thesis, the invalidation conditions, the structure
 * hint, any warnings, and the score breakdown. Everything renders verbatim from the API --
 * same rule `VerdictCell` follows for `reasons`: no rewording, re-casing or truncation,
 * because every sentence here is a reading of a number that sits beside it and the two must
 * not drift.
 *
 * T64: this component used to render its own `<section>`/heading/Close button wrapper. It is
 * now mounted as `ui/DetailDrawer`'s children (`Decisions.tsx`), which owns that chrome (and
 * the focus-trap/Escape/backdrop/focus-return behavior neither this component nor
 * `DetailDrawer`'s predecessor ever had) -- this file renders only the content below the
 * drawer's own header.
 */
import type { RankedOpportunity } from '../../api/types';
import { formatAtr, formatPrice, formatRatio } from '../../lib/format';
import { StatusChip } from '../scan/StatusChip';

function magnitude(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return formatAtr(value).replace(/^\+/, '');
}

export interface OpportunityDetailProps {
  opportunity: RankedOpportunity;
}

export function OpportunityDetail({ opportunity }: OpportunityDetailProps) {
  const o = opportunity;
  return (
    <div className="decisions-detail">
      <div className="decisions-detail__chips">
        <StatusChip status={o.status} />
        <StatusChip status={o.setup} />
        <span className={`decisions-side decisions-side--${o.side.toLowerCase()}`}>{o.side}</span>
        <span className="decisions-grade">
          <strong>{o.grade}</strong> <span className="scan-cell--muted">{o.score}/100</span>
        </span>
      </div>

      {o.rejection_reason && (
        <p className="decisions-detail__rejection" role="note">
          Rejected: {o.rejection_reason}.
        </p>
      )}

      <dl className="decisions-levels">
        <dt>Entry</dt>
        <dd>
          <span className="decisions-levels__price">{formatPrice(o.entry)}</span>
          <span className="decisions-levels__label">{o.entry_label}</span>
        </dd>
        <dt>Stop</dt>
        <dd>
          <span className="decisions-levels__price">{formatPrice(o.stop)}</span>
          <span className="decisions-levels__label">
            {o.stop_label} · risk {magnitude(o.risk_atr)}
          </span>
        </dd>
        <dt>Target</dt>
        <dd>
          <span className="decisions-levels__price">{formatPrice(o.target)}</span>
          <span className="decisions-levels__label">
            {o.target_label} · reward {magnitude(o.reward_atr)} · R:R {formatRatio(o.rr)}
          </span>
        </dd>
        {o.target_2 != null && (
          <>
            <dt>Target 2</dt>
            <dd>
              <span className="decisions-levels__price">{formatPrice(o.target_2)}</span>
              <span className="decisions-levels__label">{o.target_2_label}</span>
            </dd>
          </>
        )}
      </dl>

      <section aria-label="Thesis">
        <h3 className="decisions-detail__h">Thesis</h3>
        <ul className="decisions-detail__list">
          {o.thesis.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </section>

      <section aria-label="Invalidation">
        <h3 className="decisions-detail__h">Invalidation</h3>
        <ul className="decisions-detail__list">
          {o.invalidation.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </section>

      <section aria-label="Structure">
        <h3 className="decisions-detail__h">Structure</h3>
        <p className="decisions-detail__structure">{o.structure}</p>
      </section>

      {o.warnings.length > 0 && (
        <section aria-label="Warnings">
          <h3 className="decisions-detail__h">Warnings</h3>
          <ul className="decisions-detail__list decisions-detail__list--warn">
            {o.warnings.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Score breakdown">
        <h3 className="decisions-detail__h">Score</h3>
        <table className="decisions-score">
          <tbody>
            {o.score_breakdown.map((c) => (
              <tr key={c.name}>
                <th scope="row">{c.name}</th>
                <td className="decisions-score__pts">
                  {c.points}/{c.max_points}
                </td>
                <td className="decisions-score__note">{c.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
