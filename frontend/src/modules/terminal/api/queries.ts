/**
 * TanStack Query hooks for the terminal (T80).
 *
 * **`asOf` is part of every query key.** That is what makes the as-of control work as a control
 * rather than as a suggestion: changing it produces a different key, so every screen refetches
 * and none of them can quietly keep showing the previous world.
 *
 * `staleTime` is generous because this module is daily by design — the nightly ingest is the
 * only thing that changes the answer, and re-asking on window focus would be pure noise.
 */
import { useQuery } from '@tanstack/react-query';
import { terminalClient } from './client';

const STALE_MS = 5 * 60 * 1000;

export const terminalQueryKeys = {
  board: (asOf: string | undefined, assetClass?: string) =>
    ['terminal-board', asOf ?? 'latest', assetClass ?? 'all'] as const,
  regime: (asOf: string | undefined) => ['terminal-regime', asOf ?? 'latest'] as const,
  edges: (asOf: string | undefined) => ['terminal-edges', asOf ?? 'latest'] as const,
  policy: (asOf: string | undefined) => ['terminal-policy', asOf ?? 'latest'] as const,
  brief: (asOf: string | undefined) => ['terminal-brief', asOf ?? 'latest'] as const,
};

export function useBoard(asOf: string | undefined, assetClass?: string) {
  return useQuery({
    queryKey: terminalQueryKeys.board(asOf, assetClass),
    queryFn: () => terminalClient.board(asOf, assetClass),
    staleTime: STALE_MS,
    placeholderData: (previous) => previous,
  });
}

export function useRegime(asOf: string | undefined) {
  return useQuery({
    queryKey: terminalQueryKeys.regime(asOf),
    queryFn: () => terminalClient.regime(asOf),
    staleTime: STALE_MS,
  });
}

export function useEdges(asOf: string | undefined) {
  return useQuery({
    queryKey: terminalQueryKeys.edges(asOf),
    queryFn: () => terminalClient.edges(asOf),
    staleTime: STALE_MS,
  });
}

export function usePolicy(asOf: string | undefined) {
  return useQuery({
    queryKey: terminalQueryKeys.policy(asOf),
    queryFn: () => terminalClient.policy(asOf),
    staleTime: STALE_MS,
  });
}

export function useBrief(asOf: string | undefined) {
  return useQuery({
    queryKey: terminalQueryKeys.brief(asOf),
    queryFn: () => terminalClient.brief(asOf),
    staleTime: STALE_MS,
  });
}
