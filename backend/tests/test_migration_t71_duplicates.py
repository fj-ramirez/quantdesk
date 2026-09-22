"""Test for the duplicate-resolution half of T71's migration.

The migration was run live against the real Postgres database on 2026-09-11, both directions,
but that database happened to contain **no** duplicate `(underlying, captured_at)` pairs -- so
the branch that actually deletes rows went unexercised there. Since that branch destroys data
and runs exactly once per database, it is checked here against a seeded SQLite database
instead, where duplicates can be manufactured.

Only `_resolve_duplicates` is under test. The surrounding DDL (`create_unique_constraint`,
`drop_index`) is Postgres-flavoured and was verified live; re-testing it here would test
SQLAlchemy's SQLite dialect, not this project.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.models.db import Base, GexByStrike, GexLevel, Snapshot

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "c7a1e93b5d02_snapshot_content_hash_and_uniqueness.py"
)


def _load_migration():
    """Import the revision module directly -- `alembic/versions` is not a package."""
    spec = importlib.util.spec_from_file_location("t71_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pre_migration_snapshots_ddl() -> str:
    """`CREATE TABLE snapshots` **without** the uniqueness this migration adds.

    Derived from the live model rather than written out by hand. The hand-written version broke
    twice in one day -- T102 added `session_date`, T103 added six `atm_iv*` columns -- because
    the rows below are inserted through the ORM, so any column the model has and the DDL lacks
    fails the insert with "table main.snapshots has no column named ...". The comment here
    promised the real fix if it broke a third time; this is it.

    What is historical is the **constraint**, not the column list. So: copy the table into a
    throwaway `MetaData` with no schema, discard the unique constraint the migration exists to
    add, and compile the rest for SQLite. The fixture tracks the model automatically and still
    cannot hold the uniqueness that would stop it holding the duplicates this test is about.
    """
    from sqlalchemy import MetaData, UniqueConstraint
    from sqlalchemy.dialects import sqlite
    from sqlalchemy.schema import CreateTable

    scratch = MetaData()
    table = Snapshot.__table__.to_metadata(scratch, schema=None)
    for constraint in list(table.constraints):
        if isinstance(constraint, UniqueConstraint):
            table.constraints.discard(constraint)
    for index in list(table.indexes):
        if index.unique:
            table.indexes.discard(index)
    return str(CreateTable(table).compile(dialect=sqlite.dialect()))


@pytest.fixture
def seeded(tmp_path):
    """Three snapshots for one instant (two of them duplicates) plus one distinct instant,
    with level and strike children hanging off the rows that will be deleted."""
    engine = get_engine(f"sqlite:///{tmp_path / 'dupes.db'}")
    with engine.begin() as connection:
        connection.execute(text(_pre_migration_snapshots_ddl()))
    # `checkfirst` leaves the hand-built `snapshots` alone and creates only the child tables.
    Base.metadata.create_all(engine, checkfirst=True)
    factory = get_sessionmaker(engine)
    instant = dt.datetime(2026, 9, 4, 20, 20, tzinfo=dt.UTC)

    with factory() as session:
        rows = [
            Snapshot(
                underlying="SPX",
                captured_at=instant,
                source="cboe",
                spot=6500.0,
                contract_count=10,
                parquet_path=f"chains/SPX/2026/09/dup{n}.parquet",
                is_eod=True,
            )
            for n in range(3)
        ]
        rows.append(
            Snapshot(
                underlying="SPX",
                captured_at=instant + dt.timedelta(minutes=15),
                source="cboe",
                spot=6501.0,
                contract_count=10,
                parquet_path="chains/SPX/2026/09/unique.parquet",
                is_eod=False,
            )
        )
        session.add_all(rows)
        session.commit()
        for row in rows:
            session.add(
                GexLevel(snapshot_id=row.id, filter="ALL", net_gex=1.0, computed_at=instant)
            )
            session.add(
                GexByStrike(
                    snapshot_id=row.id,
                    filter="ALL",
                    strike=6500.0,
                    call_gex=1.0,
                    put_gex=-1.0,
                    net_gex=0.0,
                )
            )
        session.commit()
        ids = [row.id for row in rows]

    yield factory, ids
    engine.dispose()


def test_resolve_duplicates_keeps_the_earliest_and_deletes_the_rest(seeded, monkeypatch, capsys):
    factory, ids = seeded
    migration = _load_migration()

    with factory() as session:
        monkeypatch.setattr(migration.op, "get_bind", lambda: session.connection())
        migration._resolve_duplicates()
        session.commit()

    with factory() as session:
        surviving = session.execute(select(Snapshot.id).order_by(Snapshot.id)).scalars().all()

    # ids[0] is the earliest of the three duplicates and is kept; ids[1] and ids[2] go;
    # ids[3] is a different instant and is untouched.
    assert surviving == [ids[0], ids[3]]

    printed = capsys.readouterr().out
    assert "keeping id=" in printed
    assert "removed 2 duplicate snapshot rows" in printed


def test_resolve_duplicates_deletes_the_children_of_removed_rows(seeded, monkeypatch):
    """Orphaned `gex_levels` / `gex_by_strike` rows would outlive their snapshot and break the
    foreign key the read API joins on."""
    factory, ids = seeded
    migration = _load_migration()

    with factory() as session:
        monkeypatch.setattr(migration.op, "get_bind", lambda: session.connection())
        migration._resolve_duplicates()
        session.commit()

    with factory() as session:
        levels = session.execute(select(GexLevel.snapshot_id)).scalars().all()
        strikes = session.execute(select(GexByStrike.snapshot_id)).scalars().all()

    assert sorted(levels) == [ids[0], ids[3]]
    assert sorted(strikes) == [ids[0], ids[3]]


def test_resolve_duplicates_is_a_noop_on_a_clean_table(seeded, monkeypatch, capsys):
    """Runs twice: the second pass must find nothing and delete nothing -- the state the real
    Postgres database was already in when this shipped."""
    factory, ids = seeded
    migration = _load_migration()

    for _ in range(2):
        with factory() as session:
            monkeypatch.setattr(migration.op, "get_bind", lambda: session.connection())
            migration._resolve_duplicates()
            session.commit()

    assert "no duplicate" in capsys.readouterr().out
    with factory() as session:
        assert session.execute(select(Snapshot.id).order_by(Snapshot.id)).scalars().all() == [
            ids[0],
            ids[3],
        ]


def test_resolve_duplicates_leaves_parquet_paths_intact_on_the_kept_row(seeded, monkeypatch):
    """The kept row must still point at its own file -- the cleanup never rewrites paths, and
    the deleted rows' files are deliberately left on disk rather than chased down."""
    factory, ids = seeded
    migration = _load_migration()

    with factory() as session:
        monkeypatch.setattr(migration.op, "get_bind", lambda: session.connection())
        migration._resolve_duplicates()
        session.commit()

    with factory() as session:
        kept = session.get(Snapshot, ids[0])
        assert kept.parquet_path == "chains/SPX/2026/09/dup0.parquet"


def test_seeded_table_really_did_violate_the_constraint(seeded):
    """Guards the fixture itself: if this stopped producing duplicates, every test above would
    pass vacuously."""
    factory, _ = seeded
    with factory() as session:
        violations = session.execute(
            text(
                "SELECT COUNT(*) FROM (SELECT 1 FROM snapshots "
                "GROUP BY underlying, captured_at HAVING COUNT(*) > 1) v"
            )
        ).scalar()
    assert violations == 1
