/**
 * The broker module's layout shell (T159). Same shape as `ResearchFrame`, for the same reason
 * (T81 owns the shared module shell): the shared rail brand, one nav group, the identity strip.
 */
import { NavLink, Outlet } from 'react-router-dom';
import { ModuleHeader, RailBrand } from '../../shell/ModuleIdentity';

const LINKS = [{ to: '/broker', label: 'Chart', end: true }];

export function BrokerFrame() {
  return (
    <div className="app-frame app-frame--broker">
      <aside className="side-rail">
        <RailBrand moduleKey="broker" />
        <nav className="side-rail__nav" aria-label="Primary">
          <div className="side-rail__group">
            <div className="side-rail__group-title">Broker</div>
            {LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={({ isActive }) => `side-rail__link${isActive ? ' side-rail__link--active' : ''}`}
              >
                <span className="side-rail__label">{link.label}</span>
              </NavLink>
            ))}
          </div>
        </nav>
      </aside>
      <div className="app-frame__main">
        <main className="app-frame__content">
          <ModuleHeader moduleKey="broker" />
          <Outlet />
        </main>
      </div>
    </div>
  );
}
