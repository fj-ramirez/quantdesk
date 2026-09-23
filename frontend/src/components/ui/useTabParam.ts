import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * The active tab as a URL search param. An absent or unrecognized value falls back to the
 * first allowed one rather than throwing — the same degrade-gracefully rule as
 * `useDashboardParams`. The default is never written to the URL, so an untouched page keeps
 * a clean link.
 */
export function useTabParam<T extends string>(key: string, values: readonly T[]): [T, (next: T) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get(key);
  const active = values.includes(raw as T) ? (raw as T) : values[0];

  const setActive = useCallback(
    (next: T) => {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev);
          if (next === values[0]) params.delete(key);
          else params.set(key, next);
          return params;
        },
        { replace: true },
      );
    },
    [key, values, setSearchParams],
  );

  return [active, setActive];
}
