/**
 * The as-of control's state (T80).
 *
 * **It lives in the URL, and it lives in the module frame rather than on a page.** Both of those
 * are the brief's requirements and both matter:
 *
 * - *In the URL*, because "the board on the morning of 16 March 2020" is a thing you want to
 *   send someone. A React state hook would make it a mood the tab is in.
 * - *In the frame*, because setting it re-renders **every** screen. It is not a filter on the
 *   change board that the regime page also happens to offer; it is the single question this
 *   module answers, and a terminal where the board was historical and the regime strip was live
 *   would be actively misleading.
 *
 * The value is an ISO 8601 instant. Absent means latest-known, which is the API's own default —
 * so `/terminal` with no query string is "now" and needs no special case.
 */
import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

export const AS_OF_PARAM = 'as_of';

export interface AsOfState {
  /** The raw ISO string, or undefined for latest-known. Pass straight to the API. */
  asOf: string | undefined;
  /** True when pinned to a past moment — the frame uses this to shout about it. */
  isHistorical: boolean;
  setAsOf: (next: string | undefined) => void;
  clear: () => void;
}

/** `datetime-local` gives `YYYY-MM-DDTHH:mm`; the API needs a timezone-aware instant. */
export function toIsoInstant(localValue: string): string | undefined {
  if (!localValue) return undefined;
  const parsed = new Date(localValue);
  if (Number.isNaN(parsed.getTime())) return undefined;
  return parsed.toISOString();
}

/** The inverse, for populating the input from the URL. */
export function toLocalInput(iso: string | undefined): string {
  if (!iso) return '';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}` +
    `T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  );
}

export function useAsOf(): AsOfState {
  const [params, setParams] = useSearchParams();
  const raw = params.get(AS_OF_PARAM) ?? undefined;

  const setAsOf = useCallback(
    (next: string | undefined) => {
      const updated = new URLSearchParams(params);
      if (next) updated.set(AS_OF_PARAM, next);
      else updated.delete(AS_OF_PARAM);
      // `replace` so scrubbing through history does not fill the back button with every
      // intermediate moment the user passed through.
      setParams(updated, { replace: true });
    },
    [params, setParams],
  );

  return {
    asOf: raw,
    isHistorical: raw != null && raw !== '',
    setAsOf,
    clear: () => setAsOf(undefined),
  };
}
