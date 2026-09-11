/**
 * T64 — DataTableFrame: title, a one-line reading cue, a source/as-of timing line, and an
 * optional density/expand slot wrapped around the table itself. Generalizes the pattern
 * every scan-family page already assembles by hand (a `.scan-legend` paragraph sitting above
 * a `ScanTable`) into one primitive per the plan's "Standardize chart/table frames" spec.
 * `ScanTable`'s own internals — sorting, `aria-sort`, null-sorts-last, keyboard row
 * activation — are untouched; this only wraps it, the same way `Surface` wraps a card without
 * reaching into what it contains.
 */
import type { ReactNode } from 'react';
import { Surface } from './Surface';

export interface DataTableFrameProps {
  title: string;
  /** One sentence: what this table answers / how to read it. */
  readingCue?: ReactNode;
  /** Source/as-of timing line, e.g. the decision engine's `generated_from` disclaimer. */
  sourceTiming?: ReactNode;
  /** A density toggle, expand action, or similar compact control for the table's own
   * chrome — rendered in the header row, right-aligned. */
  actions?: ReactNode;
  children: ReactNode;
}

export function DataTableFrame({ title, readingCue, sourceTiming, actions, children }: DataTableFrameProps) {
  return (
    <Surface className="data-table-frame" level="app" bordered={false} padded={false}>
      <div className="data-table-frame__head">
        <div className="data-table-frame__heading">
          <h2 className="data-table-frame__title">{title}</h2>
          {readingCue != null && <p className="data-table-frame__cue">{readingCue}</p>}
        </div>
        {actions != null && <div className="data-table-frame__actions">{actions}</div>}
      </div>
      {sourceTiming != null && <p className="data-table-frame__timing">{sourceTiming}</p>}
      {children}
    </Surface>
  );
}
