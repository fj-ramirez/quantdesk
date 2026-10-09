"""The broker module's tables, in the `broker` schema (T144, plans/charter-mt5/README.md).

**Created by Alembic** (revision `f7a8b9c0d1e2`), like every other module's tables. While
charter-mt5 was an experiment the `broker-ingest` worker created them itself (decision 10); the
merge moved that into the chain, along with the `levels` view and the read-only grants. The
worker now only waits for them: :func:`schema_ready`.

Point-in-time where it matters:

* `bars` are market prices keyed on `(symbol, timeframe, ts)`. A closed bar that comes back
  different is updated and logged, not versioned: the broker's price history is what it is, and
  the desk does not own it.
* `symbol_specs` gets one row per **change**, never an update (decision 5), because swap rates
  and stop levels move, and a backtest must use the spec in force at the time.
* `clock_checks` records every offset measurement that changed the verdict, so the offset any
  stored bar was converted with can be audited.
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, Float, Integer, MetaData, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.schemas import SCHEMA_BROKER
from app.modules.gex.models.db import UTCDateTime

__all__ = [
    "SCHEMA_BROKER",
    "Bar",
    "Base",
    "BasisRow",
    "ClockCheck",
    "RollRow",
    "SymbolSpec",
    "TickFile",
    "schema_ready",
]

_JSON = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA_BROKER)


class Bar(Base):
    """One closed bar. `ts` is the bar's **open** instant in UTC, converted from server time
    with `offset_s` (server minus UTC), which is stored per row so a conversion can be audited."""

    __tablename__ = "bars"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(UTCDateTime(), primary_key=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    tick_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The bar's spread in **points** as MT5 reports it (the lowest spread seen in the bar).
    spread: Mapped[int] = mapped_column(Integer, nullable=False)
    real_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    offset_s: Mapped[int] = mapped_column(Integer, nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)


class SymbolSpec(Base):
    """A contract spec as observed. A new row only when something in `spec` changed."""

    __tablename__ = "symbol_specs"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    observed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), primary_key=True)
    spec: Mapped[dict] = mapped_column(_JSON, nullable=False)


class TickFile(Base):
    """One closed UTC hour of ticks for one symbol, written to Parquet under `DATA_DIR`.

    `path` is relative to `DATA_DIR`, the same rule as `gex.snapshots.parquet_path`. An hour
    with no ticks (the market was closed) still gets a row with `rows = 0` and no file, so it
    is not fetched again."""

    __tablename__ = "tick_files"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    hour: Mapped[dt.datetime] = mapped_column(UTCDateTime(), primary_key=True)
    path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_s: Mapped[int] = mapped_column(Integer, nullable=False)
    written_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)


class ClockCheck(Base):
    """An offset measurement, against what the New York close convention predicts.

    Written when the verdict changes and at least hourly while measurements are possible, not
    once per poll. `measured_s` is `None` when no fresh tick was available to measure from."""

    __tablename__ = "clock_checks"

    checked_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), primary_key=True)
    measured_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_s: Mapped[int] = mapped_column(Integer, nullable=False)
    agrees: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)


class BasisRow(Base):
    """One basis observation (T145). Derived, recomputed in full by the basis job: nothing
    here is an observation in its own right, so replacing it loses nothing."""

    __tablename__ = "basis"

    desk_symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(4), primary_key=True)  # "1d" or "5m"
    #: The instant both prices are taken at: the desk bar's close, not its open.
    at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), primary_key=True)
    cfd_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    proxy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    desk_price: Mapped[float] = mapped_column(Float, nullable=False)
    cfd_price: Mapped[float] = mapped_column(Float, nullable=False)
    offset: Mapped[float] = mapped_column(Float, nullable=False)
    ratio: Mapped[float] = mapped_column(Float, nullable=False)
    #: The roll this observation's contract started at, or None before the first known roll.
    segment_start: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class RollRow(Base):
    """A futures roll in one CFD, found in its primary pair's basis (T145)."""

    __tablename__ = "rolls"

    cfd_symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: First observation on the new contract: a 5-minute instant when the desk has 5-minute
    #: bars across the roll, else the daily close.
    rolled_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), primary_key=True)
    #: When the persistence test could first pass. Anything acting "live" on a roll may only
    #: know it from here on.
    confirmed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    step: Mapped[float] = mapped_column(Float, nullable=False)  # change in log(cfd / desk)
    desk_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    resolution: Mapped[str] = mapped_column(String(4), nullable=False)  # "5m" or "1d"


# `broker.levels`, the GEX levels in CFD price (T145), is a view defined in migration
# `f7a8b9c0d1e2`: one row per `gex.gex_levels` row, translated with the newest basis at or before
# the snapshot's `captured_at`, never a later one. A change to it is a new migration.


def schema_ready(engine: sa.Engine) -> bool:
    """True when every broker table exists. The backend's `alembic upgrade head` creates them;
    the worker waits for that rather than creating anything itself."""
    inspector = sa.inspect(engine)
    schema = SCHEMA_BROKER if engine.dialect.name == "postgresql" else None
    return all(inspector.has_table(t.name, schema=schema) for t in Base.metadata.sorted_tables)
