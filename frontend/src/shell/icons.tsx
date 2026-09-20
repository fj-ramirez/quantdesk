/**
 * T63 — a small hand-rolled icon set for the workbench shell, so nav items and toggle
 * buttons carry an icon *alongside* their text label (the plan's "icon is never the sole
 * signal" rule — see `SideRail.tsx`) without adding an icon-library dependency. Every icon
 * is a plain inline SVG, stroke-only, `currentColor`, 24x24 viewBox — it inherits the
 * caller's text color, so it already follows both themes with zero icon-specific tokens.
 */
import type { ReactElement, ReactNode, SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={18}
      height={18}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

/** Overview — an at-a-glance 2x2 grid. */
export function IconOverview(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1" />
    </Icon>
  );
}

/** Opportunities — a target. */
export function IconOpportunities(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <circle cx="12" cy="12" r="0.75" fill="currentColor" />
    </Icon>
  );
}

/** GEX Explorer — a bar chart. */
export function IconExplorer(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="4" y1="20" x2="20" y2="20" />
      <rect x="6" y="12" width="3.2" height="8" />
      <rect x="10.4" y="7" width="3.2" height="13" />
      <rect x="14.8" y="10" width="3.2" height="10" />
    </Icon>
  );
}

/** Regime — a pulse/activity line. */
export function IconRegime(props: IconProps) {
  return (
    <Icon {...props}>
      <polyline points="3.5,13 8,13 10,8 14,18 16,13 20.5,13" />
    </Icon>
  );
}

/** Scan — a magnifying glass. */
export function IconScan(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <line x1="15.3" y1="15.3" x2="20.5" y2="20.5" />
    </Icon>
  );
}

/** Rotation — a cycle/refresh arrow. */
export function IconRotation(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4.5 12a7.5 7.5 0 0 1 12.6-5.5" />
      <polyline points="17.5,3 17.5,7 13.5,7" />
      <path d="M19.5 12a7.5 7.5 0 0 1 -12.6 5.5" />
      <polyline points="6.5,21 6.5,17 10.5,17" />
    </Icon>
  );
}

/** Flows — a trending line with an arrowhead. */
export function IconFlows(props: IconProps) {
  return (
    <Icon {...props}>
      <polyline points="3.5,17 9,10.5 13,14 20.5,5.5" />
      <polyline points="14.5,5 20.5,5 20.5,11" />
    </Icon>
  );
}

/** Report — a document. */
export function IconReport(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6.5 3.5h8l4 4v13h-12z" />
      <path d="M14.5 3.5v4h4" />
      <line x1="8.5" y1="12" x2="15.5" y2="12" />
      <line x1="8.5" y1="15.5" x2="15.5" y2="15.5" />
    </Icon>
  );
}

/** History — a clock. */
export function IconHistory(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12.5" r="8" />
      <polyline points="12,8 12,12.5 15.5,14.5" />
      <path d="M9 3.2h6" />
    </Icon>
  );
}

/** Settings — sliders. */
export function IconSettings(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="4" y1="6" x2="20" y2="6" />
      <circle cx="9" cy="6" r="2" fill="none" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <circle cx="16" cy="12" r="2" fill="none" />
      <line x1="4" y1="18" x2="20" y2="18" />
      <circle cx="11" cy="18" r="2" fill="none" />
    </Icon>
  );
}

/** Hamburger menu — the narrow-width drawer toggle. */
export function IconMenu(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="4" y1="6.5" x2="20" y2="6.5" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <line x1="4" y1="17.5" x2="20" y2="17.5" />
    </Icon>
  );
}

/** Close (X) — used by both the drawer's close button and the palette-adjacent affordances. */
export function IconClose(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="5.5" y1="5.5" x2="18.5" y2="18.5" />
      <line x1="18.5" y1="5.5" x2="5.5" y2="18.5" />
    </Icon>
  );
}

/** Search — the command palette's own leading glyph (a plain, undecorated magnifying glass;
 * kept distinct from `IconScan` only in name, since the two nav/command surfaces should not
 * import each other). */
export function IconSearch(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <line x1="15.3" y1="15.3" x2="20.5" y2="20.5" />
    </Icon>
  );
}

/** Chevron — the desktop rail's collapse/expand toggle. Points left when the rail is
 * expanded (collapsing it), right when collapsed (expanding it). */
export function IconChevron({ direction, ...props }: IconProps & { direction: 'left' | 'right' }) {
  const points = direction === 'left' ? '14,5 8,12 14,19' : '10,5 16,12 10,19';
  return (
    <Icon {...props}>
      <polyline points={points} />
    </Icon>
  );
}

/** Maps every `NavItem.key` (see `navConfig.ts`) to its icon component. A `key` with no
 * entry here would be a programming error (a nav item with no icon violates the plan's own
 * rule), so `SideRail` indexes into this directly rather than falling back silently. */
export const NAV_ICONS: Record<string, (props: IconProps) => ReactElement> = {
  overview: IconOverview,
  decisions: IconOpportunities,
  dashboard: IconExplorer,
  regime: IconRegime,
  scan: IconScan,
  rotation: IconRotation,
  flows: IconFlows,
  report: IconReport,
  history: IconHistory,
  settings: IconSettings,
};

/* -----------------------------------------------------------------------------------------
 * The launcher's own marks (`shell/Launcher.tsx`). Same rules as everything above:
 * inline SVG, stroke-only, `currentColor`, 24x24 viewBox, no icon-library dependency. The
 * three product marks (`IconGexMark`/`IconEdgeLabMark`/`IconXactxMark`) are the only icons in
 * this file drawn to be *recognised* rather than read — each is the visual identity of one
 * module on the launcher card, so they are deliberately chunkier than the nav glyphs.
 * --------------------------------------------------------------------------------------- */

/** Home — a roof over a door. */
export function IconHome(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z" />
    </Icon>
  );
}

/** Markets — a trend line over an axis. */
export function IconMarkets(props: IconProps) {
  return (
    <Icon {...props}>
      <polyline points="3.5,16.5 9,11 13,14.5 20.5,6.5" />
      <polyline points="15.5,6.5 20.5,6.5 20.5,11.5" />
    </Icon>
  );
}

/** Analysis — a document with lines of text. */
export function IconAnalysis(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3.5h7.5L18 8v12.5H6z" />
      <polyline points="13.5,3.5 13.5,8 18,8" />
      <line x1="9" y1="12.5" x2="15" y2="12.5" />
      <line x1="9" y1="16" x2="15" y2="16" />
    </Icon>
  );
}

/** Backtesting — a play triangle: run the thing over history. */
export function IconBacktest(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M8 5.5 19 12 8 18.5z" />
    </Icon>
  );
}

/** Watchlist — a star. */
export function IconWatchlist(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m12 4 2.6 5.3 5.9.85-4.25 4.15 1 5.85L12 17.4l-5.25 2.75 1-5.85L3.5 10.15l5.9-.85z" />
    </Icon>
  );
}

/** The theme toggle's light-mode mark. */
export function IconSun(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="4" />
      <line x1="12" y1="2.5" x2="12" y2="5" />
      <line x1="12" y1="19" x2="12" y2="21.5" />
      <line x1="2.5" y1="12" x2="5" y2="12" />
      <line x1="19" y1="12" x2="21.5" y2="12" />
      <line x1="5.4" y1="5.4" x2="7.2" y2="7.2" />
      <line x1="16.8" y1="16.8" x2="18.6" y2="18.6" />
      <line x1="5.4" y1="18.6" x2="7.2" y2="16.8" />
      <line x1="16.8" y1="7.2" x2="18.6" y2="5.4" />
    </Icon>
  );
}

/** The theme toggle's dark-mode mark. */
export function IconMoon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z" />
    </Icon>
  );
}

/** The launcher card's "open this module" affordance. */
export function IconArrowRight(props: IconProps) {
  return (
    <Icon {...props}>
      <line x1="4.5" y1="12" x2="19" y2="12" />
      <polyline points="13,6 19,12 13,18" />
    </Icon>
  );
}

/** GEX — the gamma profile as a column chart. */
export function IconGexMark(props: IconProps) {
  return (
    <Icon strokeWidth={2} {...props}>
      <line x1="5" y1="20" x2="5" y2="12" />
      <line x1="10" y1="20" x2="10" y2="5" />
      <line x1="15" y1="20" x2="15" y2="9" />
      <line x1="20" y1="20" x2="20" y2="15" />
    </Icon>
  );
}

/** EdgeLab — a document under a lens: the search, not the result. */
export function IconEdgeLabMark(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3.5h7.5L18 8v12.5H6z" />
      <polyline points="13.5,3.5 13.5,8 18,8" />
      <circle cx="11.2" cy="13.2" r="2.8" />
      <line x1="13.4" y1="15.4" x2="15.6" y2="17.6" />
    </Icon>
  );
}

/** xactx — a transmission graph: nodes and the edges between them. */
export function IconXactxMark(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="4.8" r="2.1" />
      <circle cx="5" cy="17" r="2.1" />
      <circle cx="19" cy="17" r="2.1" />
      <line x1="10.9" y1="6.7" x2="6.1" y2="15.1" />
      <line x1="13.1" y1="6.7" x2="17.9" y2="15.1" />
      <line x1="7.1" y1="17" x2="16.9" y2="17" />
    </Icon>
  );
}
