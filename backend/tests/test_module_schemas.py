"""T76: every module table carries its schema, and SQLite still works anyway.

Two things are being held here, and they pull against each other.

The models must say `gex.snapshots`, because that is what makes schema placement declarative
-- a table added in T90 lands in the right namespace because `Base.metadata` says so, not
because someone remembered a `__table_args__`. But the test suite is ~30 files that each build
a SQLite engine and call `Base.metadata.create_all`, and SQLite has no schemas at all.

`app.core.db.get_engine` reconciles them with `schema_translate_map`, and the reconciliation is
invisible -- which is exactly why it needs a test of its own. Nothing else in the suite would
notice if it silently stopped applying; the 990 tests would go on passing right up until a
`CREATE TABLE gex.snapshots` reached a real SQLite file.

The Postgres half of T76 -- the `ALTER TABLE ... SET SCHEMA` move, the `quantdesk_ro` grants
and the default privileges -- is not here. It was verified against the live database on
2026-09-19 (both directions, with rows in the tables, plus a from-scratch upgrade and a
role that could SELECT and could not write); see the plan file's Result section. Re-testing it
here would need a Postgres the offline suite does not have.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import inspect, select

from app.core.db import connect_args_for, get_engine, get_sessionmaker
from app.core.schemas import SCHEMA_GEX, SCHEMA_RESEARCH, SCHEMA_TERMINAL, SCHEMAS
from app.modules.gex.models.db import Base, DailyBar, GexLevel, Snapshot


def test_every_gex_table_declares_the_gex_schema():
    """The declarative half. A new model inherits this; it cannot forget it."""
    assert Base.metadata.schema == SCHEMA_GEX
    for table in Base.metadata.tables.values():
        assert table.schema == SCHEMA_GEX, f"{table.name} escaped the gex schema"

    # Keyed by qualified name, which is what makes a cross-module name collision impossible
    # once research and terminal have tables of their own.
    assert "gex.snapshots" in Base.metadata.tables
    assert "snapshots" not in Base.metadata.tables


def test_foreign_keys_resolve_inside_the_schema_without_being_qualified():
    """`ForeignKey("snapshots.id")` in the models must still mean `gex.snapshots.id`.

    This is why T76 needed no edit to the seven existing FK declarations, and why a new one
    needs no prefix. If SQLAlchemy ever stopped resolving string targets against the
    metadata's default schema, the DDL would point at a `public.snapshots` that does not
    exist.
    """
    fk = next(iter(GexLevel.__table__.c.snapshot_id.foreign_keys))
    assert fk.column.table.schema == SCHEMA_GEX
    assert fk.column.table.fullname == "gex.snapshots"


def test_schema_constants_are_distinct_and_complete():
    assert SCHEMAS == (SCHEMA_GEX, SCHEMA_RESEARCH, SCHEMA_TERMINAL)
    assert len(set(SCHEMAS)) == len(SCHEMAS)


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    yield engine
    engine.dispose()


def test_sqlite_engines_translate_every_schema_away(sqlite_engine):
    """The mechanism, named. Not just `gex` -- research (T77) and terminal (T79) too."""
    translate = sqlite_engine.get_execution_options()["schema_translate_map"]
    assert translate == dict.fromkeys(SCHEMAS)


def test_create_all_on_sqlite_produces_unqualified_tables(sqlite_engine):
    """The regression this guards: `CREATE TABLE gex.snapshots` on SQLite is a hard error."""
    Base.metadata.create_all(sqlite_engine)

    tables = set(inspect(sqlite_engine).get_table_names())
    assert {"snapshots", "gex_levels", "daily_bars", "decisions"} <= tables
    # Nothing called `gex.something` and nothing attached under a schema name.
    assert not any("." in name for name in tables)
    assert inspect(sqlite_engine).get_schema_names() == ["main"]


def test_orm_round_trip_over_the_translated_schema(sqlite_engine):
    """A write and a read through the ORM, including a foreign key across two schema'd tables.

    Belt and braces over the DDL test above: translation applying to `CREATE TABLE` but not to
    `INSERT`/`SELECT` would leave the tables looking right and every query failing.
    """
    Base.metadata.create_all(sqlite_engine)
    factory = get_sessionmaker(sqlite_engine)

    with factory() as session:
        snapshot = Snapshot(
            underlying="SPX",
            captured_at=dt.datetime(2026, 9, 19, 20, 5, tzinfo=dt.UTC),
            source="test",
            spot=7709.52,
            contract_count=1,
            parquet_path="a.parquet",
            is_eod=True,
        )
        session.add(snapshot)
        session.flush()
        session.add(
            GexLevel(
                snapshot_id=snapshot.id,
                filter="all",
                net_gex=1.0,
                computed_at=dt.datetime(2026, 9, 19, 20, 6, tzinfo=dt.UTC),
            )
        )
        session.add(
            DailyBar(
                symbol="SPY",
                date=dt.date(2026, 9, 18),
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100,
                source="test",
            )
        )
        session.commit()

    with factory() as session:
        joined = session.execute(
            select(Snapshot.underlying, GexLevel.net_gex).join(
                GexLevel, GexLevel.snapshot_id == Snapshot.id
            )
        ).all()
        assert joined == [("SPX", 1.0)]
        assert session.scalar(select(DailyBar.close)) == 1.5


def test_postgres_engines_keep_their_schemas(monkeypatch):
    """The other side of the guard: translating on Postgres would silently undo the migration.

    No connection is made -- `create_engine` is lazy, and the execution options are set at
    construction time, so this asserts on the real code path without needing a database.
    """
    engine = get_engine("postgresql+psycopg://u:p@localhost:5432/nonexistent")
    assert "schema_translate_map" not in engine.get_execution_options()


def test_postgres_connections_pin_the_search_path():
    """`$user` must not be allowed to resolve to the `gex` schema.

    This project's database role is called `gex` and T76 created a schema of the same name, so
    the default `"$user", public` search path silently made `gex` the default schema. That is
    what made `alembic --autogenerate` emit a migration recreating all seven tables; the same
    ambiguity would make unqualified raw SQL behave differently on a host whose database user
    is named anything else.
    """
    args = connect_args_for("postgresql+psycopg://u:p@localhost:5432/db")
    assert args["options"] == "-csearch_path=public"
    assert args["connect_timeout"] == 5

    # SQLite accepts neither keyword -- passing them would fail `create_engine`, which is why
    # every test in this file can build an engine at all.
    assert connect_args_for("sqlite:///x.db") == {}
