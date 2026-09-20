/**
 * The module registry — one file, exactly as `plans/quantdesk/04-launcher-shell.md` specifies
 * ("id, label, route, icon ... Adding a fourth module later is an entry there plus a router
 * entry plus a backend router — visible, boring and easy to get right").
 *
 * It was a literal array inside `Launcher.tsx` until three other surfaces needed the same
 * three facts: the rail brand under each module's nav, the identity strip at the top of each
 * module, and the accent that tells the three apart. The entries are T75's, unchanged — same
 * keys, same names, same blurbs, same routes.
 *
 * `accent` is the one new field, and it is not decoration: it is the same blue/teal/purple the
 * launcher's three cards already carried, promoted to a token (`--module-accent`) that each
 * module's frame sets once. Everything inside a module that wants to say "this module" —
 * the active nav pill, a metric card's edge, the identity mark — reads that variable instead
 * of picking a colour, so a module has one accent rather than one per component.
 */
import { IconEdgeLabMark, IconGexMark, IconXactxMark } from './icons';
import type { ReactElement, SVGProps } from 'react';

export type IconComponent = (props: SVGProps<SVGSVGElement>) => ReactElement;

export type ModuleKey = 'gex' | 'research' | 'terminal';

export interface ModuleEntry {
  key: ModuleKey;
  name: string;
  blurb: string;
  /** What kind of work this module is, in three words — the launcher card's bottom-left tag. */
  tag: string;
  /** `null` until the module exists. All three are live as of T80. */
  to: string | null;
  mark: IconComponent;
  /** This module's identity colour, as a CSS colour. Read through `--module-accent`. */
  accent: string;
  /** The same hue, dark enough to be read as text on a light surface. */
  accentLight: string;
}

export const MODULES: readonly ModuleEntry[] = [
  {
    key: 'gex',
    name: 'GEX',
    blurb: 'Gamma exposure, walls and flip points for SPX, SPY, QQQ, GLD, DIA and the scan universe.',
    tag: 'Options Flow & Gamma',
    to: '/gex',
    mark: IconGexMark,
    accent: '#6ea8ff',
    accentLight: '#2f6fe0',
  },
  {
    key: 'research',
    name: 'EdgeLab',
    blurb: 'Systematic edge search and a paper-traded leaderboard, with the noise ceiling that says which rows mean anything.',
    tag: 'Strategy Research',
    to: '/research',
    mark: IconEdgeLabMark,
    accent: '#16c7b7',
    accentLight: '#0d9488',
  },
  {
    key: 'terminal',
    name: 'xactx',
    blurb: 'Cross-asset change board, macro regime, transmission graph and the daily brief — rendered as of any moment you choose.',
    tag: 'Macro & Cross-Asset',
    to: '/terminal',
    mark: IconXactxMark,
    accent: '#8b6cff',
    accentLight: '#6341e8',
  },
];

export function moduleByKey(key: ModuleKey): ModuleEntry {
  const entry = MODULES.find((module) => module.key === key);
  // Unreachable through the type, but a missing module should be loud rather than a frame
  // that renders with no identity at all.
  if (!entry) throw new Error(`Unknown module: ${key}`);
  return entry;
}
