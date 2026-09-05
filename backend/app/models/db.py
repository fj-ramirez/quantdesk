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

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings

__all__ = [
    "Base",
    "GexByStrike",
    "GexLevel",
    "Snapshot",
    "UTCDateTime",
    "get_engine",
    "get_sessionmaker",
]


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
    (posix separators) so the index stays valid if `DATA_DIR` itself moves between hosts --
    see `app.storage.parquet.to_data_dir_relative_path` (write side, used by
    `app.storage.repository.SnapshotRepository.add`) and
    `app.storage.parquet.resolve_snapshot_path` (read side). Every reader must go through
    `resolve_snapshot_path` rather than joining `settings.DATA_DIR` itself -- that's the one
    place the column's on-disk convention is decoded (TASKS.md T30).
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


class GexLevel(Base):
    """One row per (snapshot, expiry filter): the headline numbers from
    `app.gex.engine.key_levels`, persisted by `app.gex.store.compute_and_store` (T09) so the
    read API and history views never reopen Parquet and re-run the engine just to answer
    "what was the flip point that day".

    **Every numeric level column is nullable, on purpose, not defensively.** `key_levels`
    legitimately returns `None` for a wall, the flip point, or every level at once whenever
    the filter admits no contracts -- the everyday example is `ZERO_DTE` on an EOD capture
    taken at 16:20 ET, after every same-day contract has expired, not some rare edge case --
    or whenever the ±10% gamma profile never changes sign. Storing `0.0` in either case would
    misreport "no level exists" as "level at strike zero", exactly the kind of silent
    meaning-corruption this schema avoids everywhere else (see `UTCDateTime` above, and the
    None-vs-zero open-interest rule in `app.gex.engine`). A missing wall reads back as
    `None`, never `0`.

    `call_wall_gex` / `put_wall_gex` / `max_call_gex_strike` / `max_put_gex_strike` are
    persisted even though TASKS.md's column list for this table does not name them: T08's
    `KeyLevels` computes them as part of the same pass, and the alternative -- recomputing a
    single snapshot's levels means re-reading its Parquet file and re-running the whole
    engine, not a cheap lookup -- makes it worth the four extra float columns to have them on
    hand already. `max_call_gex_strike` / `max_put_gex_strike` in particular are the per-side
    reading `key_levels`' own docstring warns must never be confused with `call_wall` /
    `put_wall` (which are net-GEX-based); persisting both under their distinct names keeps
    that distinction intact in storage, not just in memory.
    """

    __tablename__ = "gex_levels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("snapshots.id"), nullable=False)
    filter: Mapped[str] = mapped_column(String(32), nullable=False)
    net_gex: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_wall: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_wall_gex: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_wall: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_wall_gex: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_abs_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_call_gex_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_put_gex_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    flip_point: Mapped[float | None] = mapped_column(Float, nullable=True)
    spot: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)

    __table_args__ = (
        # One row per snapshot per filter -- the uniqueness story `compute_and_store` relies
        # on for its delete-then-insert replace to be idempotent rather than accumulating.
        UniqueConstraint("snapshot_id", "filter", name="uq_gex_levels_snapshot_filter"),
        Index("ix_gex_levels_snapshot_id", "snapshot_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<GexLevel snapshot_id={self.snapshot_id} filter={self.filter!r} "
            f"net_gex={self.net_gex!r} flip_point={self.flip_point!r}>"
        )


class GexByStrike(Base):
    """Per-strike dollar GEX for one (snapshot, expiry filter) -- `app.gex.engine.by_strike`'s
    output, persisted so a GEX-by-strike chart reads rows instead of recomputing them.

    This table is the volume driver: a full SPX chain spans on the order of a hundred-plus
    distinct strikes per filter, times three filters, times three symbols, times every
    capture -- and T18 adds a capture every 15 minutes during the session on top of the daily
    EOD one. `compute_and_store` deletes the existing `(snapshot_id, filter)` slice before
    reinserting, so a re-run (a retried capture, a backfill pass) replaces rather than
    accumulates; the unique constraint below is the backstop against two writers racing past
    that delete, not the primary mechanism.

    `call_gex` / `put_gex` / `net_gex` are never `None` here, unlike `GexLevel`: a strike
    only gets a row when it actually had admitted contracts contributing to it, so a filter
    that admits nothing (`ZERO_DTE` after the close) simply writes zero rows for that filter
    rather than rows full of nulls.
    """

    __tablename__ = "gex_by_strike"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("snapshots.id"), nullable=False)
    filter: Mapped[str] = mapped_column(String(32), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    call_gex: Mapped[float] = mapped_column(Float, nullable=False)
    put_gex: Mapped[float] = mapped_column(Float, nullable=False)
    net_gex: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "filter", "strike", name="uq_gex_by_strike_snapshot_filter_strike"
        ),
        # Leading (snapshot_id, filter) serves the dominant read shape (T11: "every strike for
        # this snapshot and this filter") as well as the unique constraint above.
        Index("ix_gex_by_strike_snapshot_filter", "snapshot_id", "filter"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<GexByStrike snapshot_id={self.snapshot_id} filter={self.filter!r} "
            f"strike={self.strike!r} net_gex={self.net_gex!r}>"
        )


def get_engine(database_url: str | None = None) -> Engine:
    """Create an engine for `database_url`, defaulting to `settings.DATABASE_URL`.

    Never called at import time -- Alembic's ``env.py`` and tests each need a different URL,
    and constructing an engine has side effects (connection pool setup) that don't belong at
    module import.

    T35: psycopg's default connect timeout is "however long the OS takes to give up on the
    TCP handshake" -- unbounded from this app's point of view, and the actual mechanism behind
    the startup hang the moment any caller runs a connection attempt synchronously (as
    `app/jobs/catchup.py`'s `has_eod_snapshot_today` used to). Moving that call to a worker
    thread (see `catch_up_missed_eod`) already keeps a slow connect off the event loop, but a
    short, explicit `connect_timeout` still bounds how long that thread -- and, for the
    catch-up's own logging, how long the user waits to see it give up -- is on the hook for.
    Only applied to Postgres URLs: SQLite (every test's `session_factory`) has no such
    keyword and would fail `create_engine` outright.
    """
    url = database_url or settings.DATABASE_URL
    connect_args = {"connect_timeout": 5} if url.startswith("postgresql") else {}
    return create_engine(url, connect_args=connect_args)


def get_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to `engine`.

    ``expire_on_commit=False`` so a `Snapshot` returned by `SnapshotRepository.add` stays
    readable (e.g. for logging its `id`) after the commit that persisted it, without an extra
    round trip.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)
