"""`SnapshotRepository`: the Postgres-side half of snapshot storage (PLAN.md §2).

Pairs with `app/storage/parquet.py`. A capture (T05) writes the Parquet file first, then
calls `SnapshotRepository.add` with the same `ChainSnapshot` and the path just written, so the
index row is always derived from -- never independently re-typed from -- the data that is
actually on disk.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chain import ChainSnapshot
from app.models.db import Snapshot

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

    def add(self, snapshot: ChainSnapshot, parquet_path: str | Path, *, is_eod: bool) -> Snapshot:
        """Index a snapshot just written to Parquet. Returns the persisted row (with `id`)."""
        row = Snapshot(
            underlying=snapshot.underlying.value,
            captured_at=snapshot.captured_at,
            source=snapshot.source,
            spot=snapshot.spot,
            contract_count=len(snapshot),
            parquet_path=Path(parquet_path).as_posix(),
            is_eod=is_eod,
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
