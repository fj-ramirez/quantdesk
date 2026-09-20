/**
 * T63 — one nav-route table shared by `SideRail` (the rendered rail/drawer) and
 * `CommandPalette` (Ctrl/Cmd+K route search), so the two can never drift: adding a route
 * here is the only place a future task needs to touch to teach both surfaces about it.
 *
 * Route paths are T62's inventory (`plans/ui-ux-refresh/01-ux-baseline.md`'s "Navigation
 * mapping" table) with T75's `/gex` module segment in front of each — only labels and
 * grouping changed from T62. "Opportunities" and
 * "GEX Explorer" are new display labels for the existing `/decisions` and `/dashboard`
 * routes; every `to` below is unchanged from `AppShell.tsx`'s old flat `NavBar`.
 */

export interface NavItem {
  key: string;
  label: string;
  to: string;
  /** Passed straight through to `NavLink`'s own `end` prop — only the module root (`/gex`,
   * T75) needs it, since every other route is not a prefix of any other route in this list. */
  end?: boolean;
}

export interface NavPair {
  pair: readonly [NavItem, NavItem];
}

export type NavEntry = NavItem | NavPair;

export interface NavGroup {
  title: string;
  entries: readonly NavEntry[];
}

export function isNavPair(entry: NavEntry): entry is NavPair {
  return 'pair' in entry;
}

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    title: 'Today',
    entries: [
      { key: 'overview', label: 'Overview', to: '/gex', end: true },
      { key: 'decisions', label: 'Opportunities', to: '/gex/decisions' },
    ],
  },
  {
    title: 'Analyze',
    entries: [
      { key: 'dashboard', label: 'GEX Explorer', to: '/gex/dashboard' },
      {
        pair: [
          { key: 'regime', label: 'Regime', to: '/gex/regime' },
          { key: 'scan', label: 'Scan', to: '/gex/scan' },
        ],
      },
      {
        pair: [
          { key: 'rotation', label: 'Rotation', to: '/gex/rotation' },
          { key: 'flows', label: 'Flows', to: '/gex/flows' },
        ],
      },
    ],
  },
  {
    title: 'Review',
    entries: [
      {
        pair: [
          { key: 'report', label: 'Report', to: '/gex/report' },
          { key: 'history', label: 'History', to: '/gex/history' },
        ],
      },
    ],
  },
] as const;

/** Rendered ungrouped, at the bottom of the rail — see `plans/ui-ux-refresh/README.md`'s
 * target IA diagram. */
export const SETTINGS_NAV_ITEM: NavItem = { key: 'settings', label: 'Settings', to: '/gex/settings' };

/** Flattens every nav item (including the two paired into one visual row, and Settings) into
 * one ordered list — the command palette's route-search list, and every ordering/grouping
 * test's ground truth. */
export function flattenNavItems(): NavItem[] {
  const items: NavItem[] = [];
  for (const group of NAV_GROUPS) {
    for (const entry of group.entries) {
      if (isNavPair(entry)) items.push(...entry.pair);
      else items.push(entry);
    }
  }
  items.push(SETTINGS_NAV_ITEM);
  return items;
}
