"""In-process pub/sub for "a new snapshot's levels were stored" (TASKS.md T19).

## Why this is deliberately tiny

One process, one user, at most a couple of browser tabs. The entire mechanism is a dict of
`asyncio.Queue`s keyed by underlying, and that is the right size: Redis, a message broker or a
Postgres `LISTEN/NOTIFY` channel would each add an operational dependency, a failure mode and a
deployment step to deliver a few dozen events a day to one person. If this app ever becomes
multi-process the decision gets revisited against real requirements; today the alternative
would be architecture for an audience of one.

The consequence to be honest about: **events do not survive a restart and do not cross
processes.** A browser tab connected through a backend restart sees its `EventSource`
reconnect (the browser does that on its own) and simply misses whatever was published while the
socket was down. That is acceptable here because the event carries no payload the client cannot
re-fetch -- it is a nudge to invalidate a query, not a datum. Anything that ever needs delivery
guarantees must not be built on this module.

## Threading

`publish` is called from `app.modules.gex.jobs.capture.capture_snapshot`, which is `async` and runs on the
event loop -- the DB and Parquet work around it is pushed to worker threads via
`asyncio.to_thread`, but the publish itself happens back on the loop. So no locking is needed
and none is used. A caller from another thread would need `loop.call_soon_threadsafe`; there is
no such caller, and `publish`'s docstring says so rather than leaving it to be discovered.

## Backpressure

Each subscriber queue is bounded. A client that stops reading (a laptop asleep with the tab
open, a paused debugger) must not grow an unbounded queue behind it, so the **oldest** event is
dropped to make room rather than the newest. For a "something changed, re-fetch" signal the
newest event strictly dominates: a client that receives only the latest of five dropped events
does exactly the right thing, while one that receives the oldest re-fetches stale data.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

__all__ = ["LevelsBroker", "broker"]

logger = logging.getLogger("app.modules.gex.events")

#: Per-subscriber queue depth. Small on purpose -- see the module docstring's backpressure
#: note. Captures arrive at most once per symbol per 15 minutes, so a client that is more than
#: a handful behind is not "briefly busy", it is gone.
_QUEUE_MAXSIZE = 8


class LevelsBroker:
    """Fan-out of level-update events to the clients currently watching one underlying.

    Not a singleton by construction -- the module-level :data:`broker` is the app's instance,
    but tests construct their own rather than reaching into shared state.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, underlying: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a new subscriber for `underlying` and return its queue.

        The caller **must** pass the same queue back to :meth:`unsubscribe` when it is done,
        including on disconnect and on error -- otherwise the queue is retained forever and
        every subsequent publish writes into nothing. The SSE route does this in a `finally`.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers[underlying].add(queue)
        return queue

    def unsubscribe(self, underlying: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber. Idempotent, and safe for a queue that was never registered --
        a disconnect path that runs twice is not worth an exception."""
        subscribers = self._subscribers.get(underlying)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._subscribers[underlying]

    def subscriber_count(self, underlying: str) -> int:
        """How many clients are currently watching `underlying`. For tests and diagnostics."""
        return len(self._subscribers.get(underlying, ()))

    def publish(self, underlying: str, event: dict[str, Any]) -> int:
        """Deliver `event` to every current subscriber of `underlying`.

        Must be called from the event loop thread (see the module docstring). Never raises and
        never blocks: a full queue has its oldest event discarded to make room, which is the
        correct trade for a re-fetch nudge.

        Returns:
            How many subscribers it was delivered to. Zero is the ordinary case -- nobody has
            the app open -- and is not an error.
        """
        subscribers = self._subscribers.get(underlying)
        if not subscribers:
            return 0

        for queue in tuple(subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - drained concurrently
                    pass
                logger.debug(
                    "events: dropped the oldest queued event for a slow %s subscriber",
                    underlying,
                )
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - refilled concurrently
                pass
        return len(subscribers)


#: The application's broker. Imported by `app.modules.gex.jobs.capture` (publisher) and `app.modules.gex.api.stream`
#: (subscriber); nothing else should touch it.
broker = LevelsBroker()
