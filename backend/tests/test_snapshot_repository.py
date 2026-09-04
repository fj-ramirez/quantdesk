"""Tests for `Snapshot` / `UTCDateTime` / `SnapshotRepository` against a SQLite URL.

SQLite is used here on purpose (per T04): the schema is plain SQLAlchemy core types with no
Postgres-specific type, so the same model code and the same repository must behave
identically against SQLite in CI and Postgres in production. The tz-aware round trip gets its
own explicit tests because SQLite -- unlike Postgres's TIMESTAMPTZ -- has no timezone-aware
storage at all and will silently hand back a naive datetime unless `UTCDateTime` compensates;
that is exactly the kind of gap that would otherwise only be discovered against production
Postgres.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from app.models.chain import ChainSnapshot, Underlying
from app.models.db import Base, Snapshot, get_engine, get_sessionmaker
from app.storage.repository import SnapshotRepository


@pytest.fixture
def session(tmp_path) -> Session:
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    with factory() as s:
        yield s
    engine.dispose()


def make_snapshot(**kw) -> ChainSnapshot:
    base = {
        "underlying": Underlying.SPX,
        "spot": 7709.52,
        "captured_at": dt.datetime(2026, 9, 4, 18, 5, 33, tzinfo=dt.UTC),
        "source": "cboe",
        "delayed_minutes": 15,
        "contracts": (),
    }
    return ChainSnapshot(**(base | kw))


def test_add_persists_row_and_returns_it_with_id(session: Session):
    repo = SnapshotRepository(session)
    snap = make_snapshot()
    row = repo.add(snap, "chains/SPX/2026/09/20260904T180533000000Z.parquet", is_eod=True)

    assert row.id is not None
    assert row.underlying == "SPX"
    assert row.source == "cboe"
    assert row.spot == pytest.approx(7709.52)
    assert row.contract_count == 0
    assert row.parquet_path == "chains/SPX/2026/09/20260904T180533000000Z.parquet"
    assert row.is_eod is True


def test_add_normalizes_windows_backslash_parquet_path(session: Session):
    """`Path(...).as_posix()` keeps the stored path portable across OSes; a Windows-produced
    path must not end up with backslashes baked into the index."""
    repo = SnapshotRepository(session)
    row = repo.add(make_snapshot(), r"chains\SPX\2026\09\x.parquet", is_eod=False)
    assert "\\" not in row.parquet_path
    assert row.parquet_path == "chains/SPX/2026/09/x.parquet"


def test_captured_at_round_trips_tz_aware_utc_through_sqlite(session: Session):
    """The whole point of `UTCDateTime`: SQLite has no tz-aware column type, so this would
    come back naive without it."""
    captured_at = dt.datetime(2026, 9, 4, 14, 5, 33, 123456, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
    repo = SnapshotRepository(session)
    row = repo.add(make_snapshot(captured_at=captured_at), "p.parquet", is_eod=False)
    session.expire_all()  # force a real reload from SQLite, not the in-memory ORM object

    reloaded = session.get(Snapshot, row.id)
    assert reloaded.captured_at.tzinfo is not None
    assert reloaded.captured_at == captured_at.astimezone(dt.UTC)
    assert reloaded.captured_at.utcoffset() == dt.timedelta(0)


def test_utc_datetime_rejects_naive_input(session: Session):
    naive = dt.datetime(2026, 9, 4, 18, 5, 33)  # noqa: DTZ001
    row = Snapshot(
        underlying="SPX",
        captured_at=naive,
        source="cboe",
        spot=1.0,
        contract_count=0,
        parquet_path="p.parquet",
        is_eod=False,
    )
    session.add(row)
    # SQLAlchemy wraps the TypeDecorator's ValueError in a StatementError at flush time; the
    # original message survives in its args, which is what matters here.
    with pytest.raises(StatementError, match="timezone-aware"):
        session.commit()


def test_latest_returns_none_when_empty(session: Session):
    assert SnapshotRepository(session).latest("SPX") is None


def test_latest_returns_most_recent_by_captured_at_per_underlying(session: Session):
    repo = SnapshotRepository(session)
    t0 = dt.datetime(2026, 9, 4, 16, 0, 0, tzinfo=dt.UTC)
    for i, minutes in enumerate([0, 30, 15]):
        repo.add(
            make_snapshot(captured_at=t0 + dt.timedelta(minutes=minutes)),
            f"spx-{i}.parquet",
            is_eod=False,
        )
    repo.add(make_snapshot(underlying=Underlying.SPY, captured_at=t0), "spy-0.parquet", is_eod=False)

    latest_spx = repo.latest("SPX")
    assert latest_spx.parquet_path == "spx-1.parquet"  # the +30min one, not insertion order
    assert repo.latest("SPY").parquet_path == "spy-0.parquet"
    assert repo.latest("QQQ") is None


def test_list_filters_by_underlying_range_and_eod(session: Session):
    repo = SnapshotRepository(session)
    t0 = dt.datetime(2026, 9, 4, 16, 0, 0, tzinfo=dt.UTC)
    times = [t0, t0 + dt.timedelta(minutes=15), t0 + dt.timedelta(minutes=30)]
    for i, t in enumerate(times):
        repo.add(make_snapshot(captured_at=t), f"spx-{i}.parquet", is_eod=(i == 2))
    repo.add(make_snapshot(underlying=Underlying.QQQ, captured_at=t0), "qqq-0.parquet", is_eod=True)

    all_spx = repo.list("SPX")
    assert [r.parquet_path for r in all_spx] == ["spx-0.parquet", "spx-1.parquet", "spx-2.parquet"]

    windowed = repo.list("SPX", start=times[1], end=times[1])
    assert [r.parquet_path for r in windowed] == ["spx-1.parquet"]

    eod_only = repo.list("SPX", eod_only=True)
    assert [r.parquet_path for r in eod_only] == ["spx-2.parquet"]

    assert repo.list("QQQ") != []
    assert repo.list("SPX") != repo.list("QQQ")


def test_list_returns_empty_for_unknown_underlying(session: Session):
    assert SnapshotRepository(session).list("SPX") == []
