/**
 * T64 — PageHeader: title, one-line "what this answers" reading cue, a freshness/caveat
 * slot, and an actions slot, per the plan's "Standardize a page header" spec. Generalizes
 * the ad hoc heading/paragraph pairs pages wrote individually (e.g. `Decisions.tsx`'s
 * `generated_from` disclaimer paragraph sitting loose above its toolbar).
 */
import type { ReactNode } from 'react';

export interface PageHeaderProps {
  title: string;
  /** One sentence: what this page answers. Optional — a stub page may have nothing more to
   * say than its title. */
  description?: ReactNode;
  /** Freshness badge, source-timing sentence, or a data caveat — rendered under the
   * description, before actions. */
  caveat?: ReactNode;
  /** Buttons/links for this page's most consequential control(s). */
  actions?: ReactNode;
}

export function PageHeader({ title, description, caveat, actions }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-header__main">
        <h1 className="page-header__title">{title}</h1>
        {description != null && <p className="page-header__description">{description}</p>}
        {caveat != null && <div className="page-header__caveat">{caveat}</div>}
      </div>
      {actions != null && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}
