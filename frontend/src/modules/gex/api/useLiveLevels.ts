/**
 * T19 — subscribe to the backend's SSE channel and invalidate this symbol's queries whenever a
 * new snapshot's levels are stored.
 *
 * What this replaces: without it, a UI that wants to notice a capture has to poll. Captures
 * happen at most once per symbol per 15 minutes (T18), so polling either runs far too often or
 * leaves the dashboard minutes staler than the server. One long-lived connection per open tab
 * removes the choice.
 *
 * The event is a **nudge, not a payload** (see `app/modules/gex/api/stream.py`): it carries enough to show a
 * freshness stamp and decide whether this symbol is affected, and nothing that would let the
 * client skip re-fetching. So this hook invalidates and lets TanStack Query do the rest, using
 * the same "every query mentioning this underlying" predicate as the `Capture now` mutation —
 * a fresh capture changes the dashboard, the history page and the snapshot list together, and
 * invalidating only one of them is how a user ends up looking at a filled-in report behind an
 * empty chart.
 *
 * `EventSource` reconnects on its own, with its own backoff, so there is deliberately no retry
 * logic here. The connection state is surfaced only so the UI can say whether it is live.
 */
import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { API_BASE_URL } from './client';
import type { Underlying } from './types';

export type LiveStatus = 'unsupported' | 'connecting' | 'live' | 'reconnecting';

/** Shape of the `levels` event's JSON payload. Mirrors `app/modules/gex/api/stream.py`'s `_sse` call. */
export interface LiveLevelsEvent {
  underlying: Underlying;
  snapshot_id: number;
  captured_at: string;
  /** T34: the honest "as of" instant, which is what a staleness badge must read. Never
   * `captured_at`, which keeps advancing after the close while the quotes are frozen. */
  effective_at: string;
  is_eod: boolean;
  spot: number;
  skipped_duplicate: boolean;
}

export function useLiveLevels(underlying: Underlying): {
  status: LiveStatus;
  lastEvent: LiveLevelsEvent | null;
} {
  const queryClient = useQueryClient();
  // jsdom has no EventSource, and neither does any server-rendering context. That is resolved
  // in the initializer rather than by a `setState` inside the effect: environment support does
  // not change over a component's life, so deriving it once avoids a second render — and a
  // synchronous `setState` in an effect is a cascading-render smell the linter rejects outright.
  const [status, setStatus] = useState<LiveStatus>(() =>
    typeof EventSource === 'undefined' ? 'unsupported' : 'connecting',
  );
  const [lastEvent, setLastEvent] = useState<LiveLevelsEvent | null>(null);

  useEffect(() => {
    // Falling through to no subscription means a test or a prerender renders the component
    // exactly as it would without live updates, which is the correct fallback: the data is
    // still fetched, it just does not refresh by itself.
    if (typeof EventSource === 'undefined') return;

    const source = new EventSource(new URL(`/api/gex/stream/${underlying}`, API_BASE_URL).toString());

    const onReady = () => setStatus('live');
    const onLevels = (event: MessageEvent<string>) => {
      setStatus('live');
      let parsed: LiveLevelsEvent | null = null;
      try {
        parsed = JSON.parse(event.data) as LiveLevelsEvent;
      } catch {
        // A frame we cannot parse is not a reason to stop listening or to skip refreshing:
        // something changed upstream either way, and the re-fetch is what actually matters.
        parsed = null;
      }
      if (parsed) setLastEvent(parsed);
      void queryClient.invalidateQueries({
        predicate: (query) => (query.queryKey as unknown[]).includes(underlying),
      });
    };
    // Fires on a dropped connection *and* while the browser is retrying. Reported rather than
    // acted on: EventSource's own backoff is the retry.
    const onError = () => setStatus('reconnecting');

    source.addEventListener('ready', onReady);
    source.addEventListener('levels', onLevels as EventListener);
    source.addEventListener('error', onError);

    return () => {
      source.removeEventListener('ready', onReady);
      source.removeEventListener('levels', onLevels as EventListener);
      source.removeEventListener('error', onError);
      source.close();
      setStatus('connecting');
    };
  }, [underlying, queryClient]);

  return { status, lastEvent };
}
