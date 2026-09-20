"""The terminal module's six tables, in the `terminal` schema (T79).

**Named `tables.py`, not `models/db.py` like the other two modules, and that is deliberate.**
xactx already owns a `models.py` -- its Pydantic domain types (`Observation`, `SeriesMeta`) that
the adapters and loader import. A `models/` package beside it shadows that module and breaks
every one of those imports. The ported file is the one that must not move, because keeping it
where it was is what makes the port diffable against the standalone repo; so the new file took
the different name.

**These models exist for Alembic, not for the module's own queries.** The analytics read
frames, not objects -- `board.py`, `factors.py`, `regime.py` and `graph.py` all pull into pandas
and work there -- so the ported code keeps its raw SQL through a psycopg connection
(`store/db.py`). Rewriting 25 call sites into the ORM would have been a large diff with no
beneficiary, and would have put this module's point-in-time semantics at risk in the same
commit that moved it.

Transcribed from `store/schema.sql`, whose header promised it was "kept close to ANSI so the
same tables can be created in Postgres later without a redesign". That promise held: the only
type change the port needed was `DOUBLE` -> `DOUBLE PRECISION`. `TEXT`, `BOOLEAN`, `INTEGER`,
`DATE` and `TIMESTAMPTZ` carried over untouched.

**The point-in-time rule is this module's invariant.** A revision *adds a row* and never
overwrites one: `as_of` is part of `observations`' primary key, so one (series, date) may hold
many vintages and none destroys another. `as_of_basis` records per row how that `as_of` was
established, because a single series mixes bases and the provenance therefore belongs on the
observation rather than only on its metadata row. Anything that made `as_of` updatable -- an
upsert, a "correction", a dedupe -- would destroy the only thing this module has that a price
feed does not.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    Index,
    Integer,
    MetaData,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.schemas import SCHEMA_TERMINAL
from app.modules.gex.models.db import UTCDateTime

__all__ = [
    "Base",
    "EdgeDefinition",
    "EdgeStat",
    "IngestBatch",
    "Observation",
    "Release",
    "SeriesMetadata",
]


class Base(DeclarativeBase):
    """Declarative base for the terminal module. Its schema is decided once, here.

    Invariant 8: a table added later is `terminal.something` because this metadata says so,
    never because a model remembered a `__table_args__`.
    """

    metadata = MetaData(schema=SCHEMA_TERMINAL)


class SeriesMetadata(Base):
    """One row per series. Descriptive, and safe to correct in place -- unlike observations."""

    __tablename__ = "series_metadata"

    series_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    asset_class: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    default_transform: Mapped[str] = mapped_column(Text, nullable=False)
    revisable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: 'source_vintage' | 'ingest_time' -- whether `as_of` is a real publisher vintage or our
    #: first-seen time. The difference decides whether a backtest over this series is honest.
    vintage_source: Mapped[str] = mapped_column(Text, nullable=False)
    #: NULL means the series does not honour the global snapshot convention (e.g. 24-hour FX).
    #: Recorded, not assumed away.
    snapshot_tz: Mapped[str | None] = mapped_column(Text)
    snapshot_local_time: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class Observation(Base):
    """The point-in-time table. **`as_of` in the primary key is the whole design.**

    One (series_id, value_date) may hold many vintages and none overwrites another, which is
    what lets any screen re-render the world as it looked at a past moment rather than as it is
    now known to have been.
    """

    __tablename__ = "observations"

    series_id: Mapped[str] = mapped_column(Text, primary_key=True)
    value_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    as_of: Mapped[dt.datetime] = mapped_column(UTCDateTime(timezone=True), primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    #: How `as_of` was established for THIS row: source_vintage | derived_lag | archive_floor.
    #: A series mixes these, so the per-row column is the authority -- not the series' dominant
    #: basis.
    as_of_basis: Mapped[str] = mapped_column(Text, nullable=False)
    source_batch: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        # Carried over from the original. Point-in-time reads filter `series_id = ? AND as_of
        # <= ?`, which is this index exactly.
        Index("observations_series_asof", "series_id", "as_of"),
    )


class Release(Base):
    """Scheduled releases and their consensus. **Empty, and knowingly so.**

    Phase 4 is blocked on a paid consensus vendor, and moving house does not unblock it. The
    table comes across so the schema is whole and the constraint stays documented; the UI says
    so wherever a consensus would otherwise appear, rather than rendering a blank that looks
    like a zero.
    """

    __tablename__ = "releases"

    release_id: Mapped[str] = mapped_column(Text, primary_key=True)
    series_id: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    consensus: Mapped[float | None] = mapped_column(Float)
    #: Spec 2.3: must be strictly before `scheduled_at`. Enforced by the loader when this table
    #: starts being written, not by a CHECK, so the violation can name the offending release.
    consensus_as_of: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(timezone=True))
    prior: Mapped[float | None] = mapped_column(Float)
    actual: Mapped[float | None] = mapped_column(Float)
    actual_as_of: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(timezone=True))


class IngestBatch(Base):
    """Every ingestion run, so any stored value traces back to the fetch that produced it."""

    __tablename__ = "ingest_batches"

    source_batch: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(timezone=True))
    adapter: Mapped[str | None] = mapped_column(Text)
    args: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class EdgeDefinition(Base):
    """The transmission graph's theoretical half: which series should move which."""

    __tablename__ = "edge_definitions"

    from_series: Mapped[str] = mapped_column(Text, primary_key=True)
    to_series: Mapped[str] = mapped_column(Text, primary_key=True)
    #: +1 / -1 / 0. **Zero is a real value, not a missing one**: it means the sign is genuinely
    #: regime-dependent, and asserting one would mislead exactly when it matters most (spec 4,
    #: equity/rates). Same rule as GEX's invariant 3 about open interest.
    expected_sign: Mapped[int] = mapped_column(Integer, nullable=False)
    typical_lag_days: Mapped[int] = mapped_column(Integer, nullable=False)
    chain: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


class EdgeStat(Base):
    """The empirical half, recomputed nightly and **kept**, never overwritten.

    Split from `edge_definitions` for the same reason observations carry an `as_of`: overwriting
    the estimate would destroy the history of how a relationship changed, which spec 4 names as
    precisely the signal that matters most.
    """

    __tablename__ = "edge_stats"

    from_series: Mapped[str] = mapped_column(Text, primary_key=True)
    to_series: Mapped[str] = mapped_column(Text, primary_key=True)
    as_of: Mapped[dt.datetime] = mapped_column(UTCDateTime(timezone=True), primary_key=True)
    value_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    beta: Mapped[float | None] = mapped_column(Float)
    beta_window: Mapped[int] = mapped_column(Integer, nullable=False)
    beta_t_stat: Mapped[float | None] = mapped_column(Float)
    r_squared: Mapped[float | None] = mapped_column(Float)
    corr: Mapped[float | None] = mapped_column(Float)
    #: Where this correlation sits in its own trailing history. Spec 4 calls this and
    #: `sign_conflict` the two highest-value outputs of the whole graph.
    corr_percentile: Mapped[float | None] = mapped_column(Float)
    corr_history_n: Mapped[int | None] = mapped_column(Integer)
    sign_conflict: Mapped[bool | None] = mapped_column(Boolean)
    significant: Mapped[bool | None] = mapped_column(Boolean)
    n_obs: Mapped[int] = mapped_column(Integer, nullable=False)
    source_batch: Mapped[str] = mapped_column(Text, nullable=False)
