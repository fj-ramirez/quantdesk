/**
 * T19 — `useLiveLevels`.
 *
 * jsdom has no `EventSource`, so one is installed on `globalThis` for these tests. That is also
 * what makes the hook's `typeof EventSource === 'undefined'` branch worth having: without it,
 * every existing component test that renders `ContextBar` would throw.
 */
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { useLiveLevels } from './useLiveLevels';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  private listeners = new Map<string, Set<EventListener>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(type: string, listener: EventListener) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    this.closed = true;
  }

  /** Drive the hook from the test, the way the server would. */
  emit(type: string, data?: unknown) {
    const event = data === undefined ? new Event(type) : new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  /** Emit a raw, unparseable frame -- the malformed-payload path. */
  emitRaw(type: string, data: string) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data }));
    }
  }

  static latest() {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }
}

const LEVELS_EVENT = {
  underlying: 'SPX',
  snapshot_id: 91,
  captured_at: '2026-09-11T17:15:31Z',
  effective_at: '2026-09-11T17:15:31Z',
  is_eod: false,
  spot: 7671.24,
  skipped_duplicate: false,
};

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe('useLiveLevels', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    queryClient.clear();
  });

  it('opens a stream for the requested underlying', () => {
    renderHook(() => useLiveLevels('SPX'), { wrapper: wrapper(queryClient) });
    expect(FakeEventSource.latest().url).toContain('/api/gex/stream/SPX');
  });

  it('starts as connecting and goes live on the ready event', async () => {
    const { result } = renderHook(() => useLiveLevels('SPX'), { wrapper: wrapper(queryClient) });
    expect(result.current.status).toBe('connecting');

    act(() => FakeEventSource.latest().emit('ready'));

    await waitFor(() => expect(result.current.status).toBe('live'));
  });

  it('invalidates every query mentioning that underlying on a levels event', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    renderHook(() => useLiveLevels('SPX'), { wrapper: wrapper(queryClient) });

    act(() => FakeEventSource.latest().emit('levels', LEVELS_EVENT));

    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    // The predicate is the whole contract here, so it is exercised rather than merely asserted
    // to exist. Cast through `unknown`: TanStack types it against a full `Query` instance, and
    // constructing one to check a key-membership test would be all ceremony and no coverage.
    const predicate = invalidate.mock.calls[0][0]?.predicate as unknown as (q: {
      queryKey: unknown[];
    }) => boolean;
    expect(predicate({ queryKey: ['gex', 'SPX', 'ALL', 'latest'] })).toBe(true);
    expect(predicate({ queryKey: ['gex', 'QQQ', 'ALL', 'latest'] })).toBe(false);
  });

  it('exposes the last event so the UI can show what arrived', async () => {
    const { result } = renderHook(() => useLiveLevels('SPX'), { wrapper: wrapper(queryClient) });

    act(() => FakeEventSource.latest().emit('levels', LEVELS_EVENT));

    await waitFor(() => expect(result.current.lastEvent?.snapshot_id).toBe(91));
    expect(result.current.lastEvent?.effective_at).toBe('2026-09-11T17:15:31Z');
  });

  it('still refreshes when the payload cannot be parsed', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const { result } = renderHook(() => useLiveLevels('SPX'), { wrapper: wrapper(queryClient) });

    act(() => FakeEventSource.latest().emitRaw('levels', 'not json at all'));

    // Something changed upstream either way, and the re-fetch is what actually matters.
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(result.current.lastEvent).toBeNull();
  });

  it('reports reconnecting on error rather than hiding a dead stream', async () => {
    const { result } = renderHook(() => useLiveLevels('SPX'), { wrapper: wrapper(queryClient) });

    act(() => FakeEventSource.latest().emit('ready'));
    await waitFor(() => expect(result.current.status).toBe('live'));
    act(() => FakeEventSource.latest().emit('error'));

    await waitFor(() => expect(result.current.status).toBe('reconnecting'));
  });

  it('closes the stream on unmount', () => {
    const { unmount } = renderHook(() => useLiveLevels('SPX'), { wrapper: wrapper(queryClient) });
    const source = FakeEventSource.latest();

    unmount();

    expect(source.closed).toBe(true);
  });

  it('opens a new stream and closes the old one when the symbol changes', () => {
    const { rerender } = renderHook(({ symbol }) => useLiveLevels(symbol), {
      wrapper: wrapper(queryClient),
      initialProps: { symbol: 'SPX' as const },
    });
    const first = FakeEventSource.latest();

    rerender({ symbol: 'QQQ' as unknown as 'SPX' });

    expect(first.closed).toBe(true);
    expect(FakeEventSource.latest().url).toContain('/api/gex/stream/QQQ');
  });

  it('reports unsupported, without throwing, where EventSource does not exist', () => {
    vi.stubGlobal('EventSource', undefined);
    const { result } = renderHook(() => useLiveLevels('SPX'), { wrapper: wrapper(queryClient) });
    expect(result.current.status).toBe('unsupported');
  });
});
