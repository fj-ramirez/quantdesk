/**
 * The two places a module says which module it is: the brand block at the top of its nav rail,
 * and the identity strip at the top of its content.
 *
 * Both used to be per-frame markup — `ResearchFrame` and `TerminalFrame` each wrote their own
 * brand block and GEX's `SideRail` had none at all, so the one place the app tells you where
 * you are looked different in each of the three. They read `shell/modules.ts` now, so a module
 * has one name, one mark and one accent wherever it appears.
 *
 * `RailBrand`'s wordmark is also the way back to the launcher, which is what it already was in
 * two of the three frames. It stays a link for that reason, with the module name beside it as
 * plain text — the module name is not a destination, you are already there.
 */
import { Link } from 'react-router-dom';
import { IconGexMark } from './icons';
import { moduleByKey, type ModuleKey } from './modules';

export function RailBrand({ moduleKey }: { moduleKey: ModuleKey }) {
  const module = moduleByKey(moduleKey);
  return (
    <div className="side-rail__brand">
      {/* Back to the launcher — until the in-frame module switcher
          (`plans/quantdesk/04-launcher-shell.md`) exists, this is the only way between modules
          that does not involve editing the URL. */}
      <Link to="/" className="side-rail__home" aria-label="quantdesk — all modules">
        <span className="side-rail__home-mark" aria-hidden="true">
          <IconGexMark width={17} height={17} />
        </span>
        <span aria-hidden="true">
          <span className="side-rail__home-quant">quant</span>
          <span className="side-rail__home-desk">desk</span>
        </span>
      </Link>
      <span className="side-rail__module">{module.name}</span>
    </div>
  );
}

/**
 * The identity strip at the top of a module's content: mark, name, and the same one-line blurb
 * the launcher card carries. It is deliberately not a heading — every page under it already
 * renders its own `<h1>` through `PageHeader`, and a second competing heading would make the
 * page's outline read as though the module were the page.
 */
export function ModuleHeader({ moduleKey }: { moduleKey: ModuleKey }) {
  const module = moduleByKey(moduleKey);
  const Mark = module.mark;
  return (
    <div className="module-header">
      <span className="module-header__mark" aria-hidden="true">
        <Mark width={22} height={22} />
      </span>
      <p className="module-header__name">{module.name}</p>
      <p className="module-header__blurb">{module.blurb}</p>
    </div>
  );
}
