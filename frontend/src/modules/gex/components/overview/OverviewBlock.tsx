/**
 * T56 -- the one header+link wrapper every panel on `/overview` uses. 07-ui.md: "Every block
 * header links to its full page." A `<section>` with a heading and a `Link`, styled from
 * `index.css`'s `.overview-block__*` tokens-only rules -- no markup this component owns is
 * reimplemented per panel.
 *
 * `aria-label` (not `aria-labelledby` + a generated id) names the region directly from
 * `heading`, the same pattern `EmptyState` already uses -- lets a test target a panel with
 * `getByRole('region', { name: heading })` without this component minting ids.
 *
 * T65: renders on `ui/Surface` (`as="section"`, so the region role/name and every existing
 * test query against it are unaffected) rather than a bare `<section>` -- the plan's "calm
 * visual language" card treatment T64 already gave `/decisions`, applied here so every
 * Overview panel reads as a Surface-based card instead of the un-styled flex column it was
 * before. Nothing inside a block (heading text, link target, children) changed.
 */
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Surface } from '../../../../components/ui/Surface';

export interface OverviewBlockProps {
  heading: string;
  to: string;
  linkLabel?: string;
  children: ReactNode;
  /** T68: every block nested under one of the page's own `h2` section headers (`Where
   * continuation is`, `Open now`) is correctly an `h3` -- one level down. `Tape`, the one
   * block that sits directly under the page's `h1` with no wrapping section heading, must be
   * `h2` itself or the heading order skips a level (axe-core: "heading-order", moderate).
   * Defaults to the `h3` every other caller still wants. */
  headingLevel?: 'h2' | 'h3';
}

export function OverviewBlock({ heading, to, linkLabel = 'Open full page →', children, headingLevel = 'h3' }: OverviewBlockProps) {
  const Heading = headingLevel;
  return (
    <Surface as="section" aria-label={heading} className="overview-block">
      <div className="overview-block__head">
        <Heading className="overview-block__title">{heading}</Heading>
        <Link className="overview-block__link" to={to}>
          {linkLabel}
        </Link>
      </div>
      {children}
    </Surface>
  );
}
