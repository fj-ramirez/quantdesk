/**
 * The launcher at `/` — pick a module (T75).
 *
 * **Deliberately plain.** T75's brief says so in as many words: "Launcher design. T75 ships a
 * plain list; T81 makes it a page worth looking at." This exists so that `/` is not a 404
 * once GEX moves to `/gex`, and so the shape of the module registry is visible in one file
 * before three modules need it. It is not a design.
 *
 * The registry is a literal array rather than anything derived, matching the backend's
 * `app/main.py`: three visible entries beat a discovery mechanism, and a module that is
 * missing is then obvious on the page rather than silently absent.
 */
import { Link } from 'react-router-dom';

interface ModuleEntry {
  key: string;
  name: string;
  blurb: string;
  /** `null` until the module exists. Research filled this in at T78; terminal is T80. */
  to: string | null;
}

const MODULES: readonly ModuleEntry[] = [
  {
    key: 'gex',
    name: 'GEX',
    blurb: 'Gamma exposure, walls and flip points for SPX, SPY, QQQ, GLD, DIA and the scan universe.',
    to: '/gex',
  },
  {
    key: 'research',
    name: 'EdgeLab',
    blurb: 'Systematic edge search and a paper-traded leaderboard, with the noise ceiling that says which rows mean anything.',
    to: '/research',
  },
  {
    key: 'terminal',
    name: 'xactx',
    blurb: 'Cross-asset board, macro regime and the daily brief. Arrives in T79.',
    to: null,
  },
];

export function Launcher() {
  return (
    <main className="launcher">
      <h1 className="launcher__title">quantdesk</h1>
      <ul className="launcher__list">
        {MODULES.map((module) => (
          <li key={module.key} className="launcher__item">
            {module.to ? (
              <Link className="launcher__link" to={module.to}>
                <span className="launcher__name">{module.name}</span>
                <span className="launcher__blurb">{module.blurb}</span>
              </Link>
            ) : (
              // Not a disabled <Link>: a link to nowhere is still focusable and still
              // announced as a link. A plain element with `aria-disabled` says what it is.
              <span className="launcher__link launcher__link--disabled" aria-disabled="true">
                <span className="launcher__name">{module.name}</span>
                <span className="launcher__blurb">{module.blurb}</span>
              </span>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
