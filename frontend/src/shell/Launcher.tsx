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
 * The page is a thin top bar, a hero, and one card per module — and nothing else. It carried a
 * nav rail for one revision; the rail's six destinations were three module roots (which are
 * the cards), the page you were already on, and two pages one click inside a module, so it was
 * a second copy of the cards down the left-hand side. A launcher with three things on it does
 * not need navigation to reach them.
 *
 * Two things on the page are real rather than decorative, which is the difference between this
 * and a template:
 *
 *  - the search pill opens the real `CommandPalette` (Ctrl/Cmd+K works here too), which is now
 *    also how you reach a page *inside* a module without going through its card;
 *  - the quote strip is `/api/gex/scan/regime`, labelled and dated — see `LauncherTape`.
 *
 * The candlestick field behind the hero is the one thing on the page that is invented, and it
 * is drawn as texture at single-digit opacity with no axis, symbol or price anywhere near it
 * (`LauncherBackdrop`).
 */
import { Link } from 'react-router-dom';
import { IconArrowRight, IconGexMark, IconMoon, IconSearch, IconSun } from './icons';
import { CommandPalette } from './CommandPalette';
import { openCommandPalette } from './commandPaletteBus';
import { BuildStamp } from './BuildStamp';
import { LauncherBackdrop } from './LauncherBackdrop';
import { LauncherTape } from './LauncherTape';
import { MODULES, type ModuleEntry } from './modules';
import { useTheme } from '../theme/ThemeContext';

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
      <div className="lx__main">
        <header className="lx__topbar">
          {/* The page's `<h1>`: on a launcher, the desk's own name *is* the heading, and the
              hero line below is its subtitle. `aria-label` carries the name so the two-tone
              wordmark cannot be announced as "quant desk". */}
          <h1 className="lx-brand" aria-label="quantdesk">
            <span className="lx-brand__mark" aria-hidden="true">
              <IconGexMark width={20} height={20} />
            </span>
            <span className="lx-brand__word" aria-hidden="true">
              <span className="lx-brand__quant">quant</span>
              <span className="lx-brand__desk">desk</span>
            </span>
          </h1>

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

          <p className="lx-footnote">Better data. Deeper analysis. Smarter decisions.</p>

          {/* Which build each container is running. Here rather than inside a module,
              because it is a fact about the deployment and not about GEX. */}
          <BuildStamp />
        </main>
      </div>

      <CommandPalette />
    </div>
  );
}
