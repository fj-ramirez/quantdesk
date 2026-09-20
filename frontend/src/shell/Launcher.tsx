/**
 * The launcher at `/` — pick a module (T75, redesigned).
 *
 * T75 shipped a deliberately plain list and said so in as many words: "T75 ships a plain
 * list; T81 makes it a page worth looking at." This is that page — but only that half of it.
 * `plans/quantdesk/04-launcher-shell.md` scopes T81 to three things, and this change is one
 * of them: **the launcher's design**. The other two are *not* built here and T81 is not done
 * — there is no `GET /api/<module>/summary` (so no per-module freshness, no "this worker has
 * not run" on a card, which the plan calls the whole point of the card) and no in-frame
 * module switcher.
 *
 * What did *not* change is the thing T75 built the file around: the module registry is still a
 * literal array of three entries, matching the backend's `app/main.py` — three visible entries
 * beat a discovery mechanism, and a module that is missing is then obvious on the page rather
 * than silently absent. Every route, every destination and all three blurbs are byte-for-byte
 * T75's. It now lives in `shell/modules.ts`, because the module frames need the same three
 * facts (`plans/quantdesk/04-launcher-shell.md`: "the module registry is one file").
 *
 * The page is a desk shell rather than a marketing splash: a fixed rail (the same nav idea as
 * `SideRail`, but pointing at *modules*, not at GEX's pages), a thin top bar, a hero, and one
 * card per module. Three things on it are real rather than decorative, which is the whole
 * difference between this and a template:
 *
 *  - the rail's six destinations are existing routes, not placeholders;
 *  - the search pill opens the real `CommandPalette` (Ctrl/Cmd+K works here now too);
 *  - the quote strip is `/api/gex/scan/regime`, labelled and dated — see `LauncherTape`.
 *
 * The candlestick field behind the hero is the one thing on the page that is invented, and it
 * is drawn as texture at single-digit opacity with no axis, symbol or price anywhere near it
 * (`LauncherBackdrop`).
 */
import { Link, NavLink } from 'react-router-dom';
import {
  IconAnalysis,
  IconArrowRight,
  IconBacktest,
  IconGexMark,
  IconHome,
  IconMarkets,
  IconMoon,
  IconSearch,
  IconSettings,
  IconSun,
  IconWatchlist,
} from './icons';
import { CommandPalette } from './CommandPalette';
import { openCommandPalette } from './commandPaletteBus';
import { LauncherBackdrop } from './LauncherBackdrop';
import { LauncherTape } from './LauncherTape';
import { MODULES, type IconComponent, type ModuleEntry } from './modules';
import { useTheme } from '../theme/ThemeContext';

interface RailItem {
  key: string;
  label: string;
  to: string;
  icon: IconComponent;
  /** `NavLink`'s own `end` — only the launcher itself needs it, since `/` prefixes everything. */
  end?: boolean;
}

/**
 * The rail's destinations. Every one is a route that exists today (`App.tsx`) — this page
 * does not link anywhere it cannot go. The labels are the desk's vocabulary rather than each
 * module's internal name: "Markets" is GEX's board, "Analysis" is the xactx terminal,
 * "Backtesting" is EdgeLab's search, "Watchlist" is EdgeLab's paper book. Renaming a label
 * here renames nothing inside a module.
 */
const RAIL_ITEMS: readonly RailItem[] = [
  { key: 'home', label: 'Home', to: '/', icon: IconHome, end: true },
  { key: 'markets', label: 'Markets', to: '/gex', icon: IconMarkets },
  { key: 'analysis', label: 'Analysis', to: '/terminal', icon: IconAnalysis },
  { key: 'backtesting', label: 'Backtesting', to: '/research', icon: IconBacktest },
  { key: 'watchlist', label: 'Watchlist', to: '/research/paper', icon: IconWatchlist },
  { key: 'settings', label: 'Settings', to: '/gex/settings', icon: IconSettings },
];

/** The card's inner content, shared by the linked and the not-yet-built cases so the two can
 * never drift apart visually. */
function CardBody({ module }: { module: ModuleEntry }) {
  const Mark = module.mark;
  return (
    <>
      <span className="lx-card__mark" aria-hidden="true">
        <Mark width={26} height={26} />
      </span>
      <span className="lx-card__name">{module.name}</span>
      <span className="lx-card__blurb">{module.blurb}</span>
      <span className="lx-card__foot">
        <span className="lx-card__tag">{module.tag}</span>
        <span className="lx-card__go" aria-hidden="true">
          <IconArrowRight width={20} height={20} />
        </span>
      </span>
    </>
  );
}

function ThemeButton() {
  const { theme, toggleTheme } = useTheme();
  const nextLabel = theme === 'light' ? 'dark' : 'light';
  return (
    <button
      type="button"
      className="lx-iconbutton"
      onClick={toggleTheme}
      aria-label={`Switch to ${nextLabel} theme`}
      title={`Switch to ${nextLabel} theme`}
    >
      {theme === 'light' ? <IconMoon /> : <IconSun />}
    </button>
  );
}

export function Launcher() {
  return (
    <div className="lx">
      <aside className="lx__rail">
        {/* The page's `<h1>`: on a launcher, the desk's own name *is* the heading, and the
            hero line below is its subtitle. `aria-label` carries the name so the two-tone
            wordmark cannot be announced as "quant desk". */}
        <h1 className="lx-brand" aria-label="quantdesk">
          <span className="lx-brand__mark" aria-hidden="true">
            <IconGexMark width={22} height={22} />
          </span>
          <span className="lx-brand__word" aria-hidden="true">
            <span className="lx-brand__quant">quant</span>
            <span className="lx-brand__desk">desk</span>
          </span>
        </h1>

        <nav className="lx-rail-nav" aria-label="Desk">
          {RAIL_ITEMS.map((item) => {
            const Glyph = item.icon;
            return (
              <NavLink
                key={item.key}
                to={item.to}
                end={item.end}
                className={({ isActive }) => `lx-rail-nav__link${isActive ? ' lx-rail-nav__link--active' : ''}`}
              >
                <Glyph className="lx-rail-nav__icon" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>

        <p className="lx-rail-note">
          Better data.
          <br />
          Deeper analysis.
          <br />
          Smarter decisions.
        </p>
      </aside>

      <div className="lx__main">
        <header className="lx__topbar">
          <button type="button" className="lx-search" onClick={openCommandPalette}>
            <IconSearch className="lx-search__icon" />
            <span className="lx-search__text">Search markets, tickers, or tools…</span>
            <kbd className="lx-search__kbd">Ctrl + K</kbd>
          </button>
          <div className="lx__topbar-right">
            <span className="lx-status">
              <span className="lx-status__dot" aria-hidden="true" />
              System Online
            </span>
            <ThemeButton />
          </div>
        </header>

        <main className="lx__body">
          <LauncherBackdrop />

          <section className="lx-hero">
            <div className="lx-hero__copy">
              <p className="lx-hero__eyebrow">
                Quantdesk
                <span className="lx-hero__rule" aria-hidden="true" />
              </p>
              <h2 className="lx-hero__title">
                Smarter tools for <span className="lx-hero__title-accent">quantitative trading</span>.
              </h2>
              <p className="lx-hero__lede">
                Analyze markets, build strategies, backtest ideas and explore data — all in one place.
              </p>
            </div>
            <LauncherTape />
          </section>

          <ul className="lx-cards">
            {MODULES.map((module) => (
              <li key={module.key} className={`lx-card lx-card--${module.key}`}>
                {module.to ? (
                  <Link className="lx-card__face" to={module.to}>
                    <CardBody module={module} />
                  </Link>
                ) : (
                  // Not a disabled <Link>: a link to nowhere is still focusable and still
                  // announced as a link. A plain element with `aria-disabled` says what it is.
                  <span className="lx-card__face lx-card__face--disabled" aria-disabled="true">
                    <CardBody module={module} />
                  </span>
                )}
              </li>
            ))}
          </ul>
        </main>
      </div>

      <CommandPalette />
    </div>
  );
}
