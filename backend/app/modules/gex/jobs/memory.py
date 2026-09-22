"""Return freed memory to the kernel between scheduled jobs (T88).

The capture worker's heap rose 125 -> 626 MB over four hours and never fell once, across
roughly sixteen capture cycles (`plans/capture-memory/README.md`). Freeing *is* happening --
Python's refcounting reclaims each chain as soon as the capture returns -- but nothing gives
the pages back, because two allocators sit between the interpreter and the kernel and both
keep what they have:

* **glibc** returns a free to a per-thread arena, not to the OS. The capture worker runs 15
  threads in steady state, so that is 15 places for a 63,000-object chain to be retained.
  `malloc_trim(0)` walks every arena and releases the whole free pages it finds.
* **pyarrow** allocates the Parquet read/write buffers from its own pool and holds the blocks
  for reuse. `default_memory_pool().release_unused()` hands them back.

Both calls are best-effort by nature and this module treats them that way. `malloc_trim` is a
GNU extension: it does not exist on musl, and there is no `libc.so.6` at all on the Windows
dev host where this code is run by hand constantly. Both resolutions are therefore paid once
at import, behind their own guard, and a host that cannot do one still does the other.

**This module is deliberately not in `gex/`.** Invariant 1 makes `gex/engine.py` and
`greeks.py` pure -- no HTTP, DB, filesystem or logging -- and a `ctypes` call into libc is
emphatically not that. The memory being trimmed is mostly engine memory, which is a reason to
call this *after* the engine has run, not a reason to put it inside.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import time
from dataclasses import dataclass

__all__ = ["MALLOC_TRIM_AVAILABLE", "ReleaseResult", "read_rss", "release_allocator"]

logger = logging.getLogger("app.modules.gex.jobs.memory")


def _resolve_malloc_trim():
    """The libc `malloc_trim`, or `None` where there is not one.

    Resolved once, at import, because the failure is a property of the platform and not of the
    call: paying the exception on every job would be the same answer at a cost. `ctypes.util.
    find_library` first so musl and unusual sonames get a chance, then the glibc name outright
    -- `find_library` shells out to `gcc`/`ldconfig` and returns `None` on a slim image that
    has neither, while `libc.so.6` is right there.
    """
    for name in (ctypes.util.find_library("c"), "libc.so.6"):
        if not name:
            continue
        try:
            libc = ctypes.CDLL(name, use_errno=True)
            trim = libc.malloc_trim
        except (OSError, AttributeError):
            continue
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        return trim
    return None


_MALLOC_TRIM = _resolve_malloc_trim()

#: Whether this host can trim glibc's arenas at all. False on Windows and on musl; read by the
#: tests, which must pass on the dev host precisely *because* it is False there.
MALLOC_TRIM_AVAILABLE: bool = _MALLOC_TRIM is not None


@dataclass(frozen=True)
class ReleaseResult:
    """What one release actually did.

    `freed_bytes` is `rss_before - rss_after`, so a positive number is memory handed back. It
    is `None` -- not `0` -- where RSS could not be read, following the same discipline
    invariant 3 applies to open interest: "could not measure" and "measured nothing" are
    different facts, and a release that reports `0` on Windows would read as a failure of the
    trim rather than an absence of the instrument.
    """

    rss_before: int | None
    rss_after: int | None
    arrow_released: bool
    trimmed: bool
    #: Wall time for both calls. Logged because this runs on the scheduler's event loop:
    #: `malloc_trim` is normally sub-millisecond, but on a large fragmented heap it can take
    #: longer, and the fix (move it into `asyncio.to_thread`) should be triggered by a
    #: measurement rather than by a worry. Tens of milliseconds is the threshold to act on.
    duration_ms: float

    @property
    def freed_bytes(self) -> int | None:
        if self.rss_before is None or self.rss_after is None:
            return None
        return self.rss_before - self.rss_after


def read_rss() -> int | None:
    """Resident set size of this process in bytes, or `None` where it cannot be read.

    `/proc/self/statm` rather than `psutil`: the figure is two fields of one line, this
    package has no `psutil` dependency, and adding one for a log line would be the largest
    thing in this task. The file does not exist on the Windows dev host, hence `None`.
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return pages * os.sysconf("SC_PAGE_SIZE")


def release_allocator() -> ReleaseResult:
    """Ask Arrow and then glibc to hand back what has already been freed.

    Arrow first, deliberately: `release_unused()` frees its blocks *through* malloc, so
    trimming before it would leave exactly those pages behind for the next cycle to find.

    Each step is guarded on its own. A host with no `malloc_trim` still gets the Arrow
    release, and an Arrow that raises does not cost the trim. Never raises -- it is called
    from a scheduler listener, where an exception would be logged by APScheduler and
    understood by nobody.
    """
    started = time.monotonic()
    rss_before = read_rss()

    arrow_released = False
    try:
        import pyarrow

        pyarrow.default_memory_pool().release_unused()
        arrow_released = True
    except Exception:  # pragma: no cover - pyarrow is a hard dependency; guard is for safety
        logger.debug("release_allocator: arrow pool release failed", exc_info=True)

    trimmed = False
    if _MALLOC_TRIM is not None:
        try:
            _MALLOC_TRIM(0)
            trimmed = True
        except Exception:  # pragma: no cover - a ctypes call that fails is worth surviving
            logger.debug("release_allocator: malloc_trim failed", exc_info=True)

    return ReleaseResult(
        rss_before=rss_before,
        rss_after=read_rss(),
        arrow_released=arrow_released,
        trimmed=trimmed,
        duration_ms=(time.monotonic() - started) * 1000.0,
    )
