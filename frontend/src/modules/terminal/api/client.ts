/**
 * Typed paths for `/api/terminal/*` (T80).
 *
 * **Every call takes `asOf`.** Not an optional refinement on one screen — the parameter is the
 * module's reason to exist, so it is threaded through every endpoint and every hook rather than
 * being something a page can forget to pass.
 */
import { apiFetch } from '../../../lib/http';
import type {
  BoardResponse,
  BriefResponse,
  EdgesResponse,
  PolicyResponse,
  RegimeResponse,
  TerminalSeries,
} from './types';

/** Undefined means "latest known" — the API's own default. Never send an empty string. */
type AsOf = string | undefined;

export const terminalClient = {
  board(asOf: AsOf, assetClass?: string): Promise<BoardResponse> {
    return apiFetch<BoardResponse>('/api/terminal/board', {
      as_of: asOf,
      asset_class: assetClass,
    });
  },
  regime(asOf: AsOf): Promise<RegimeResponse> {
    return apiFetch<RegimeResponse>('/api/terminal/regime', { as_of: asOf });
  },
  edges(asOf: AsOf): Promise<EdgesResponse> {
    return apiFetch<EdgesResponse>('/api/terminal/edges', { as_of: asOf });
  },
  policy(asOf: AsOf): Promise<PolicyResponse> {
    return apiFetch<PolicyResponse>('/api/terminal/policy', { as_of: asOf });
  },
  brief(asOf: AsOf): Promise<BriefResponse> {
    return apiFetch<BriefResponse>('/api/terminal/brief', { as_of: asOf });
  },
  series(): Promise<TerminalSeries[]> {
    return apiFetch<TerminalSeries[]>('/api/terminal/series');
  },
};
