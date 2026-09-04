import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { TopBar } from './TopBar';

/** Route nav. `to` carries the *current* search string forward explicitly — React Router
 * does not do this by default (an absolute `to="/history"` drops query params), and
 * dropping them here would silently break the "URL fully drives the view" contract the
 * moment someone clicks between Dashboard/History/Settings. */
function NavBar() {
  const location = useLocation();
  return (
    <nav>
      <NavLink to={{ pathname: '/', search: location.search }} end>
        Dashboard
      </NavLink>
      <NavLink to={{ pathname: '/history', search: location.search }}>History</NavLink>
      <NavLink to={{ pathname: '/settings', search: location.search }}>Settings</NavLink>
    </nav>
  );
}

export function AppShell() {
  return (
    <div>
      <TopBar />
      <NavBar />
      <main>
        <Outlet />
      </main>
    </div>
  );
}
