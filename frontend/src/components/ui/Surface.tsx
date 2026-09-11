/**
 * T64 — the base card/panel container every other data-display primitive in this directory
 * composes on top of: a background/border/radius/elevation block reading only from T63's
 * semantic surface tokens (`--surface-app`/`--surface-raised`/`--surface-subtle`,
 * `--border-default`, `--radius-md`, `--elevation-1` in index.css). No primitive below
 * declares its own background/border/shadow; each wraps a `Surface` instead, so a future
 * palette change only ever has to touch this one file's CSS rules.
 */
import type { ElementType, ReactNode } from 'react';

export type SurfaceLevel = 'app' | 'raised' | 'subtle';

export interface SurfaceProps {
  children: ReactNode;
  /** Which semantic surface token supplies the background. Defaults to `raised`, the level
   * a standalone card/panel reads on this workbench. */
  level?: SurfaceLevel;
  /** Border + radius + elevation-1 shadow, i.e. renders as a visible "card" rather than a
   * flat background fill. Defaults to true. */
  bordered?: boolean;
  /** Base padding. Defaults to true; a primitive that wraps a component with its own
   * padding (e.g. a table) passes `false` to avoid doubling it. */
  padded?: boolean;
  as?: ElementType;
  className?: string;
  'aria-label'?: string;
}

export function Surface({
  children,
  level = 'raised',
  bordered = true,
  padded = true,
  as: Component = 'div',
  className,
  ...rest
}: SurfaceProps) {
  const classes = [
    'ui-surface',
    `ui-surface--${level}`,
    bordered ? 'ui-surface--bordered' : '',
    padded ? 'ui-surface--padded' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <Component className={classes} {...rest}>
      {children}
    </Component>
  );
}
