/**
 * T64 -- the shared "this is honestly still working" component, completing the third leg of
 * the loading/error/empty triad next to `EmptyState`/`ErrorState`. Extracted from the
 * `<p className="scan-page__loading">...</p>` every scan-family page (Scan/Regime/Rotation/
 * Flows/Decisions) already wrote inline -- this adds the one thing those bare paragraphs were
 * missing: `aria-live="polite"`, so a screen-reader user is told a page is still working
 * rather than hearing nothing until it resolves or errors.
 *
 * Kept alongside `EmptyState.tsx`/`ErrorState.tsx` at this flat `components/` location
 * (not nested under `components/ui/`) because that is where the existing loading/error/empty
 * triad already lives -- see `01-ux-baseline.md`'s note that `EmptyState`/`ErrorState` are
 * already the shared kit's precedent for "where a cross-page primitive belongs."
 */
import type { ReactNode } from 'react';

export interface LoadingStateProps {
  message: ReactNode;
}

export function LoadingState({ message }: LoadingStateProps) {
  return (
    <p className="loading-state" aria-live="polite">
      {message}
    </p>
  );
}
