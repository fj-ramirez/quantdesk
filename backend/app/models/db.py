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
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings

__all__ = [
    "Base",
    "DailyBar",
    "Decision",
    "EtfSharesOutstanding",
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


class DailyBar(Base):
    """One symbol's OHLCV summary for one exchange-local trading date (T42,
    plans/continuation/00-foundation-daily-bars.md).

    Unlike `Snapshot`/`GexLevel`/`GexByStrike`, this table's rows are not derived from anything
    in Parquet -- CLAUDE.md invariant 5 ("per-contract rows live only in Parquet") is about
    option contracts specifically; bars are a different, small dataset (roughly 80 symbols x 5
    years, on the order of 100k rows) that every scan query filters and joins against, which is
    exactly what Postgres is for, per the plan's own "Storage is Postgres" design note.

    `date` is a plain SQLAlchemy `Date`, not `UTCDateTime` -- a daily bar has no instant, only
    an exchange-local calendar date (see `app.models.bars.DailyBar`'s own docstring), so the
    tz-aware-instant machinery `UTCDateTime` exists for does not apply here. `Date` round-trips
    to a real `datetime.date` on both SQLite (tests) and Postgres (production) without a custom
    `TypeDecorator` -- verified by `tests/test_bars_repository.py`, which asserts the type on
    read rather than merely the value, per the plan's called-out Windows/SQLite hazard.

    Unique on `(symbol, date)`: `upsert_bars` (`app.storage.bars_repository`) relies on this
    constraint to decide insert-vs-update, and it is what makes a re-run of the bars job or the
    backfill CLI idempotent rather than accumulating duplicate rows for a date already stored.
    Indexed on `symbol` alone as well, since every read (`read_bars`, `last_bar_date`,
    `read_universe_closes`) starts from "this symbol" before narrowing by date.
    """

    __tablename__ = "daily_bars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    # None means "vendor did not report volume"; 0 means "reported, genuinely zero" (^VIX).
    # Never collapse the two -- CLAUDE.md invariant 3, applied to volume instead of open
    # interest. See app.models.bars.DailyBar's own docstring for the full rationale.
    #
    # BigInteger, not Integer: index volume overflows int4. ^GSPC (SPX) prints on the order of
    # 4.97e9 shares a day against Postgres's int4 ceiling of 2_147_483_647, so `Integer` here
    # raised `NumericValueOutOfRange` on the very first real SPX backfill. Nothing in the test
    # suite caught it because SQLite's INTEGER is 64-bit and accepts the value silently -- the
    # same SQLite-accepts-what-Postgres-rejects trap `UTCDateTime` above exists for. Any column
    # holding a share/contract count for an *index* needs 64 bits.
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_daily_bars_symbol_date"),
        Index("ix_daily_bars_symbol", "symbol"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<DailyBar symbol={self.symbol!r} date={self.date.isoformat()!r} "
            f"close={self.close!r} volume={self.volume!r}>"
        )


class EtfSharesOutstanding(Base):
    """One fund's issuer-published shares outstanding for one calendar as-of date (T52,
    plans/continuation/05-etf-flows.md; survey: docs/etf-flows-sources.md).

    **`date` is the issuer's own stated as-of date, never the day the fetch ran.** The survey
    that preceded this table found issuer files lag a full trading day -- at 20:56 ET on a
    Wednesday, State Street's file still carried Tuesday's date, and iShares was not even
    uniform about it across its own funds captured at the same instant. Keying on the run date
    (the plan's original design) would either overwrite yesterday's real value with a
    duplicate under today's date, or -- under the "skip unless as-of is today" rule the survey
    replaced -- never insert a row at all. `(symbol, date)` unique below is what makes
    `app.storage.flows_repository.insert_new_rows`'s "insert when unseen, do nothing when
    already stored" contract enforceable at the database level, not just in application code.

    `date` is a plain SQLAlchemy `Date`, same choice and same rationale as `DailyBar.date`
    above: an as-of date is a calendar date with no instant attached, so the `UTCDateTime`
    machinery for tz-aware instants does not apply, and `Date` round-trips identically on
    SQLite (tests) and Postgres (production) without a custom type.

    `shares` is `BigInteger`, not `Integer`, for the same belt-and-suspenders reason as
    `DailyBar.volume`: every fund in this app's covered universe today fits comfortably in a
    32-bit int, but a share count is exactly the kind of large, ever-growing index quantity
    that has already bitten this codebase once (SPX volume overflowing Postgres `int4` on the
    very first real backfill, caught only because SQLite's 64-bit `INTEGER` accepted it
    silently in tests) -- see `DailyBar.volume`'s own docstring for the full story.

    `nav` is nullable: the SPDR all-funds file always reports one, but the trimmed iShares
    product-page fixtures this table's provider was built and tested against
    (`docs/etf-flows-sources.md`'s iShares section) do not carry a `navAmount` block at all --
    a provider that cannot find one supplies `None` rather than fabricating a NAV from some
    other source, matching the `SharesOutstandingProvider` contract's own `nav | None` return.

    `source` names the provider that produced the row (`'spdr-xlsx'`, `'ishares-productpage'`)
    -- the per-fund freshness/provenance the health block and the flows API both surface,
    mirroring `DailyBar.source`.
    """

    __tablename__ = "etf_shares_outstanding"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nav: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_etf_shares_outstanding_symbol_date"),
        Index("ix_etf_shares_outstanding_symbol", "symbol"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<EtfSharesOutstanding symbol={self.symbol!r} date={self.date.isoformat()!r} "
            f"shares={self.shares!r} nav={self.nav!r}>"
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


class Decision(Base):
    """One opportunity the decision engine emitted, frozen at the moment it was recorded, plus
    what the bars that followed said about it (T61).

    **Why a table at all, when every other scan route computes on request.** The engine's
    suggestions are only calibratable against what happened *afterwards*, and "afterwards" is
    the one thing a recompute can never reproduce: tomorrow's chain gives tomorrow's levels,
    not today's. So the levels are written down once per `(snapshot, filter, key)` -- the
    unique constraint below makes a second run on the same snapshot a no-op rather than a
    duplicate -- and the outcome columns are filled in by `app.jobs.decisions` as bars arrive.

    `decided_on` is the *trading date* of the chain the suggestion came from (`effective_at`
    in New York), not the run date: the bars that score it are those strictly after this
    date. `payload` is the full `Opportunity.to_dict()` as JSON text (thesis, invalidation,
    score breakdown -- everything the row's scalar columns do not repeat), so a later
    calibration pass can ask "which thesis lines were present on the winners" without a
    schema change; `Text` rather than a JSON column so it round-trips identically on SQLite.

    `outcome` vocabulary and the R-unit fields are `app.scan.outcomes.Outcome`'s, column for
    column; `outcome_note` is that record's `note`. `result_r` is `None` until resolved, and
    `mark_r` is the unrealized R while `pending` -- never collapsed into one column, for the
    same None-vs-zero discipline the rest of the schema keeps.
    """

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    underlying: Mapped[str] = mapped_column(String(16), nullable=False)
    filter: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("snapshots.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    setup: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    grade: Mapped[str] = mapped_column(String(1), nullable=False)
    entry: Mapped[float] = mapped_column(Float, nullable=False)
    stop: Mapped[float] = mapped_column(Float, nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    target_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    atr14: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    fill: Mapped[float | None] = mapped_column(Float, nullable=True)
    triggered_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    resolved_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    bars_held: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mfe_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    mark_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluated_through: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "filter", "key", name="uq_decisions_snapshot_filter_key"),
        Index("ix_decisions_underlying_decided_on", "underlying", "decided_on"),
        Index("ix_decisions_outcome", "outcome"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<Decision id={self.id} {self.underlying} {self.key} decided_on={self.decided_on} "
            f"outcome={self.outcome!r}>"
        )
