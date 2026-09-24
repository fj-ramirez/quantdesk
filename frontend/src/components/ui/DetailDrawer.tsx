/**
 * T64 — DetailDrawer: a focus-managed evidence panel, built on `useOverlayDismiss`
 * (`components/layout/useOverlayDismiss.ts`), the same Escape/backdrop-click/focus-trap/
 * focus-return hook `SideRail`'s narrow drawer and `CommandPalette` already share. No
 * detail/drawer view in the app manages focus correctly today (`01-ux-baseline.md`: checked
 * `OpportunityDetail`, `BreakoutDetail`, `TrendDetail` — none has Escape, a focus trap, or
 * focus-return) — this is new, corrective work, not a preservation task.
 *
 * Responsive behavior per the plan ("open underlying evidence in a desktop side panel or
 * narrow-screen drawer"): at desktop widths the panel renders docked in normal page flow (no
 * dimming scrim — `.detail-drawer-scrim` is a plain, non-positioned wrapper there, via CSS,
 * not a conditional render, the same "CSS decides, jsdom can't see it" approach
 * `SideRail.test.tsx` already documents for its own responsive rail/drawer split); at or below
 * 640px it becomes a fixed, full-viewport overlay with a dimmed scrim, sliding up from the
 * bottom. In both cases the panel itself is `role="dialog"`/`aria-modal="true"` and traps
 * focus/returns it on close — a deliberate simplification over having two different focus
 * disciplines per breakpoint (see this task's report for the reasoning).
 *
 * **Caller contract — this component must always be mounted, never `{open && <DetailDrawer
 * .../>}`.** `useOverlayDismiss`'s focus-return effect only fires when its own `open` argument
 * changes value on an already-mounted hook instance; if the caller instead conditionally
 * mounts/unmounts this component, the hook itself is destroyed on close and the "return focus
 * to the trigger" effect never runs. `CommandPalette` already follows this exact pattern
 * (always rendered by `AppFrame`, calls the hook unconditionally, then `if (!open) return
 * null` near the end) — `DetailDrawer` does the same. Pass content as `children` computed
 * from the caller's own selection state (e.g. `{selected && <Foo .../>}`); evaluating that
 * expression when `selected` is `null` is cheap and harmless even though this component
 * discards it by returning `null` first.
 */
import type { ReactNode } from 'react';
import { useOverlayDismiss } from '../../shell/useOverlayDismiss';

export interface DetailDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Visible heading and the panel's accessible name (`aria-label`). */
  title: string;
  children?: ReactNode;
  /** T122: a wider sheet, for fixed-width content such as the report's full text. */
  wide?: boolean;
}

export function DetailDrawer({ open, onClose, title, children, wide }: DetailDrawerProps) {
  const containerRef = useOverlayDismiss<HTMLElement>(open, onClose);

  if (!open) return null;

  return (
    <div className="detail-drawer-scrim">
      <section ref={containerRef} role="dialog" aria-modal="true" aria-label={title}
        className={wide ? 'detail-drawer detail-drawer--wide' : 'detail-drawer'}
      >
        <header className="detail-drawer__head">
          <h2 className="detail-drawer__title">{title}</h2>
          <button type="button" className="detail-drawer__close" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="detail-drawer__body">{children}</div>
      </section>
    </div>
  );
}
