"""Persistence for computed GEX levels (T09) -- the join between the capture pipeline (T05)
and the GEX engine (T08). ``app.modules.gex.gex.engine`` is pure and does no I/O by design; this module is
where its output actually meets a database, so the read API (T11) and history views can
answer "what was the flip point on this snapshot" by reading a row instead of reopening the
snapshot's Parquet file and re-running ``compute_all``.

Design constraints from the T09 brief, restated here because they are load-bearing:

* **Every level column is nullable and every write here preserves exactly what
  ``key_levels`` returned.** ``None`` stays ``None`` end to end -- see ``GexLevel``'s own
  docstring in ``app.modules.gex.models.db`` for why (the ZERO_DTE-after-close case is the everyday
  example this schema exists to get right, not a corner case).
* **Idempotent by construction, not by relying on the unique constraint alone.**
  :func:`compute_and_store` deletes the existing ``(snapshot_id, filter)`` slice of both
  tables before writing the freshly computed one, so calling it twice for the same snapshot
  (a re-run capture hook, a backfill re-run, or a duplicate-capture skip that still reaches
  the T09 seam with a pre-existing ``row.id``) replaces rather than accumulates. The unique
  constraints in the migration exist as a backstop against two writers racing past that
  delete, not as the primary mechanism -- see the module docstring on ``GexByStrike`` for why
  that matters at this table's row count.
* **A level-computation failure must never cost the underlying capture.** This function
  raises on any failure (missing snapshot row, unreadable Parquet, a DB error) rather than
  swallowing it -- that decision belongs to the caller. ``app.modules.gex.jobs.capture``'s T09 seam and
  the backfill CLI (``app.modules.gex.gex.backfill``) each catch, log, and move on, exactly because the
  raw Parquet chain is irreplaceable (the free Cboe source keeps no history) while a level row
  can always be recomputed later.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.gex.engine import ExpiryFilter, compute_all, to_frame
from app.modules.gex.gex.report import IvRegime, iv_regime
from app.modules.gex.models.chain import ChainSnapshot
from app.modules.gex.models.db import GexByExpiry, GexByStrike, GexLevel, Snapshot
from app.modules.gex.storage.parquet import read_snapshot, resolve_snapshot_path

__all__ = ["DEFAULT_FILTERS", "compute_and_store", "get_session_factory"]

logger = logging.getLogger("app.modules.gex.gex.store")

#: T09 brief: compute and store levels for these three filters on every capture.
DEFAULT_FILTERS: tuple[ExpiryFilter, ...] = (
    ExpiryFilter.ALL,
    ExpiryFilter.ZERO_DTE,
    ExpiryFilter.EX_ZERO_DTE,
)

# A cache separate from `app.modules.gex.jobs.capture`'s own: importing that module's factory here would
# be a circular import (the T09 seam in capture.py calls into this module), and its docstring
# already says not to reuse it outside the capture path. `app.modules.gex.jobs.capture` passes its own
# factory into `compute_and_store` explicitly in the normal capture flow, so this cache is
# really only exercised by the standalone backfill CLI.
_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    """Cached, lazily-created sessionmaker bound to `settings.DATABASE_URL`.

    Tests should not use this -- pass an explicit `session_factory` to `compute_and_store`,
    same pattern as `app.modules.gex.jobs.capture.capture_snapshot`.
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = get_sessionmaker(get_engine())
    return _session_factory


def compute_and_store(
    snapshot_id: int,
    *,
    session_factory: sessionmaker[Session] | None = None,
    data_dir: str | Path | None = None,
    filters: Sequence[ExpiryFilter] = DEFAULT_FILTERS,
    snapshot: ChainSnapshot | None = None,
) -> tuple[GexLevel, ...]:
    """Compute GEX levels for `snapshot_id` under `filters` and persist them.

    Loads the snapshot's index row, reopens its Parquet file, flattens it once with
    `app.modules.gex.gex.engine.to_frame` (shared across every filter -- the flatten, not the aggregation,
    is the expensive step), runs `compute_all` per filter, and replaces that filter's slice of
    `gex_levels` / `gex_by_strike`.

    Args:
        snapshot_id: `Snapshot.id` to compute levels for.
        session_factory: Defaults to `get_session_factory()` (real Postgres). Tests, and
            `app.modules.gex.jobs.capture`'s capture hook, pass an explicit factory.
        data_dir: Forwarded to Parquet path resolution; defaults to `settings.DATA_DIR`.
        filters: Which expiry filters to compute. Defaults to `DEFAULT_FILTERS`.
        snapshot: T87. The chain this call would otherwise re-read from disk. The caller
            passes it **only when it just wrote that Parquet file itself** -- see the note
            below. `None`, the default, keeps the read: that is what `app.modules.gex.gex.backfill`
            does, having nothing but a `snapshot_id` to work from.

    Returns:
        The persisted `GexLevel` rows, one per filter, in the order of `filters`.

    Raises:
        ValueError: no `Snapshot` row exists for `snapshot_id`.
        Exception: any Parquet read or database error propagates -- see the module docstring
            for why this function does not catch on the caller's behalf.
    """
    factory = session_factory or get_session_factory()
    with factory() as session:
        row = session.get(Snapshot, snapshot_id)
        if row is None:
            raise ValueError(f"no snapshot with id={snapshot_id}")

        # T87. A capture materializes ~63,000 Pydantic contract models per cycle, and before
        # this shortcut it materialized them twice: once from the vendor payload, then again
        # here from the file it had just written, with the first set still alive for the T19
        # publish. The caller hands over the object it already has.
        #
        # **`snapshot` must be the chain that was written to this snapshot's Parquet file.**
        # A snapshot's levels being reproducible from its stored Parquet is what makes
        # `app.modules.gex.gex.backfill` a valid repair tool at all, so the capture path
        # passes this only on a fresh write and keeps reading from disk on the duplicate
        # path, where `row.id` points at an *earlier* snapshot's file.
        if snapshot is None:
            path = resolve_snapshot_path(row, data_dir)  # invariant 5
            snapshot = read_snapshot(path)
        frame = to_frame(snapshot)

        # T103. The frame is already built and the spot is already known, so the constant-
        # maturity ATM vol costs nothing here -- and everything, later, to anyone who has to
        # reopen this Parquet file to get it. Never fatal: a snapshot whose chain carries no
        # usable IV still has perfectly good gamma, and losing the whole capture over a
        # missing vol would be the wrong trade.
        iv: IvRegime | None
        try:
            iv = iv_regime(frame, snapshot.spot)
        except Exception:
            logger.exception(
                "compute_and_store: ATM IV failed for snapshot_id=%d; storing null", snapshot_id
            )
            iv = None

        # Written on the snapshot row itself: IV is a property of the chain, not of an expiry
        # filter, so it has no business being repeated per (snapshot, filter).
        row.atm_iv = None if iv is None else iv.atm_iv
        row.atm_iv_target_dte = None if iv is None else iv.target_dte
        row.atm_iv_lower_dte = None if iv is None else iv.lower_dte
        row.atm_iv_upper_dte = None if iv is None else iv.upper_dte
        row.atm_iv_interpolated = None if iv is None else iv.interpolated
        row.atm_iv_contracts = None if iv is None else iv.contracts

        stored: list[GexLevel] = []
        for f in filters:
            result = compute_all(snapshot, f, frame=frame)
            levels = result.levels

            # Idempotent replace, not an upsert: see the module docstring. One row for
            # gex_levels either way, but for gex_by_strike (hundreds of rows per snapshot per
            # filter) this delete-before-insert is what keeps a repeated capture hook call or
            # backfill re-run from ballooning the table.
            session.execute(
                delete(GexLevel).where(
                    GexLevel.snapshot_id == snapshot_id, GexLevel.filter == f.value
                )
            )
            session.execute(
                delete(GexByStrike).where(
                    GexByStrike.snapshot_id == snapshot_id, GexByStrike.filter == f.value
                )
            )
            session.execute(
                delete(GexByExpiry).where(
                    GexByExpiry.snapshot_id == snapshot_id, GexByExpiry.filter == f.value
                )
            )

            level_row = GexLevel(
                snapshot_id=snapshot_id,
                filter=f.value,
                net_gex=levels.net_gex,
                call_wall=levels.call_wall,
                call_wall_gex=levels.call_wall_gex,
                put_wall=levels.put_wall,
                put_wall_gex=levels.put_wall_gex,
                max_abs_strike=levels.max_abs_strike,
                max_call_gex_strike=levels.max_call_gex_strike,
                max_put_gex_strike=levels.max_put_gex_strike,
                flip_point=levels.flip_point,
                spot=levels.spot,
                computed_at=levels.computed_at,
            )
            session.add(level_row)

            if result.by_strike:
                # Bulk core insert rather than one ORM object per strike -- hundreds of rows
                # per (snapshot, filter), and this runs once per capture.
                session.execute(
                    insert(GexByStrike),
                    [
                        {
                            "snapshot_id": snapshot_id,
                            "filter": f.value,
                            "strike": s.strike,
                            "call_gex": s.call_gex,
                            "put_gex": s.put_gex,
                            "net_gex": s.net_gex,
                            # T101. Null (not zero) if this frame predates the horizon split.
                            "net_gex_0dte": s.net_gex_0dte,
                            "net_gex_this_week": s.net_gex_this_week,
                            "net_gex_next_30d": s.net_gex_next_30d,
                            "net_gex_beyond_30d": s.net_gex_beyond_30d,
                        }
                        for s in result.by_strike
                    ],
                )
            if result.by_expiry:
                # T101: one row per expiry per filter. Cheap -- it does not multiply by strike.
                session.execute(
                    insert(GexByExpiry),
                    [
                        {
                            "snapshot_id": snapshot_id,
                            "filter": f.value,
                            "expiry": e.expiry,
                            "dte": e.dte,
                            "call_gex": e.call_gex,
                            "put_gex": e.put_gex,
                            "net_gex": e.net_gex,
                            "abs_gex": e.abs_gex,
                            "contracts": e.contracts,
                            "open_interest": e.open_interest,
                        }
                        for e in result.by_expiry
                    ],
                )
            stored.append(level_row)

        session.commit()
        return tuple(stored)
