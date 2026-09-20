/**
 * The research module's layout shell (T78).
 *
 * **Deliberately its own frame rather than a reuse of `shell/AppFrame`.** `AppFrame` is not the
 * generic shell its name suggests yet: it hard-mounts GEX's `ContextBar` (symbol/filter/snapshot
 * controls that mean nothing here) and a `SideRail` bound to GEX's `NAV_GROUPS`. Making it
 * generic is exactly **T81**'s job — "launcher and module shell, plus the module switcher" — and
 * half-building that switcher here would leave T81 undoing it rather than doing it.
 *
 * So this is a small, honest placeholder with the same visual vocabulary: the shared `Surface`,
 * `PageHeader` and table primitives do the work, and only the chrome differs. When T81 lands a
 * real module shell, this file should disappear into it and the two links below become part of
 * the shared nav.
 */
import { NavLink, Outlet } from 'react-router-dom';
import { ModuleHeader, RailBrand } from '../../shell/ModuleIdentity';

const LINKS = [
  { to: '/research', label: 'Leaderboard', end: true },
  { to: '/research/paper', label: 'Paper candidates', end: false },
];

export function ResearchFrame() {
  return (
    <div className="app-frame app-frame--research">
      <aside className="side-rail">
        <RailBrand moduleKey="research" />
        <nav className="side-rail__nav" aria-label="Primary">
          <div className="side-rail__group">
            <div className="side-rail__group-title">Research</div>
            {LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={({ isActive }) =>
                  `side-rail__link${isActive ? ' side-rail__link--active' : ''}`
                }
              >
                <span className="side-rail__label">{link.label}</span>
              </NavLink>
            ))}
          </div>
        </nav>
      </aside>
      <div className="app-frame__main">
        <main className="app-frame__content">
          <ModuleHeader moduleKey="research" />
          <Outlet />
        </main>
      </div>
    </div>
  );
}
