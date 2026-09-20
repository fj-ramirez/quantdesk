/**
 * The terminal's shell: nav, the global as-of control, and the live regime strip (T80).
 *
 * The as-of control sits **here**, in the frame, and that placement is the whole design. Setting
 * it re-renders every screen under it, because this module's claim is not "here is a board with
 * a history feature" but "here is the world, at a moment you choose". A board pinned to March
 * 2020 beside a regime strip showing today would be worse than either alone.
 *
 * When a past moment is pinned the frame says so loudly and permanently — a coloured bar, not a
 * subtle input state. The failure this prevents is the expensive one: reading a historical board
 * as if it were live, which looks exactly like reading a live board.
 */
import { Link, NavLink, Outlet } from 'react-router-dom';
import { useRegime } from './api/queries';
import { toIsoInstant, toLocalInput, useAsOf } from './state/asOf';

const LINKS = [
  { to: '/terminal', label: 'Board', end: true },
  { to: '/terminal/regime', label: 'Regime', end: false },
  { to: '/terminal/graph', label: 'Transmission', end: false },
  { to: '/terminal/policy', label: 'Policy path', end: false },
  { to: '/terminal/brief', label: 'Brief', end: false },
];

const REGIME_LABEL: Record<string, string> = {
  risk_off: 'Risk off',
  risk_on: 'Risk on',
  rates_led: 'Rates led',
  quiet: 'Quiet',
};

function AsOfControl() {
  const { asOf, isHistorical, setAsOf, clear } = useAsOf();

  return (
    <div className={`asof${isHistorical ? ' asof--historical' : ''}`}>
      <label className="asof__field">
        <span className="asof__label">As of</span>
        <input
          type="datetime-local"
          value={toLocalInput(asOf)}
          onChange={(e) => setAsOf(toIsoInstant(e.target.value))}
          aria-label="As of"
        />
      </label>
      {isHistorical ? (
        <>
          <span className="asof__badge" role="status">
            Historical — showing only what was knowable then
          </span>
          <button type="button" className="asof__clear" onClick={clear}>
            Back to live
          </button>
        </>
      ) : (
        <span className="asof__badge asof__badge--live" role="status">
          Live — latest known
        </span>
      )}
    </div>
  );
}

function RegimeStrip() {
  const { asOf } = useAsOf();
  const regime = useRegime(asOf);

  if (regime.isPending) return <div className="regime-strip regime-strip--loading">Regime…</div>;
  if (regime.isError || !regime.data) return null;

  const { state, evidence, window_days: windowDays } = regime.data;
  return (
    <div className={`regime-strip regime-strip--${state}`}>
      <span className="regime-strip__state">{REGIME_LABEL[state] ?? state}</span>
      <span className="regime-strip__window">{windowDays}d</span>
      {/* The evidence, always. A regime label with no z-scores behind it is an opinion you
          cannot check, and this strip is the most-seen surface in the module. */}
      {Object.entries(evidence).map(([leg, z]) => (
        <span className="regime-strip__leg" key={leg}>
          <span className="regime-strip__leg-name">{leg}</span>
          <span className="regime-strip__leg-z">{z == null ? '—' : z.toFixed(2)}</span>
        </span>
      ))}
    </div>
  );
}

export function TerminalFrame() {
  return (
    <div className="app-frame terminal">
      <aside className="side-rail">
        <div className="side-rail__brand">
          <Link to="/" className="side-rail__home">
            quantdesk
          </Link>
          <span className="side-rail__module">xactx</span>
        </div>
        <nav className="side-rail__nav" aria-label="Primary">
          <div className="side-rail__group">
            <div className="side-rail__group-title">Cross-asset</div>
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
        <div className="terminal__topbar">
          <AsOfControl />
          <RegimeStrip />
        </div>
        <main className="app-frame__content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
