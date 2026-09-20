"""Tests for T19: the in-process broker (`app/modules/gex/events.py`) and the SSE route
(`app/modules/gex/api/stream.py`).

Both are new surfaces with no prior pattern in this codebase, so what is pinned here is the
behaviour a future change could plausibly break without any test failing otherwise: queue
release on disconnect, oldest-first dropping under backpressure, frame framing, and the fact
that a client is told it is connected rather than left guessing.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.gex.events import LevelsBroker

READY_FRAME = 'event: ready\ndata: {"underlying": "SPX"}\n\n'
KEEPALIVE_FRAME = ": keepalive\n\n"


# --- the broker -------------------------------------------------------------------------------


async def test_publish_reaches_every_subscriber_of_that_underlying():
    broker = LevelsBroker()
    first = broker.subscribe("SPX")
    second = broker.subscribe("SPX")

    delivered = broker.publish("SPX", {"snapshot_id": 1})

    assert delivered == 2
    assert first.get_nowait() == {"snapshot_id": 1}
    assert second.get_nowait() == {"snapshot_id": 1}


async def test_publish_does_not_cross_underlyings():
    broker = LevelsBroker()
    spx = broker.subscribe("SPX")
    spy = broker.subscribe("SPY")

    broker.publish("SPX", {"snapshot_id": 1})

    assert spx.qsize() == 1
    assert spy.qsize() == 0


async def test_publish_with_no_subscribers_is_not_an_error():
    """The ordinary case -- nobody has the app open."""
    assert LevelsBroker().publish("SPX", {"snapshot_id": 1}) == 0


async def test_unsubscribe_stops_delivery_and_releases_the_queue():
    broker = LevelsBroker()
    queue = broker.subscribe("SPX")

    broker.unsubscribe("SPX", queue)

    assert broker.subscriber_count("SPX") == 0
    assert broker.publish("SPX", {"snapshot_id": 1}) == 0
    assert queue.qsize() == 0


async def test_unsubscribe_is_idempotent():
    """A disconnect path that runs twice must not raise -- the SSE route's `finally` can be
    reached after an error that already cleaned up."""
    broker = LevelsBroker()
    queue = broker.subscribe("SPX")
    broker.unsubscribe("SPX", queue)
    broker.unsubscribe("SPX", queue)  # must not raise
    broker.unsubscribe("QQQ", queue)  # never registered here at all


async def test_a_slow_subscriber_drops_the_oldest_event_not_the_newest():
    """For a "something changed, re-fetch" signal the newest event strictly dominates: a client
    that receives only the latest of several dropped events does the right thing, while one
    that receives the oldest re-fetches stale data."""
    from app.modules.gex.events import _QUEUE_MAXSIZE

    broker = LevelsBroker()
    queue = broker.subscribe("SPX")

    for n in range(_QUEUE_MAXSIZE + 3):
        broker.publish("SPX", {"snapshot_id": n})

    assert queue.qsize() == _QUEUE_MAXSIZE
    drained = [queue.get_nowait()["snapshot_id"] for _ in range(_QUEUE_MAXSIZE)]
    assert drained[-1] == _QUEUE_MAXSIZE + 2  # the newest survived
    assert 0 not in drained  # the oldest did not


class _FakeRequest:
    """Minimal stand-in for `Request` -- `_event_stream` only calls `is_disconnected`.

    The generator is driven directly in the tests below rather than through `TestClient`, on
    purpose. The TestClient runs the app's event loop in a worker thread, so a `broker.publish`
    from the test thread would enqueue without waking the loop's pending `queue.get()` --
    `asyncio.Queue` is not thread-safe, and a test built that way passes or hangs depending on
    timing. Driving the generator on the test's own loop makes delivery deterministic.
    """

    def __init__(self, disconnect_after: int = 99) -> None:
        self._checks = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self._disconnect_after


# --- the route --------------------------------------------------------------------------------


def test_unknown_underlying_is_a_404_not_a_silent_stream():
    """An EventSource that connects and then never fires is a hard client bug to see, so a typo
    must fail in the ordinary way instead."""
    with TestClient(app) as client:
        response = client.get("/api/gex/stream/SPXX")
    assert response.status_code == 404


async def test_stream_response_headers_are_set_for_a_live_channel():
    """Proxies and some browsers buffer a streamed response by default, which turns a live
    channel into one that delivers everything at disconnect.

    The route is called directly and only its headers inspected. Consuming the body through
    `TestClient.stream` would hang: the response is an endless generator, and the client's
    context exit waits for a body that never ends. That is correct behaviour for an SSE channel
    and a bad shape for a test -- the streaming behaviour itself is covered by the generator
    tests below, which can stop whenever they like.
    """
    from app.modules.gex.api.stream import stream_levels

    response = await stream_levels("spx", _FakeRequest())  # lowercase: also checks normalizing

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


async def test_stream_announces_readiness_immediately(monkeypatch):
    """Without this a client cannot tell "connected, nothing published yet" from "still
    connecting" for up to fifteen minutes."""
    import app.modules.gex.api.stream as stream_module

    monkeypatch.setattr(stream_module, "broker", LevelsBroker())
    stream = stream_module._event_stream("SPX", _FakeRequest())
    assert await anext(stream) == READY_FRAME
    await stream.aclose()


async def test_stream_delivers_a_published_event(monkeypatch):
    import app.modules.gex.api.stream as stream_module

    broker = LevelsBroker()
    monkeypatch.setattr(stream_module, "broker", broker)

    stream = stream_module._event_stream("SPX", _FakeRequest())
    await anext(stream)  # ready

    broker.publish(
        "SPX",
        {
            "underlying": "SPX",
            "snapshot_id": 91,
            "captured_at": dt.datetime(2026, 9, 11, 17, 15, tzinfo=dt.UTC),
            "is_eod": False,
        },
    )
    frame = await anext(stream)

    assert frame.startswith("event: levels\ndata: ")
    assert '"snapshot_id": 91' in frame
    assert frame.endswith("\n\n")  # the blank line that terminates an SSE frame
    await stream.aclose()


async def test_stream_emits_a_keepalive_when_nothing_is_published(monkeypatch):
    """Idle connections are culled by proxies and some browsers; the reconnect that follows is
    invisible noise."""
    import app.modules.gex.api.stream as stream_module

    monkeypatch.setattr(stream_module, "broker", LevelsBroker())
    monkeypatch.setattr(stream_module, "_KEEPALIVE_SECONDS", 0.01)

    stream = stream_module._event_stream("SPX", _FakeRequest())
    await anext(stream)  # ready
    assert await anext(stream) == KEEPALIVE_FRAME
    await stream.aclose()


async def test_stream_releases_its_queue_on_disconnect(monkeypatch):
    """The leak this guards against is invisible: a retained queue is written to by every later
    publish and never read, forever."""
    import app.modules.gex.api.stream as stream_module

    broker = LevelsBroker()
    monkeypatch.setattr(stream_module, "broker", broker)
    monkeypatch.setattr(stream_module, "_KEEPALIVE_SECONDS", 0.01)

    stream = stream_module._event_stream("SPX", _FakeRequest(disconnect_after=0))
    await anext(stream)  # ready; the disconnect check has not run yet
    assert broker.subscriber_count("SPX") == 1

    with pytest.raises(StopAsyncIteration):
        await anext(stream)  # the disconnect check trips and the generator returns

    assert broker.subscriber_count("SPX") == 0


async def test_stream_notices_a_disconnect_without_waiting_for_a_publish(monkeypatch):
    """At one capture per fifteen minutes, almost every disconnect happens while nothing is
    being published. Without the explicit check the generator would sit in `wait_for` until the
    next capture before noticing, holding its queue the whole time."""
    import app.modules.gex.api.stream as stream_module

    broker = LevelsBroker()
    monkeypatch.setattr(stream_module, "broker", broker)
    # Long enough that this test would time out if the disconnect were only noticed after a
    # publish or a keep-alive timeout.
    monkeypatch.setattr(stream_module, "_KEEPALIVE_SECONDS", 300.0)

    stream = stream_module._event_stream("SPX", _FakeRequest(disconnect_after=0))
    await anext(stream)  # ready
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=2.0)
    assert broker.subscriber_count("SPX") == 0


# --- the publish from the capture path ---------------------------------------------------------


async def test_capture_publishes_after_levels_are_stored(tmp_path):
    """Order matters: a client woken by this event re-fetches immediately, so waking it before
    the level rows exist would serve it the previous snapshot's levels."""
    from app.core.db import get_engine, get_sessionmaker
    from app.modules.gex.events import broker as app_broker
    from app.modules.gex.jobs.capture import capture_snapshot
    from app.modules.gex.models.db import Base

    from .test_capture import StubProvider, make_snapshot

    engine = get_engine(f"sqlite:///{tmp_path / 'stream.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    queue = app_broker.subscribe("SPX")
    try:
        snapshot = make_snapshot()
        await capture_snapshot(
            "SPX",
            is_eod=True,
            provider=StubProvider(snapshot=snapshot),
            session_factory=factory,
            data_dir=tmp_path,
        )
        event = queue.get_nowait()
        assert event["underlying"] == "SPX"
        assert event["is_eod"] is True
        assert event["spot"] == snapshot.spot
        # T34: the staleness field is the derived one, never raw `captured_at`.
        assert "effective_at" in event
    finally:
        app_broker.unsubscribe("SPX", queue)
        engine.dispose()


async def test_capture_does_not_publish_when_level_computation_fails(tmp_path, monkeypatch):
    """A failed `compute_and_store` leaves no levels to fetch, so nudging a client to re-fetch
    would hand it the previous snapshot and call it fresh."""
    from app.core.db import get_engine, get_sessionmaker
    from app.modules.gex.events import broker as app_broker
    from app.modules.gex.jobs import capture as capture_module
    from app.modules.gex.models.db import Base

    from .test_capture import StubProvider, make_snapshot

    engine = get_engine(f"sqlite:///{tmp_path / 'stream_fail.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)

    def boom(*args, **kwargs):
        raise RuntimeError("levels exploded")

    monkeypatch.setattr(capture_module, "compute_and_store", boom)
    queue = app_broker.subscribe("SPX")
    try:
        result = await capture_module.capture_snapshot(
            "SPX",
            is_eod=True,
            provider=StubProvider(snapshot=make_snapshot()),
            session_factory=factory,
            data_dir=tmp_path,
        )
        # The capture itself still succeeded -- the raw chain is on disk, which is the part
        # that cannot be recomputed.
        assert result.ok is True
        with pytest.raises(asyncio.QueueEmpty):
            queue.get_nowait()
    finally:
        app_broker.unsubscribe("SPX", queue)
        engine.dispose()
