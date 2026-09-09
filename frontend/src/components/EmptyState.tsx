/**
 * T55 — the shared "there is honestly nothing here yet, and here's why" component (T37's
 * empty-state half: a clean 404, or a nav link to a page that hasn't shipped, is not a
 * failure and must not be styled like one). Extracted from the shape `Report.tsx`'s
 * `NoDataYet` already used inline (T40) — see that component for the pattern this
 * generalizes: a heading, an explanation, and an optional recovery action.
 *
 * Every page that needs an empty state uses this rather than writing its own `<section>` —
 * the kit's whole point (07-ui.md: "Nothing in the shared kit may be reimplemented inside a
 * page"). The nav's "not built yet" stub routes (T55's own `App.tsx` change) are the
 * simplest possible caller: a heading and nothing else.
 */
import type { ReactNode } from 'react';

export interface EmptyStateAction {
  label: string;
  onClick: () => void;
  /** True while the action's own request is in flight (e.g. `useCaptureSnapshot`'s
   * `isPending`) — disables the button and swaps in `pendingLabel`. */
  pending?: boolean;
  pendingLabel?: string;
}

export interface EmptyStateProps {
  /** Short statement of what's missing — becomes the section's accessible name too, so a
   * page can target it with `getByRole('region', { name: heading })` the same way
   * `Report.test.tsx` already does for `NoDataYet`. */
  heading: string;
  /** The explanation: what happens on its own, and/or why this view is empty. Optional —
   * the nav stub pages have nothing more to say than the heading itself. */
  children?: ReactNode;
  /** A recovery affordance, e.g. T37's "Capture now". Omitted entirely (not a disabled
   * button) when there is nothing the user can do about it. */
  action?: EmptyStateAction;
  /** Surfaced when `action`'s own request fails — never the raw response body (T37). */
  errorMessage?: ReactNode;
}

export function EmptyState({ heading, children, action, errorMessage }: EmptyStateProps) {
  return (
    <section aria-label={heading} className="empty-state">
      <h2 className="empty-state__heading">{heading}</h2>
      {children != null && <p className="empty-state__description">{children}</p>}
      {action && (
        <button type="button" onClick={action.onClick} disabled={action.pending}>
          {action.pending ? (action.pendingLabel ?? 'Working…') : action.label}
        </button>
      )}
      {errorMessage != null && (
        <p role="alert" className="empty-state__error">
          {errorMessage}
        </p>
      )}
    </section>
  );
}
