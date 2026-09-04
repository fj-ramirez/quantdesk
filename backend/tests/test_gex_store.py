"""Tests for `app.gex.store.compute_and_store`.

Entirely offline (SQLite + a `tmp_path` Parquet root), same isolation pattern as
`tests/test_capture.py` and `tests/test_snapshot_repository.py`.

The centerpiece scenario replicates the exact situation the T09 brief calls out: a snapshot
captured after the close, where every 0DTE contract has already expired. `ZERO_DTE` must
persist with `None` walls/flip -- never `0.0` -- and zero `gex_by_strike` rows, while `ALL` and
`EX_ZERO_DTE` still carry real numbers from the live, longer-dated contract.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from app.gex.engine import ExpiryFilter
from app.gex.store import compute_and_store
from app.models.chain import ChainSnapshot, OptionContract, Underlying
from app.models.db import Base, GexByStrike, GexLevel, get_engine, get_sessionmaker
from app.storage.parquet import write_snapshot
from app.storage.repository import SnapshotRepository

# 2026-09-04 20:20 UTC == 16:20 America/New_York (EDT, UTC-4) -- after the PM-settlement
# instant (16:00 NY) of a same-day expiry, exactly the "EOD capture, everything 0DTE is
# already dead" situation the T09 brief verified against the live chain.
CAPTURED_AT = dt.datetime(2026, 9, 4, 20, 20, 0, tzinfo=dt.UTC)


def make_contract(symbol: str, **kw) -> OptionContract:
    return OptionContract.from_occ(symbol, **kw)


def make_snapshot() -> ChainSnapshot:
    """SPY snapshot: two 0DTE contracts (already expired as of `CAPTURED_AT`) and two live
    contracts a week out, so `ZERO_DTE` selects something and then correctly excludes all of
    it, while `ALL`/`EX_ZERO_DTE` have real, non-degenerate contributions.
    """
    contracts = [
        make_contract("SPY260904C00500000", open_interest=1000, iv=0.15, gamma=0.01, delta=0.5),
        make_contract("SPY260904P00500000", open_interest=800, iv=0.16, gamma=0.01, delta=-0.5),
        make_contract("SPY260911C00505000", open_interest=1500, iv=0.18, gamma=0.008, delta=0.4),
        make_contract("SPY260911P00495000", open_interest=1200, iv=0.19, gamma=0.009, delta=-0.4),
    ]
    return ChainSnapshot(
        underlying=Underlying.SPY,
        spot=500.0,
        captured_at=CAPTURED_AT,
        source="stub",
        delayed_minutes=15,
        contracts=contracts,
    )


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def snapshot_row(tmp_path, session_factory) -> int:
    """Write a real Parquet file and index it, returning the resulting `Snapshot.id`."""
    snapshot = make_snapshot()
    path = write_snapshot(snapshot, data_dir=tmp_path)
    with session_factory() as session:
        row = SnapshotRepository(session).add(snapshot, path, is_eod=True)
        return row.id


def _counts(session_factory) -> tuple[int, int]:
    with session_factory() as session:
        levels = session.execute(select(func.count()).select_from(GexLevel)).scalar_one()
        by_strike = session.execute(select(func.count()).select_from(GexByStrike)).scalar_one()
    return levels, by_strike


def test_compute_and_store_writes_one_level_row_per_default_filter(
    tmp_path, session_factory, snapshot_row
):
    rows = compute_and_store(snapshot_row, session_factory=session_factory, data_dir=tmp_path)
    assert {r.filter for r in rows} == {"ALL", "ZERO_DTE", "EX_ZERO_DTE"}

    with session_factory() as session:
        stored = (
            session.execute(select(GexLevel).where(GexLevel.snapshot_id == snapshot_row))
            .scalars()
            .all()
        )
    assert len(stored) == 3


def test_zero_dte_after_close_persists_nulls_not_zeros(tmp_path, session_factory, snapshot_row):
    """The exact scenario the T09 brief verified live: every 0DTE contract has expired by the
    16:20 ET EOD capture, so ZERO_DTE must read back with None walls/flip and net_gex either
    None or the engine's own explicit 0.0 -- never a fabricated wall at strike zero.
    """
    compute_and_store(snapshot_row, session_factory=session_factory, data_dir=tmp_path)

    with session_factory() as session:
        zero_dte = session.execute(
            select(GexLevel).where(
                GexLevel.snapshot_id == snapshot_row, GexLevel.filter == "ZERO_DTE"
            )
        ).scalar_one()
        by_strike_zero_dte = session.execute(
            select(func.count())
            .select_from(GexByStrike)
            .where(GexByStrike.snapshot_id == snapshot_row, GexByStrike.filter == "ZERO_DTE")
        ).scalar_one()

        all_filter = session.execute(
            select(GexLevel).where(GexLevel.snapshot_id == snapshot_row, GexLevel.filter == "ALL")
        ).scalar_one()

    assert zero_dte.call_wall is None
    assert zero_dte.put_wall is None
    assert zero_dte.call_wall_gex is None
    assert zero_dte.put_wall_gex is None
    assert zero_dte.max_abs_strike is None
    assert zero_dte.flip_point is None
    assert zero_dte.net_gex == pytest.approx(0.0)
    assert by_strike_zero_dte == 0

    # The live contracts (EX_ZERO_DTE) still show up under ALL -- levels genuinely exist here.
    assert all_filter.call_wall is not None
    assert all_filter.net_gex != 0.0


def test_compute_and_store_persists_extra_kept_fields(tmp_path, session_factory, snapshot_row):
    """`call_wall_gex` / `put_wall_gex` / `max_call_gex_strike` / `max_put_gex_strike` --
    the four extra KeyLevels fields T08's author flagged -- are persisted so a later read
    never needs to reopen the Parquet file just to get them."""
    compute_and_store(snapshot_row, session_factory=session_factory, data_dir=tmp_path)
    with session_factory() as session:
        all_filter = session.execute(
            select(GexLevel).where(GexLevel.snapshot_id == snapshot_row, GexLevel.filter == "ALL")
        ).scalar_one()
    assert all_filter.max_call_gex_strike is not None
    assert all_filter.max_put_gex_strike is not None
    # call_wall_gex/put_wall_gex accompany the (non-null) walls on a non-degenerate filter.
    assert all_filter.call_wall_gex is not None
    assert all_filter.put_wall_gex is not None


def test_compute_and_store_is_idempotent(tmp_path, session_factory, snapshot_row):
    """Running it twice for the same snapshot must replace, not accumulate -- the row counts
    (and the values) after a second call must equal the first."""
    compute_and_store(snapshot_row, session_factory=session_factory, data_dir=tmp_path)
    first_counts = _counts(session_factory)
    with session_factory() as session:
        first_values = {
            r.filter: (r.net_gex, r.call_wall, r.flip_point)
            for r in session.execute(
                select(GexLevel).where(GexLevel.snapshot_id == snapshot_row)
            ).scalars()
        }

    compute_and_store(snapshot_row, session_factory=session_factory, data_dir=tmp_path)
    second_counts = _counts(session_factory)
    with session_factory() as session:
        second_values = {
            r.filter: (r.net_gex, r.call_wall, r.flip_point)
            for r in session.execute(
                select(GexLevel).where(GexLevel.snapshot_id == snapshot_row)
            ).scalars()
        }

    assert first_counts == second_counts
    assert first_values == second_values


def test_compute_and_store_raises_for_missing_snapshot(session_factory):
    with pytest.raises(ValueError, match="no snapshot with id"):
        compute_and_store(999_999, session_factory=session_factory)


def test_compute_and_store_accepts_a_subset_of_filters(tmp_path, session_factory, snapshot_row):
    rows = compute_and_store(
        snapshot_row,
        session_factory=session_factory,
        data_dir=tmp_path,
        filters=(ExpiryFilter.ALL,),
    )
    assert [r.filter for r in rows] == ["ALL"]
    with session_factory() as session:
        stored = (
            session.execute(select(GexLevel).where(GexLevel.snapshot_id == snapshot_row))
            .scalars()
            .all()
        )
    assert len(stored) == 1
