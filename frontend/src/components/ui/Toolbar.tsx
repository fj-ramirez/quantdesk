/**
 * T64 — Toolbar + SegmentedControl: the compact, URL-backed filter/toggle row already used
 * ad hoc on Scan/Regime/Rotation/Flows/Decisions (each page wrote its own small
 * `ButtonGroup`-shaped function around `.scan-toolbar__group`/`aria-pressed` buttons —
 * `Decisions.tsx`'s local `ButtonGroup`, `Scan.tsx`'s `ChoiceRow`, and equivalents in
 * `Regime.tsx`/`Rotation.tsx`/`Flows.tsx`). This generalizes that existing vocabulary into one
 * component instead of inventing a third one, per `01-ux-baseline.md`'s instruction to T64 —
 * the classNames below are the exact `.scan-toolbar*` rules those pages already render
 * through, so no new CSS was needed to give this primitive the app's existing look.
 *
 * `SegmentedControl` does not own state: like `ScanTable`, it is handed `value`/`onChange`
 * and whatever URL hook (`useScanParams`, `useDashboardParams`, ...) already drives the page
 * stays the single source of truth — unchanged by this primitive.
 */
import type { ReactNode } from 'react';

export interface ToolbarProps {
  children: ReactNode;
  className?: string;
}

export function Toolbar({ children, className }: ToolbarProps) {
  return <div className={className ? `scan-toolbar ${className}` : 'scan-toolbar'}>{children}</div>;
}

export interface SegmentedControlProps<T extends string | number> {
  label: string;
  values: readonly T[];
  active: T;
  render: (value: T) => string;
  onChange: (value: T) => void;
}

export function SegmentedControl<T extends string | number>({
  label,
  values,
  active,
  render,
  onChange,
}: SegmentedControlProps<T>) {
  return (
    <div className="scan-toolbar__group" role="group" aria-label={label}>
      <span className="scan-toolbar__label">{label}</span>
      {values.map((value) => (
        <button
          key={String(value)}
          type="button"
          className={value === active ? 'scan-toolbar__btn scan-toolbar__btn--active' : 'scan-toolbar__btn'}
          aria-pressed={value === active}
          onClick={() => onChange(value)}
        >
          {render(value)}
        </button>
      ))}
    </div>
  );
}
