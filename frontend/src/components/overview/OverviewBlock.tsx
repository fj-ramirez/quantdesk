/**
 * T56 -- the one header+link wrapper every panel on `/overview` uses. 07-ui.md: "Every block
 * header links to its full page." A plain `<section>` with a heading and a `Link`, styled from
 * `index.css`'s `.overview-block__*` tokens-only rules -- no markup this component owns is
 * reimplemented per panel.
 *
 * `aria-label` (not `aria-labelledby` + a generated id) names the region directly from
 * `heading`, the same pattern `EmptyState` already uses -- lets a test target a panel with
 * `getByRole('region', { name: heading })` without this component minting ids.
 */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

export interface OverviewBlockProps {
  heading: string;
  to: string;
  linkLabel?: string;
  children: ReactNode;
}

export function OverviewBlock({ heading, to, linkLabel = 'Open full page →', children }: OverviewBlockProps) {
  return (
    <section aria-label={heading} className="overview-block">
      <div className="overview-block__head">
        <h3 className="overview-block__title">{heading}</h3>
        <Link className="overview-block__link" to={to}>
          {linkLabel}
        </Link>
      </div>
      {children}
    </section>
  );
}
