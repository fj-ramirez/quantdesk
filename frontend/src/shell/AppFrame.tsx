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
    // `app-frame--gex` is what sets `--module-accent` (shell/modules.ts) for everything
    // below it — the active nav pill, a metric card's edge, the identity mark.
    <div className="app-frame app-frame--gex">
      <SideRail />
      <div className="app-frame__main">
        <ContextBar />
        {/* No `ModuleHeader` here, unlike the other two frames: `ContextBar` carries GEX's
            identity inside the control bar instead, because this module's pages are dense and
            a second full-width identity row is 56px of chrome for one word. */}
        <main className="app-frame__content">
          <Outlet />
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}
