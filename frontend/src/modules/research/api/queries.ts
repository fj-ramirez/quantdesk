/**
 * TanStack Query hooks, one per `/api/research/*` endpoint (T78).
 *
 * Same shape as `modules/gex/api/queries.ts`: a `queryKeys` table so nothing duplicates a key
 * shape, and one hook per endpoint so components never call the client directly.
 */
import { useQuery } from '@tanstack/react-query';
import { researchClient } from './client';
import type { LeaderboardQuery } from './types';

export const researchQueryKeys = {
  leaderboard: (query: LeaderboardQuery) => ['research-leaderboard', query] as const,
  trial: (hash: string) => ['research-trial', hash] as const,
  paper: () => ['research-paper'] as const,
  status: () => ['research-status'] as const,
};

export function useLeaderboard(query: LeaderboardQuery) {
  return useQuery({
    queryKey: researchQueryKeys.leaderboard(query),
    queryFn: () => researchClient.leaderboard(query),
    // Keeps the previous page on screen while the next one loads, so paging and changing a
    // filter do not blank the table (and, more to the point, do not blank the ceiling banner
    // above it).
    placeholderData: (previous) => previous,
  });
}

export function useTrial(hash: string | null) {
  return useQuery({
    queryKey: researchQueryKeys.trial(hash ?? ''),
    queryFn: () => researchClient.trial(hash as string),
    enabled: hash != null,
  });
}

export function usePaperCandidates() {
  return useQuery({
    queryKey: researchQueryKeys.paper(),
    queryFn: () => researchClient.paper(),
  });
}

export function useResearchStatus() {
  return useQuery({
    queryKey: researchQueryKeys.status(),
    queryFn: () => researchClient.status(),
    // The search runs nightly. Re-asking on every focus would be pure noise.
    staleTime: 5 * 60 * 1000,
  });
}
