/**
 * Typed paths for `/api/research/*` (T78).
 *
 * Transport is `src/lib/http.ts`, shared with every module — this file only knows which
 * endpoints exist and what they return.
 */
import { apiFetch } from '../../../lib/http';
import type {
  LeaderboardQuery,
  LeaderboardResponse,
  PaperCandidate,
  ResearchStatus,
  Trial,
} from './types';

export const researchClient = {
  /**
   * The ranked leaderboard **and the noise ceiling that gives it meaning**, in one response.
   *
   * Deliberately not two calls. A component that fetched rows and ceiling separately could
   * render the first while the second was still in flight — a leaderboard with no ceiling on
   * screen, which is the exact failure mode this module exists to prevent.
   */
  leaderboard(query: LeaderboardQuery = {}): Promise<LeaderboardResponse> {
    return apiFetch<LeaderboardResponse>('/api/research/leaderboard', {
      market: query.market,
      strategy: query.strategy,
      timeframe: query.timeframe,
      min_trades_oos: query.minTradesOos,
      min_exposure: query.minExposure,
      limit: query.limit,
      offset: query.offset,
    });
  },

  trial(hash: string): Promise<Trial> {
    return apiFetch<Trial>(`/api/research/trials/${hash}`);
  },

  paper(): Promise<PaperCandidate[]> {
    return apiFetch<PaperCandidate[]>('/api/research/paper');
  },

  status(): Promise<ResearchStatus> {
    return apiFetch<ResearchStatus>('/api/research/status');
  },
};
