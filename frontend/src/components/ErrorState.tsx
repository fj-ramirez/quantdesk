/**
 * T55 — the shared "this is a genuine failure" component (T37's error-state half, as
 * distinct from `EmptyState`'s "there's honestly nothing here"). `role="alert"` so an
 * assistive-technology user hears it without polling, matching every ad hoc `<p
 * role="alert">` this app already wrote before this extraction (`Dashboard.tsx`,
 * `History.tsx`, `Report.tsx`).
 *
 * Deliberately a thin wrapper, not a smarter one: it never inspects `message` for a raw
 * response body or a status code — that parsing already happens once, at the source
 * (`api/client.ts`'s `ApiError`/`parseErrorDetail`), so nothing here needs to re-derive "is
 * this actually an empty state" the way `Report.tsx`'s `noDataYet` check does. A page that
 * needs that distinction makes it before choosing `EmptyState` or `ErrorState` — this
 * component only renders the message it's handed.
 */
import type { ReactNode } from 'react';

export interface ErrorStateProps {
  message: ReactNode;
}

export function ErrorState({ message }: ErrorStateProps) {
  return (
    <p role="alert" className="error-state">
      {message}
    </p>
  );
}
