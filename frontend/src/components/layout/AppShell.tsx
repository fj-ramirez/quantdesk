import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { TopBar } from './TopBar';

/** Route nav. `to` carries the *current* search string forward explicitly — React Router
 * does not do this by default (an absolute `to="/history"` drops query params), and
 * dropping them here would silently break the "URL fully drives the view" contract the
 * moment someone clicks between Dashboard/History/Settings. */
// T36: with no styling at all these three links rendered as one unbroken run of text
// ("DashboardHistorySettings") -- `navlink` below gives each its own padded, pill-shaped hit
// area (see index.css), which reads as separated tabs without needing a literal separator
// character between them.
function NavBar() {
  const location = useLocation();
  const linkClassName = ({ isActive }: { isActive: boolean }) => (isActive ? 'navlink navlink--active' : 'navlink');
  return (
    <nav className="navbar">
      {/* Overview is the landing page as of 2026-09-10 (the user's call on 07-ui.md's open
          question), so it leads the nav at `/` rather than trailing it. */}
      <NavLink to={{ pathname: '/', search: location.search }} end className={linkClassName}>
        Overview
      </NavLink>
      <NavLink to={{ pathname: '/dashboard', search: location.search }} className={linkClassName}>
        Dashboard
      </NavLink>
      <NavLink to={{ pathname: '/report', search: location.search }} className={linkClassName}>
        Report
      </NavLink>
      <NavLink to={{ pathname: '/history', search: location.search }} className={linkClassName}>
        History
      </NavLink>
      {/* T55: the scan family (07-ui.md). T44/T49/T51/T53/T56 have since replaced every one of
          these with a real page -- the nav's shape never changed task by task, per 07-ui.md's
          own instruction. */}
      <NavLink to={{ pathname: '/scan', search: location.search }} className={linkClassName}>
        Scan
      </NavLink>
      <NavLink to={{ pathname: '/regime', search: location.search }} className={linkClassName}>
        Regime
      </NavLink>
      <NavLink to={{ pathname: '/rotation', search: location.search }} className={linkClassName}>
        Rotation
      </NavLink>
      <NavLink to={{ pathname: '/flows', search: location.search }} className={linkClassName}>
        Flows
      </NavLink>
      <NavLink to={{ pathname: '/settings', search: location.search }} className={linkClassName}>
        Settings
      </NavLink>
    </nav>
  );
}

export function AppShell() {
  return (
    <div className="app-shell">
      <TopBar />
      <NavBar />
      <main>
        <Outlet />
      </main>
    </div>
  );
}
