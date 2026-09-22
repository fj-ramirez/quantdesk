"""Tests for `app.modules.gex.jobs.memory` and its scheduler listener (T88).

**These tests run on the Windows dev host, where `malloc_trim` does not exist and
`/proc/self/statm` is not a file.** That is not a limitation of the suite -- it is the
scenario worth pinning. The no-op fallback is the path a developer exercises every day, and a
`ctypes` resolution that raised at import would take the whole capture worker down with it.
"""

from __future__ import annotations

import json
import logging

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from app.modules.gex.jobs.memory import (
    MALLOC_TRIM_AVAILABLE,
    ReleaseResult,
    read_rss,
    release_allocator,
)
from app.modules.gex.jobs.scheduler import build_scheduler, release_memory_listener


def test_release_allocator_never_raises_on_any_platform():
    """The fallback, asserted as behaviour rather than as a platform check: whatever this host
    is, the call returns a result and the Arrow half happened."""
    result = release_allocator()

    assert isinstance(result, ReleaseResult)
    assert result.arrow_released is True
    assert result.trimmed is MALLOC_TRIM_AVAILABLE
    # It runs on the scheduler's event loop, so it has to be quick even in the worst case the
    # dev host can produce.
    assert result.duration_ms < 1000.0


def test_freed_bytes_is_none_when_rss_cannot_be_read():
    """`None` means "not measured" and `0` means "measured nothing", and a release on a host
    with no `/proc` must say the first. Same discipline as invariant 3."""
    unmeasured = ReleaseResult(
        rss_before=None, rss_after=None, arrow_released=True, trimmed=False, duration_ms=0.1
    )
    assert unmeasured.freed_bytes is None

    measured = ReleaseResult(
        rss_before=500, rss_after=200, arrow_released=True, trimmed=True, duration_ms=0.1
    )
    assert measured.freed_bytes == 300


def test_read_rss_is_none_or_a_plausible_size():
    """Linux gives a real figure; Windows gives `None`. Never a zero or a page count."""
    rss = read_rss()
    assert rss is None or rss > 1_000_000


def test_build_scheduler_registers_the_release_listener():
    scheduler = build_scheduler()

    # APScheduler keeps listeners as (callback, mask) pairs on a private attribute; there is
    # no public accessor, and reading it is still better than starting a scheduler and firing
    # a real job to find out whether the hook is attached.
    registered = [(callback, mask) for callback, mask in scheduler._listeners]
    assert (release_memory_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR) in registered


def test_a_job_event_triggers_exactly_one_release(monkeypatch, caplog):
    """The listener is what APScheduler will call, so drive it the way APScheduler does."""
    calls: list[int] = []

    def _spy():
        calls.append(1)
        return ReleaseResult(
            rss_before=200_000_000,
            rss_after=150_000_000,
            arrow_released=True,
            trimmed=True,
            duration_ms=1.5,
        )

    monkeypatch.setattr("app.modules.gex.jobs.scheduler.release_allocator", _spy)

    class _Event:
        job_id = "capture_eod"

    with caplog.at_level(logging.INFO, logger="app.modules.gex.jobs.scheduler"):
        release_memory_listener(_Event())

    assert calls == [1]

    payload = json.loads(
        next(r.message for r in caplog.records if "release_allocator" in r.message)
    )
    assert payload["job_id"] == "capture_eod"
    assert payload["rss_delta_bytes"] == 50_000_000
    assert payload["malloc_trimmed"] is True


def test_the_listener_survives_a_release_that_cannot_measure(monkeypatch, caplog):
    """A `None` delta must still log, and must log `null` rather than `0` -- the Windows dev
    host and any container without `/proc` mounted take this path."""
    monkeypatch.setattr(
        "app.modules.gex.jobs.scheduler.release_allocator",
        lambda: ReleaseResult(
            rss_before=None, rss_after=None, arrow_released=True, trimmed=False, duration_ms=0.4
        ),
    )

    class _Event:
        job_id = "bars_update"

    with caplog.at_level(logging.INFO, logger="app.modules.gex.jobs.scheduler"):
        release_memory_listener(_Event())

    payload = json.loads(
        next(r.message for r in caplog.records if "release_allocator" in r.message)
    )
    assert payload["rss_delta_bytes"] is None
    assert payload["malloc_trimmed"] is False
