/**
 * T63 — replaces `AppShell.tsx`. The old shell was a centered, fixed-width, bordered
 * "marketing template" card (`#root { width: 1126px; ...; text-align: center; border-inline:
 * 1px solid var(--border) }`) with a flat ten-link `<nav>` above the page body. `AppFrame` is
 * a full-height workbench surface instead: a stable left rail (`SideRail`) and a sticky
 * route-aware bar (`ContextBar`, T63's rename of `TopBar`) around whatever page the router
 * mounts. `CommandPalette` is mounted once here so Ctrl/Cmd+K works from every route without
 * every page having to render it itself.
 *
 * This is a pure layout swap: every route under it is unchanged (see `App.tsx`), and no page
 * file was touched to make this land — `plans/ui-ux-refresh/README.md`'s T63 scope is the
 * shell only, not page-content migration (that's T64-T67).
 */
import { Outlet } from 'react-router-dom';
import { SideRail } from './SideRail';
import { ContextBar } from '../modules/gex/components/layout/ContextBar';
import { CommandPalette } from './CommandPalette';

export function AppFrame() {
  return (
    <div className="app-frame">
      <SideRail />
      <div className="app-frame__main">
        <ContextBar />
        <main className="app-frame__content">
          <Outlet />
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}
