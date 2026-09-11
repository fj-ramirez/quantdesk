"""`GET /api/stream/{underlying}` -- Server-Sent Events for level updates (TASKS.md T19).

The point of this route is to let the dashboard stop polling. Captures happen at most once per
symbol per 15 minutes (T18), so a UI that polls to notice them either polls far too often or
shows data that is minutes staler than what the server has. One long-lived connection per open
tab replaces all of it.

## The event

    event: levels
    data: {"underlying": "SPX", "snapshot_id": 91, "captured_at": "...",
           "effective_at": "...", "is_eod": false, "spot": 7671.24}

Deliberately a **nudge, not a payload**: enough for the client to decide whether it cares and
to render a freshness stamp, not enough for it to skip re-fetching. Sending whole level sets
down this channel would duplicate `GET /api/gex/{underlying}/latest`'s contract in a second
place, and the two would drift.

`effective_at` is included and `captured_at` is not to be used for a staleness badge. T34: the
Cboe timestamp keeps advancing after the close while the quotes underneath are frozen, so a
badge computed from `captured_at` reads "updated seconds ago" all evening. Every other router
here already exposes the derived value under this same name; this one does not invent a second
freshness concept.

## Connection lifecycle

* On connect, a `ready` event fires immediately. Without it a client cannot distinguish
  "connected, nothing has happened yet" from "still connecting" for up to fifteen minutes.
* Every `_KEEPALIVE_SECONDS` of silence, an SSE comment line goes out. Idle connections are
  otherwise culled by proxies and by some browsers, and the reconnect that follows is invisible
  noise.
* The subscriber queue is released in a `finally`, so a client that vanishes mid-write (closed
  laptop, killed tab) does not leak a queue that every later publish writes into forever.

`EventSource` reconnects on its own, so there is no retry protocol here and no client-side
retry logic in the hook that consumes it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.events import broker
from app.models.chain import Underlying

__all__ = ["router"]

logger = logging.getLogger("app.api.stream")

router = APIRouter(prefix="/stream", tags=["stream"])

#: Silence before a keep-alive comment. Comfortably inside the 30-60 s idle timeout common to
#: reverse proxies, and cheap: one line per connection per interval.
_KEEPALIVE_SECONDS = 15.0


def _sse(event: str, data: dict) -> str:
    """One SSE frame. The trailing blank line is what terminates a frame -- omitting it is the
    classic way to build a stream that a browser accepts and then never dispatches."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _event_stream(underlying: str, request: Request) -> AsyncIterator[str]:
    queue = broker.subscribe(underlying)
    logger.info(
        json.dumps(
            {
                "event": "stream_subscribed",
                "underlying": underlying,
                "subscribers": broker.subscriber_count(underlying),
            }
        )
    )
    try:
        yield _sse("ready", {"underlying": underlying})
        while True:
            # `is_disconnected` catches the client that goes away while nothing is being
            # published -- which, at one capture per 15 minutes, is almost always. Without this
            # check the generator would sit in `wait_for` until the next capture before
            # noticing, holding its queue the whole time.
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield _sse("levels", event)
    finally:
        broker.unsubscribe(underlying, queue)
        logger.info(
            json.dumps(
                {
                    "event": "stream_unsubscribed",
                    "underlying": underlying,
                    "subscribers": broker.subscriber_count(underlying),
                }
            )
        )


@router.get("/{underlying}")
async def stream_levels(underlying: str, request: Request) -> StreamingResponse:
    """Subscribe to level updates for one underlying.

    Validates the symbol up front rather than streaming happily for a typo: an `EventSource`
    that connects successfully and then never fires is one of the harder client bugs to see,
    and `GET /api/stream/SPXX` should say so in the ordinary way.
    """
    try:
        symbol = Underlying(underlying.upper()).value
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"unknown underlying {underlying!r}",
        ) from None

    return StreamingResponse(
        _event_stream(symbol, request),
        media_type="text/event-stream",
        headers={
            # Proxies and some browsers buffer or transform a streamed response by default,
            # which turns a live channel into one that delivers everything at disconnect.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
