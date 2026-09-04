"""Core capture logic shared by the EOD scheduler job (T05) and the manual capture endpoint.

One call here is: fetch a chain from the configured provider -> write it to Parquet -> index
it in Postgres -> (T09 seam, see below) -> structured JSON log line. Everything funnels
through :class:`CaptureResult` rather than an exception, because the top priority for this
job (see the T05 brief) is that one symbol's failure must never stop the others or crash the
scheduler -- a caller that wants exceptions (the API route) chooses to raise from the result
itself, but this module never raises for an ordinary provider/storage failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.gex.store import compute_and_store
from app.models.chain import ChainSnapshot
from app.models.db import Snapshot, get_engine, get_sessionmaker
from app.providers import get_provider
from app.providers.base import OptionChainProvider, ProviderError
from app.storage.parquet import write_snapshot
from app.storage.repository import SnapshotRepository

__all__ = ["CaptureResult", "capture_all_symbols", "capture_snapshot", "get_session_factory"]

logger = logging.getLogger("app.jobs.capture")

# One engine (and its connection pool) for the process's lifetime, shared by every capture
# call and by the GET /api/snapshots route -- see app/models/db.py's own docstring for why
# `get_engine()` is deliberately not called at import time. Creating a fresh engine per
# capture would open (and never close) a new connection pool every time the EOD job runs.
_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    """Cached, lazily-created sessionmaker bound to `settings.DATABASE_URL`.

    Tests should not use this -- pass an explicit `session_factory` (built on a temp SQLite
    URL, per the pattern in `tests/test_snapshot_repository.py`) to `capture_snapshot`
    instead, so the test suite never touches the real database this points at.
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = get_sessionmaker(get_engine())
    return _session_factory


@dataclass(frozen=True)
class CaptureResult:
    """Outcome of one `capture_snapshot` call -- also the shape of its JSON log line."""

    underlying: str
    ok: bool
    contract_count: int | None = None
    spot: float | None = None
    duration_seconds: float = 0.0
    snapshot_id: int | None = None
    parquet_path: str | None = None
    skipped_duplicate: bool = False
    error: str | None = None


def _log_result(result: CaptureResult) -> None:
    """Emit one structured JSON log line per capture (T05: "structured logging of every
    capture: symbol, contract count, spot, duration, and the error if any"). Logged as a
    literal JSON string rather than via `extra=` so the shape is guaranteed regardless of
    whatever logging/formatter configuration the app ends up with -- a capture failure being
    unreadable because of an unrelated logging-config change is exactly the kind of silent
    gap this task exists to prevent.
    """
    level = logging.INFO if result.ok else logging.ERROR
    logger.log(level, json.dumps({"event": "capture", **asdict(result)}, default=str))


async def capture_snapshot(
    underlying: str,
    *,
    is_eod: bool,
    provider: OptionChainProvider | None = None,
    session_factory: sessionmaker[Session] | None = None,
    data_dir: str | Path | None = None,
) -> CaptureResult:
    """Fetch, persist, and index one underlying's chain. Never raises.

    Args:
        underlying: Canonical symbol (``'SPX'``, ``'SPY'``, ``'QQQ'``).
        is_eod: Written through to `Snapshot.is_eod`.
        provider: Reuse an existing provider (e.g. so `capture_all_symbols` shares one
            provider's HTTP connection across all three symbols) instead of constructing and
            tearing down a fresh one per call. Ownership follows CboeProvider/MarketDataProvider's
            own convention: a provider passed in here is left open for the caller to close;
            one constructed internally is closed before returning.
        session_factory: Defaults to :func:`get_session_factory` (real Postgres). Tests pass
            a SQLite-backed factory here so the whole capture path runs fully offline.
        data_dir: Forwarded to `write_snapshot`; defaults to `settings.DATA_DIR`. Tests pass
            a temp directory here for the same offline-isolation reason as `session_factory`.

    Returns:
        A `CaptureResult`. `ok=False` covers both a provider failure (network, upstream) and
        a storage failure (disk, DB) -- either way the caller decides what to do; this
        function has already logged the structured line either way.
    """
    owns_provider = provider is None
    active_provider = provider if provider is not None else get_provider()
    start = time.monotonic()
    try:
        try:
            snapshot = await active_provider.fetch_chain(underlying)
        except ProviderError as exc:
            result = CaptureResult(
                underlying=underlying,
                ok=False,
                duration_seconds=time.monotonic() - start,
                error=str(exc),
            )
            _log_result(result)
            return result
    finally:
        if owns_provider:
            close = getattr(active_provider, "close", None)
            if close is not None:
                await close()

    # Resolved once and reused for the T09 seam below too, so a real capture doesn't spin up
    # a second, separate engine/connection-pool to the same DATABASE_URL just to compute
    # levels a few lines later.
    effective_session_factory = session_factory or get_session_factory()
    try:
        parquet_path, row, skipped_duplicate = await asyncio.to_thread(
            _persist_sync, snapshot, is_eod, effective_session_factory, data_dir
        )
    except Exception as exc:  # noqa: BLE001 - a storage failure must not crash the scheduler
        result = CaptureResult(
            underlying=underlying,
            ok=False,
            contract_count=len(snapshot),
            spot=snapshot.spot,
            duration_seconds=time.monotonic() - start,
            error=f"{type(exc).__name__}: {exc}",
        )
        _log_result(result)
        return result

    result = CaptureResult(
        underlying=underlying,
        ok=True,
        contract_count=len(snapshot),
        spot=snapshot.spot,
        duration_seconds=time.monotonic() - start,
        snapshot_id=row.id,
        parquet_path=parquet_path,
        skipped_duplicate=skipped_duplicate,
    )
    _log_result(result)

    # --- T09 seam --------------------------------------------------------------------------
    # The snapshot is durably indexed at this point (`row.id` known, Parquet already on disk),
    # so GEX levels can be computed and stored for filters ALL, ZERO_DTE, EX_ZERO_DTE
    # (`app.gex.store.DEFAULT_FILTERS`). This runs strictly after the capture is already a
    # success and is wrapped in its own try/except that only logs: a bug here must never flip
    # `result.ok` back to False and must never raise out of this function, because the raw
    # chain on disk -- not the derived levels -- is the one artifact in this app that cannot
    # be recomputed after the fact (the free Cboe source keeps no history). A failure here is
    # self-healing: `uv run python -m app.gex.backfill` picks up any snapshot left without
    # levels on a later run.
    try:
        await asyncio.to_thread(
            compute_and_store,
            row.id,
            session_factory=effective_session_factory,
            data_dir=data_dir,
        )
    except Exception:  # see the comment above: must never fail the capture
        logger.exception(
            "capture: %s snapshot_id=%d persisted successfully but GEX level computation "
            "failed; it will be picked up by `uv run python -m app.gex.backfill`",
            underlying,
            row.id,
        )
    # -----------------------------------------------------------------------------------------

    return result


def _persist_sync(
    snapshot: ChainSnapshot,
    is_eod: bool,
    session_factory: sessionmaker[Session],
    data_dir: str | Path | None,
) -> tuple[str, Snapshot, bool]:
    """Write Parquet + index row. Runs off the event loop via `asyncio.to_thread`.

    `SnapshotRepository` is synchronous and commits inside `add()` (see its own module
    docstring) -- calling it directly from an `async def` would block the whole event loop
    for a DB round trip, stalling the *other* symbols' captures that an `AsyncIOScheduler`
    job would otherwise be free to interleave, plus any concurrent API request. Pushing this
    entire write to a worker thread (rather than, say, only the DB half) also keeps the
    Parquet file write -- itself blocking disk I/O -- off the loop.

    Duplicate-capture decision (see T05 brief): Cboe's `timestamp` advances continuously
    (verified 2026-09-04: three separate calls returned strictly increasing timestamps), so
    an exact `(underlying, captured_at)` match found here means a genuine retry or a
    double-fired job, not normal quantization. There is no DB uniqueness constraint on that
    pair (T04 shipped without one, and adding one is a schema change out of scope for T05 --
    see the task's file restrictions). Rather than either (a) trusting the constraint to not
    exist and overwriting the Parquet file in place -- silently repointing the existing index
    row's file out from under it if the payload differs even slightly -- or (b) adding a
    migration here, this function checks first and **skips** the write+insert when the exact
    instant is already indexed, returning the existing row instead. This is an
    application-level check, not a hard constraint, so a genuine race (two captures for the
    same underlying resolving to the identical vendor timestamp, in flight at once) could
    still both pass the check before either commits -- accepted here because this is a
    single-user app whose only two triggers (the daily cron and a manual API call) are never
    both in flight for the same symbol in practice, and the failure mode of a lost race is
    merely a redundant Parquet file, not corrupted data.
    """
    with session_factory() as session:
        existing = session.execute(
            select(Snapshot).where(
                Snapshot.underlying == snapshot.underlying.value,
                Snapshot.captured_at == snapshot.captured_at,
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.warning(
                "capture: %s at %s already indexed (snapshot id=%d) -- skipping duplicate "
                "write, not overwriting %s",
                snapshot.underlying.value,
                snapshot.captured_at.isoformat(),
                existing.id,
                existing.parquet_path,
            )
            return existing.parquet_path, existing, True

        path = write_snapshot(snapshot, data_dir=data_dir)
        repo = SnapshotRepository(session)
        row = repo.add(snapshot, path, is_eod=is_eod)
        return Path(path).as_posix(), row, False


async def capture_all_symbols(
    symbols: Sequence[str],
    *,
    is_eod: bool,
    session_factory: sessionmaker[Session] | None = None,
    data_dir: str | Path | None = None,
) -> list[CaptureResult]:
    """Capture every symbol in `symbols`, sequentially, sharing one provider connection.

    Sequential rather than concurrent (`asyncio.gather`) on purpose: PLAN.md's guidance for
    the free Cboe source is "no more than once per symbol per 15 minutes" -- there is no
    upside to firing three requests at the same instant, and sequential execution keeps each
    symbol's duration in the logs meaningful (a `gather` would attribute shared contention to
    whichever call happened to finish last). A failure on one symbol is already caught and
    logged inside `capture_snapshot` and does not stop the loop from reaching the rest.
    """
    provider = get_provider()
    results: list[CaptureResult] = []
    try:
        for underlying in symbols:
            results.append(
                await capture_snapshot(
                    underlying,
                    is_eod=is_eod,
                    provider=provider,
                    session_factory=session_factory,
                    data_dir=data_dir,
                )
            )
    finally:
        close = getattr(provider, "close", None)
        if close is not None:
            await close()
    return results
