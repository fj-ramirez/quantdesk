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

from app.core.db import get_session_factory
from app.modules.gex.events import broker
from app.modules.gex.gex.store import compute_and_store
from app.modules.gex.jobs.calendar import effective_data_time
from app.modules.gex.models.chain import ChainSnapshot
from app.modules.gex.models.db import Snapshot
from app.modules.gex.providers import get_provider
from app.modules.gex.providers.base import OptionChainProvider, ProviderError
from app.modules.gex.storage.fingerprint import chain_fingerprint
from app.modules.gex.storage.parquet import write_snapshot
from app.modules.gex.storage.repository import SnapshotRepository

__all__ = ["CaptureResult", "capture_all_symbols", "capture_snapshot"]

logger = logging.getLogger("app.modules.gex.jobs.capture")

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
    duplicate_reason: str | None = None
    """Why the write was skipped, when `skipped_duplicate` is True (T71). `"captured_at"` is a
    re-fire of the same request -- the same vendor timestamp already indexed. `"content"` is
    the case intraday polling introduces: a *new* vendor timestamp over a chain byte-identical
    to the last one stored, i.e. the feed had not refreshed. The two are distinguished in the
    structured log because they mean different things operationally -- the first says a job
    fired twice, the second says the upstream is not moving."""
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
        except Exception as exc:
            # Providers are *supposed* to wrap every failure in a ProviderError, but a bug or
            # an unanticipated vendor payload can still leak something else (a KeyError from a
            # malformed contract, a JSONDecodeError from an HTML error page served with status
            # 200). Letting that escape would abort `capture_all_symbols` mid-loop, so the two
            # symbols after the failing one would never be attempted -- and on this project's
            # free, history-less source a symbol not attempted at 16:20 is gone forever. This
            # docstring's "never raises" contract is what the scheduler relies on, so it is
            # enforced here rather than merely asserted.
            logger.exception("capture: provider %r raised a non-ProviderError", underlying)
            result = CaptureResult(
                underlying=underlying,
                ok=False,
                duration_seconds=time.monotonic() - start,
                error=f"provider raised {type(exc).__name__}: {exc}",
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
        parquet_path, row, skipped_duplicate, duplicate_reason = await asyncio.to_thread(
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
        duplicate_reason=duplicate_reason,
    )
    _log_result(result)

    # --- T09 seam --------------------------------------------------------------------------
    # The snapshot is durably indexed at this point (`row.id` known, Parquet already on disk),
    # so GEX levels can be computed and stored for filters ALL, ZERO_DTE, EX_ZERO_DTE
    # (`app.modules.gex.gex.store.DEFAULT_FILTERS`). This runs strictly after the capture is already a
    # success and is wrapped in its own try/except that only logs: a bug here must never flip
    # `result.ok` back to False and must never raise out of this function, because the raw
    # chain on disk -- not the derived levels -- is the one artifact in this app that cannot
    # be recomputed after the fact (the free Cboe source keeps no history). A failure here is
    # self-healing: `uv run python -m app.modules.gex.gex.backfill` picks up any snapshot left without
    # levels on a later run.
    #
    # T87: the chain is handed to `compute_and_store` instead of letting it re-read the file,
    # but **only on a fresh write**. On the duplicate path `row.id` is a *pre-existing*
    # snapshot whose Parquet file is a different object from the chain just fetched -- very
    # probably equal in content, but no longer provably so -- and levels that are reproducible
    # from their own stored Parquet is the property that makes `backfill` a repair tool rather
    # than a second opinion. Duplicates are rare and cost nothing to leave slow.
    try:
        await asyncio.to_thread(
            compute_and_store,
            row.id,
            session_factory=effective_session_factory,
            data_dir=data_dir,
            snapshot=None if skipped_duplicate else snapshot,
        )
    except Exception:  # see the comment above: must never fail the capture
        logger.exception(
            "capture: %s snapshot_id=%d persisted successfully but GEX level computation "
            "failed; it will be picked up by `uv run python -m app.modules.gex.gex.backfill`",
            underlying,
            row.id,
        )
    else:
        # --- T19 publish -----------------------------------------------------------------
        # Strictly after the levels are committed, and only when they were: a client woken by
        # this event immediately re-fetches, and waking it before the rows exist would serve it
        # the *previous* snapshot's levels and leave it stale until the next capture. Hence
        # `else` rather than a line after the try/except.
        #
        # Publishing here rather than inside `app.modules.gex.gex.store` keeps invariant 1 intact -- the
        # engine and its store stay free of transport concerns -- and keeps this beside the
        # structured log line, which is the other thing this function emits to the outside
        # world at exactly this moment.
        #
        # A duplicate capture still publishes. The levels are unchanged, but a client that
        # reconnected since the last event has no way to know that, and a redundant re-fetch is
        # cheaper than a dashboard that sits on stale data because the server decided the
        # nudge was unnecessary.
        #
        # `broker.publish` never raises and never blocks (see its docstring), so this needs no
        # guard of its own -- but it must never be the thing that fails a capture, so if that
        # contract ever changes, this call needs a try/except, not a comment.
        broker.publish(
            snapshot.underlying.value,
            {
                "underlying": snapshot.underlying.value,
                "snapshot_id": row.id,
                "captured_at": snapshot.captured_at,
                "effective_at": effective_data_time(
                    snapshot.captured_at, snapshot.delayed_minutes
                ),
                "is_eod": is_eod,
                "spot": snapshot.spot,
                "skipped_duplicate": skipped_duplicate,
            },
        )
    # -----------------------------------------------------------------------------------------

    return result


def _persist_sync(
    snapshot: ChainSnapshot,
    is_eod: bool,
    session_factory: sessionmaker[Session],
    data_dir: str | Path | None,
) -> tuple[str, Snapshot, bool, str | None]:
    """Write Parquet + index row. Runs off the event loop via `asyncio.to_thread`.

    `SnapshotRepository` is synchronous and commits inside `add()` (see its own module
    docstring) -- calling it directly from an `async def` would block the whole event loop
    for a DB round trip, stalling the *other* symbols' captures that an `AsyncIOScheduler`
    job would otherwise be free to interleave, plus any concurrent API request. Pushing this
    entire write to a worker thread (rather than, say, only the DB half) also keeps the
    Parquet file write -- itself blocking disk I/O -- off the loop.

    Duplicate captures are caught on **two** independent keys, because neither alone is
    sufficient once T18 polls every 15 minutes:

    1. `(underlying, captured_at)` -- the same vendor timestamp already indexed. This means a
       genuine retry or a double-fired job. As of T71 this pair also carries a DB uniqueness
       constraint (`uq_snapshots_underlying_captured_at`), so the check below is now a way to
       skip the redundant Parquet write and return the existing row gracefully rather than the
       only thing standing between the app and a duplicate; a lost race raises at commit
       instead of silently inserting.

    2. **Content** -- `app.modules.gex.storage.fingerprint.chain_fingerprint` of the incoming chain equals
       the fingerprint of the last row stored for this underlying. This is the case check 1
       cannot see, and the one polling introduces. T34 established that Cboe's `timestamp` is
       payload-*generation* time: it keeps advancing while the quotes underneath are frozen, so
       a slot polled before the feed refreshes arrives with a **new** timestamp over
       **identical** data. Writing it would add a Parquet file, an index row and a set of level
       rows describing an instant that never happened, and T20's timeline would plot it as a
       real reading. Compared against the most recent row only, deliberately: a chain that
       returns to a byte-identical earlier state hours later is not a duplicate capture, it is
       a genuine (astonishing) market observation, and suppressing it would lose real data.

    Either way the write is **skipped**, never overwritten -- repointing an existing index row's
    Parquet file out from under it, when the payload may differ in ways the check did not
    consider, is the one outcome worse than a redundant file.
    """
    fingerprint = chain_fingerprint(snapshot)
    with session_factory() as session:
        duplicate_reason: str | None = None
        existing = session.execute(
            select(Snapshot).where(
                Snapshot.underlying == snapshot.underlying.value,
                Snapshot.captured_at == snapshot.captured_at,
            )
        ).scalar_one_or_none()
        if existing is not None:
            duplicate_reason = "captured_at"
            logger.warning(
                "capture: %s at %s already indexed (snapshot id=%d) -- skipping duplicate "
                "write, not overwriting %s",
                snapshot.underlying.value,
                snapshot.captured_at.isoformat(),
                existing.id,
                existing.parquet_path,
            )
        else:
            latest = session.execute(
                select(Snapshot)
                .where(Snapshot.underlying == snapshot.underlying.value)
                .order_by(Snapshot.captured_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            # `content_hash is None` means the row predates T71, not that it has no contracts:
            # comparing against it would be guessing, so an un-fingerprinted predecessor simply
            # never matches and the capture proceeds. Worst case is one redundant snapshot on
            # the first capture after the migration.
            if latest is not None and latest.content_hash is not None and latest.content_hash == fingerprint:
                existing = latest
                duplicate_reason = "content"
                logger.warning(
                    "capture: %s payload at %s is byte-identical to snapshot id=%d at %s "
                    "(vendor timestamp advanced but the chain did not) -- skipping duplicate "
                    "write",
                    snapshot.underlying.value,
                    snapshot.captured_at.isoformat(),
                    latest.id,
                    latest.captured_at.isoformat(),
                )

        if existing is not None:
            if is_eod and not existing.is_eod:
                # The duplicate is the 16:20 EOD job landing on data a 16:15 intraday poll (or
                # a manual capture) already stored -- reachable on both keys, since Cboe's
                # delayed chain only refreshes every ~15 min and is frozen outright after the
                # close. Skipping without promoting the flag would leave the day with *no*
                # is_eod row at all -- an invisible, permanent hole in every eod_only query,
                # T29's catch-up guard and GET /api/gex/health/capture, for a day whose data is
                # actually on disk. Promotion is monotonic: an EOD row is never demoted by a
                # later manual capture.
                existing.is_eod = True
                session.commit()
                logger.info("capture: promoted snapshot id=%d to is_eod=True", existing.id)
            return existing.parquet_path, existing, True, duplicate_reason

        path = write_snapshot(snapshot, data_dir=data_dir)
        repo = SnapshotRepository(session)
        row = repo.add(snapshot, path, is_eod=is_eod, content_hash=fingerprint)
        return Path(path).as_posix(), row, False, None


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
