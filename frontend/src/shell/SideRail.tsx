/**
 * T63 — the grouped nav rail (`plans/ui-ux-refresh/README.md`'s "Target information
 * architecture" diagram). Replaces `AppShell.tsx`'s old flat `NavBar` (ten peer links, no
 * grouping) with three labelled groups (Today/Analyze/Review) plus Settings ungrouped at the
 * bottom, per T62's approved nav mapping (`plans/ui-ux-refresh/01-ux-baseline.md`).
 *
 * Route paths are unchanged (`navConfig.ts`'s `NAV_GROUPS`/`SETTINGS_NAV_ITEM`, copied
 * verbatim from the old `NavBar`) — only labels and grouping moved. Every link still carries
 * the current search string forward exactly as the old `NavBar` did (`to={{ pathname,
 * search: location.search }}`), so a filter/sort/snapshot query survives a nav click.
 *
 * Two independent responsive behaviors:
 *  - Desktop: a `collapsed` toggle shrinks the rail's width. Per the plan's accessibility
 *    rule ("icon is never the sole signal"), every nav *item* keeps both its icon and its
 *    label even collapsed — collapsing only stacks the label under the icon in a smaller
 *    size and hides the TODAY/ANALYZE/REVIEW section dividers to screen-reader-only text
 *    (see the `.side-rail--collapsed` rules in index.css).
 *  - Narrow width (<=900px, matching the breakpoint the rest of the app already uses for
 *    scan/rotation layouts): the persistent rail is hidden by CSS and a hamburger button
 *    opens the same nav content in an overlay drawer instead. The drawer's Escape/backdrop-
 *    click/focus-trap/focus-return behavior is `useOverlayDismiss` (shared with
 *    `CommandPalette`).
 */
import { useCallback, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { NAV_GROUPS, SETTINGS_NAV_ITEM, isNavPair, type NavGroup, type NavItem } from './navConfig';
import { NAV_ICONS, IconChevron, IconClose, IconMenu } from './icons';
import { RailBrand } from './ModuleIdentity';
import { useOverlayDismiss } from './useOverlayDismiss';

function RailLink({ item }: { item: NavItem }) {
  const location = useLocation();
  const Icon = NAV_ICONS[item.key];
  return (
    <NavLink
      to={{ pathname: item.to, search: location.search }}
      end={item.end}
      className={({ isActive }) => `side-rail__link${isActive ? ' side-rail__link--active' : ''}`}
    >
      <Icon className="side-rail__icon" />
      <span className="side-rail__label">{item.label}</span>
    </NavLink>
  );
}

function RailGroup({ group }: { group: NavGroup }) {
  return (
    <div className="side-rail__group">
      <div className="side-rail__group-title">{group.title}</div>
      {group.entries.map((entry, index) =>
        isNavPair(entry) ? (
          <div className="side-rail__pair" key={`${group.title}-pair-${index}`}>
            <RailLink item={entry.pair[0]} />
            <RailLink item={entry.pair[1]} />
          </div>
        ) : (
          <RailLink key={entry.key} item={entry} />
        ),
      )}
    </div>
  );
}

/** The nav content itself, rendered both in the persistent desktop `<aside>` and inside the
 * narrow-width drawer — one definition, so the two can never show a different route set. */
function NavContent() {
  return (
    <nav className="side-rail__nav" aria-label="Primary">
      {NAV_GROUPS.map((group) => (
        <RailGroup group={group} key={group.title} />
      ))}
      <div className="side-rail__group side-rail__group--bottom">
        <RailLink item={SETTINGS_NAV_ITEM} />
      </div>
    </nav>
  );
}

export function SideRail() {
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const menuToggleRef = useRef<HTMLButtonElement>(null);

  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  const drawerRef = useOverlayDismiss<HTMLDivElement>(drawerOpen, closeDrawer);

  return (
    <>
      <button
        type="button"
        ref={menuToggleRef}
        className="side-rail__menu-toggle"
        aria-label={drawerOpen ? 'Close navigation menu' : 'Open navigation menu'}
        aria-expanded={drawerOpen}
        aria-controls="side-rail-drawer"
        onClick={() => setDrawerOpen((v) => !v)}
      >
        {drawerOpen ? <IconClose /> : <IconMenu />}
        <span>Menu</span>
      </button>

      <aside className={`side-rail${collapsed ? ' side-rail--collapsed' : ''}`} aria-hidden={drawerOpen}>
        <RailBrand moduleKey="gex" />
        <button
          type="button"
          className="side-rail__collapse-toggle"
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          aria-pressed={collapsed}
          onClick={() => setCollapsed((v) => !v)}
        >
          <IconChevron direction={collapsed ? 'right' : 'left'} />
        </button>
        <NavContent />
      </aside>

      {drawerOpen && (
        <div className="side-rail__drawer-backdrop">
          <div id="side-rail-drawer" className="side-rail__drawer" role="dialog" aria-modal="true" aria-label="Navigation menu" ref={drawerRef}>
            <button type="button" className="side-rail__drawer-close" onClick={closeDrawer} aria-label="Close navigation menu">
              <IconClose />
            </button>
            <NavContent />
          </div>
        </div>
      )}
    </>
  );
}
