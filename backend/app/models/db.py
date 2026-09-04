"""SQLAlchemy models for the Postgres side of storage (PLAN.md §2).

Postgres holds computed results and an *index* of what raw snapshots exist on disk as
Parquet (`app/storage/parquet.py`); it never holds per-contract rows itself. `Snapshot` is
that index: one row per `ChainSnapshot` written, pointing at the Parquet file that holds the
contracts.

The engine/session helpers here read the database URL from `app.config.settings` rather than
constructing an engine at import time, so a caller (tests, Alembic's ``env.py``, a future
async wrapper) controls exactly which URL and how many engines exist. Table definitions target
plain SQLAlchemy core types (`String`, `Float`, `Integer`, `Boolean`, `DateTime`) rather than
any Postgres-specific type, so the identical migration and identical model code run unchanged
against SQLite in tests and Postgres in production -- see `UTCDateTime` below for the one place
that portability needs help.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings

__all__ = ["Base", "Snapshot", "UTCDateTime", "get_engine", "get_sessionmaker"]


class UTCDateTime(TypeDecorator):
    """A tz-aware UTC `datetime` that round-trips identically on SQLite and Postgres.

    Postgres's ``TIMESTAMPTZ`` (what SQLAlchemy's ``DateTime(timezone=True)`` maps to there)
    genuinely stores an instant. SQLite has no timezone-aware column type at all -- the
    stdlib ``sqlite3`` DBAPI passes datetimes through as strings and, critically, reads them
    back **naive**, silently dropping ``tzinfo`` even though nothing on the write side
    complained. `ChainSnapshot.captured_at` and this app's `Snapshot.captured_at` are required
    to be tz-aware UTC everywhere else in the codebase, so letting that invariant quietly die
    at the SQLite boundary is exactly the kind of bug that only shows up in production against
    real Postgres, i.e. never in a fast test suite. This type closes that gap: it rejects a
    naive datetime on the way in (loudly, in `process_bind_param`, on *both* backends) and
    always hands back an aware UTC datetime on the way out, so the same assertions
    (`captured_at.tzinfo is UTC`, `captured_at == <aware instant>`) hold in SQLite tests and in
    production Postgres alike.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        value = value.astimezone(dt.UTC)
        if dialect.name == "sqlite":
            # SQLite has no tz-aware storage; store the UTC wall-clock value naive and let
            # `process_result_value` reattach UTC on read. Postgres keeps the aware value --
            # TIMESTAMPTZ normalizes to UTC internally regardless of what offset was given.
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    """Index row for one Parquet-stored `ChainSnapshot`.

    Deliberately thin: everything queryable without opening the Parquet file (for listing,
    filtering by date range, finding the latest snapshot per symbol) lives here;
    per-contract data stays in Parquet. `parquet_path` is stored relative to `DATA_DIR`
    (posix separators) so the index stays valid if `DATA_DIR` itself moves between hosts.
    """

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    underlying: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    contract_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parquet_path: Mapped[str] = mapped_column(String(512), nullable=False)
    is_eod: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        # The dominant query shape (T05's GET /api/snapshots, T09's compute_and_store,
        # `SnapshotRepository.latest`/`list`) is "this underlying, ordered by time" -- a
        # composite index on exactly that pair serves all of them without a separate scan.
        Index("ix_snapshots_underlying_captured_at", "underlying", "captured_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<Snapshot id={self.id} underlying={self.underlying!r} "
            f"captured_at={self.captured_at.isoformat()!r} is_eod={self.is_eod}>"
        )


def get_engine(database_url: str | None = None) -> Engine:
    """Create an engine for `database_url`, defaulting to `settings.DATABASE_URL`.

    Never called at import time -- Alembic's ``env.py`` and tests each need a different URL,
    and constructing an engine has side effects (connection pool setup) that don't belong at
    module import.
    """
    return create_engine(database_url or settings.DATABASE_URL)


def get_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to `engine`.

    ``expire_on_commit=False`` so a `Snapshot` returned by `SnapshotRepository.add` stays
    readable (e.g. for logging its `id`) after the commit that persisted it, without an extra
    round trip.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)
