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
    "LEVELS_VIEW_SQL",
    "SCHEMA_BROKER",
    "Bar",
    "Base",
    "BasisRow",
    "ClockCheck",
    "RollRow",
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


#: GEX levels in CFD price (T145). One row per `gex.gex_levels` row, translated with the
#: newest basis observation at or before the snapshot's `captured_at`, never a later one.
#: `roll_after_basis` is true when a roll falls between that basis and the capture: the
#: translation then crosses contracts and is wrong by about the roll step. It uses rolls as
#: confirmed by now, so for the last few observations before a fresh roll it is hindsight.
#: A view, not a table, so it is always as current as the levels and the basis.
LEVELS_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {SCHEMA_BROKER}.levels AS
SELECT s.id AS snapshot_id, s.underlying AS desk_symbol, s.captured_at, l.filter,
       b.cfd_symbol, b.method, b.proxy, b.kind AS basis_kind, b.at AS basis_at,
       s.captured_at - b.at AS basis_age, b."offset", b.ratio,
       l.spot AS desk_spot,
       CASE WHEN b.method = 'offset' THEN l.spot + b."offset" ELSE l.spot * b.ratio END AS spot,
       CASE WHEN b.method = 'offset' THEN l.call_wall + b."offset" ELSE l.call_wall * b.ratio END
           AS call_wall,
       CASE WHEN b.method = 'offset' THEN l.put_wall + b."offset" ELSE l.put_wall * b.ratio END
           AS put_wall,
       CASE WHEN b.method = 'offset' THEN l.flip_point + b."offset" ELSE l.flip_point * b.ratio END
           AS flip_point,
       CASE WHEN b.method = 'offset' THEN l.max_abs_strike + b."offset"
            ELSE l.max_abs_strike * b.ratio END AS max_abs_strike,
       l.net_gex, l.call_wall_gex, l.put_wall_gex,
       EXISTS (SELECT 1 FROM {SCHEMA_BROKER}.rolls r WHERE r.cfd_symbol = b.cfd_symbol
               AND r.rolled_at > b.at AND r.rolled_at <= s.captured_at) AS roll_after_basis
FROM gex.gex_levels l
JOIN gex.snapshots s ON s.id = l.snapshot_id
JOIN LATERAL (
    SELECT * FROM {SCHEMA_BROKER}.basis b
    WHERE b.desk_symbol = s.underlying AND b.at <= s.captured_at
    ORDER BY b.at DESC LIMIT 1
) b ON true
"""


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
        conn.execute(sa.text(LEVELS_VIEW_SQL))
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
