"""`SnapshotRepository`: the Postgres-side half of snapshot storage (PLAN.md §2).

Pairs with `app/modules/gex/storage/parquet.py`. A capture (T05) writes the Parquet file first, then
calls `SnapshotRepository.add` with the same `ChainSnapshot` and the path just written, so the
index row is always derived from -- never independently re-typed from -- the data that is
actually on disk.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gex.models.chain import ChainSnapshot
from app.modules.gex.models.db import Snapshot
from app.modules.gex.storage.parquet import to_data_dir_relative_path

__all__ = ["SnapshotRepository"]


class SnapshotRepository:
    """CRUD-lite access to the `snapshots` index table.

    Takes a `Session` rather than owning an engine: callers (a request handler, a scheduler
    job, a test) control the session's lifetime and transaction boundaries. Each method here
    commits its own write -- there is exactly one table involved, so there is no multi-step
    unit of work for a caller to coordinate across repository calls. (T09 adds `gex_levels` /
    `gex_by_strike` rows in a separate step after `add` returns; if that ever needs to be
    atomic with the snapshot insert, wrap both in a caller-managed transaction instead of
    relying on this method's own commit.)
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        snapshot: ChainSnapshot,
        parquet_path: str | Path,
        *,
        is_eod: bool,
        data_dir: str | Path | None = None,
        content_hash: str | None = None,
    ) -> Snapshot:
        """Index a snapshot just written to Parquet. Returns the persisted row (with `id`).

        `parquet_path` is normalized to be relative to `data_dir` (posix separators) via
        `app.modules.gex.storage.parquet.to_data_dir_relative_path` before it's stored -- see
        `Snapshot.parquet_path`'s own docstring for why storing anything else (the raw,
        already-`DATA_DIR`-joined value `write_snapshot` returns) makes the row unreadable
        the moment `DATA_DIR` differs between where it was written and where it's read.
        `data_dir` should be the same value passed to `write_snapshot` for this `parquet_path`
        -- it defaults to `settings.DATA_DIR`, matching `write_snapshot`'s own default, so a
        caller that didn't override one doesn't need to override the other.

        `content_hash` (T71) is `app.modules.gex.storage.fingerprint.chain_fingerprint(snapshot)` when the
        caller has computed it. It defaults to `None` rather than being computed here on
        demand: hashing a 28,650-contract SPX chain is real work, the capture path already
        needs the value *before* this call (to decide whether to write the Parquet file at
        all), and silently recomputing it would double that cost on every capture.
        """
        row = Snapshot(
            underlying=snapshot.underlying.value,
            captured_at=snapshot.captured_at,
            source=snapshot.source,
            spot=snapshot.spot,
            contract_count=len(snapshot),
            parquet_path=to_data_dir_relative_path(parquet_path, data_dir),
            is_eod=is_eod,
            content_hash=content_hash,
        )
        self._session.add(row)
        self._session.commit()
        return row

    def latest(self, underlying: str) -> Snapshot | None:
        """Most recent snapshot row for `underlying`, or `None` if none exist yet."""
        stmt = (
            select(Snapshot)
            .where(Snapshot.underlying == underlying)
            .order_by(Snapshot.captured_at.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def list(
        self,
        underlying: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
        eod_only: bool = False,
    ) -> list[Snapshot]:
        """Snapshot rows for `underlying` in `[start, end]` (either bound optional), ascending
        by `captured_at`. `start`/`end` must be tz-aware if given -- naive bounds would compare
        incorrectly against `UTCDateTime`'s always-aware output on Postgres.
        """
        stmt = select(Snapshot).where(Snapshot.underlying == underlying)
        if start is not None:
            stmt = stmt.where(Snapshot.captured_at >= start)
        if end is not None:
            stmt = stmt.where(Snapshot.captured_at <= end)
        if eod_only:
            stmt = stmt.where(Snapshot.is_eod.is_(True))
        stmt = stmt.order_by(Snapshot.captured_at.asc())
        return list(self._session.execute(stmt).scalars().all())
