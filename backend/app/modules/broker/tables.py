"""The broker module's tables, in the `broker` schema (T144, plans/charter-mt5/README.md).

**Created by the `broker-ingest` worker, not by Alembic, while this is an experiment**
(decision 10). A migration on the experiment branch would leave the shared database at a
revision `main` has never seen, and `main`'s next `alembic upgrade head` would fail at boot.
:func:`ensure_schema` is idempotent: `CREATE SCHEMA IF NOT EXISTS`, `create_all`, and the
read-only role's grants. If the experiment merges, these tables become a migration in the same
PR, and `broker` joins `app.core.schemas.SCHEMAS`. `DROP SCHEMA broker CASCADE` undoes all of it.

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
import logging

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, Float, Integer, MetaData, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ro_role import RO_ROLE
from app.modules.gex.models.db import UTCDateTime

__all__ = [
    "SCHEMA_BROKER",
    "Bar",
    "Base",
    "ClockCheck",
    "SymbolSpec",
    "TickFile",
    "ensure_schema",
]

logger = logging.getLogger("app.modules.broker.tables")

#: Not in `app.core.schemas` yet, on purpose: see decision 10 and the module docstring.
SCHEMA_BROKER = "broker"

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


def ensure_schema(engine: sa.Engine) -> None:
    """Create `broker.` and its tables if missing, and let the read-only role read them.

    On SQLite (tests) there are no schemas or roles; the engine's `schema_translate_map` maps
    `broker` away and only `create_all` runs."""
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_BROKER}"))
    Base.metadata.create_all(engine)
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": RO_ROLE}
        ).first()
        if not exists:
            logger.warning("role %s does not exist; broker tables are not readable by MCP", RO_ROLE)
            return
        role = f'"{RO_ROLE}"'
        conn.execute(sa.text(f"GRANT USAGE ON SCHEMA {SCHEMA_BROKER} TO {role}"))
        conn.execute(sa.text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA_BROKER} TO {role}"))
        conn.execute(
            sa.text(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA_BROKER} GRANT SELECT ON TABLES TO {role}"
            )
        )
