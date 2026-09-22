"""Tests for `app.modules.gex.gex.store.compute_and_store`.

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

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.gex.engine import ExpiryFilter
from app.modules.gex.gex.store import compute_and_store
from app.modules.gex.models.chain import ChainSnapshot, OptionContract, Underlying
from app.modules.gex.models.db import Base, GexByExpiry, GexByStrike, GexLevel
from app.modules.gex.storage.parquet import write_snapshot
from app.modules.gex.storage.repository import SnapshotRepository

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
    16:20 ET EOD capture, so ZERO_DTE must read back with None walls/flip and a None
    net_gex -- never a fabricated wall at strike zero, and never a zero that reads as a
    measured flat book (T100).
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
    assert zero_dte.net_gex is None
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


# --- T87: the in-memory shortcut ------------------------------------------------------------


def _oi_mixed_snapshot() -> ChainSnapshot:
    """A chain carrying both kinds of "no open interest" (invariant 3).

    `None` means the vendor did not report a figure and the contract is excluded from every
    aggregate; `0` means it reported zero and the contract is included, contributing nothing.
    They are different facts, the Parquet round trip is deliberately careful to keep them
    apart (`read_snapshot` uses `to_pylist()` so a null comes back as `None` and not `NaN`),
    and this is the chain the equivalence test below runs through both paths.
    """
    return ChainSnapshot(
        underlying=Underlying.SPY,
        spot=500.0,
        captured_at=CAPTURED_AT,
        source="stub",
        delayed_minutes=15,
        contracts=[
            make_contract("SPY260911C00505000", open_interest=1500, iv=0.18, gamma=0.008),
            make_contract("SPY260911P00495000", open_interest=1200, iv=0.19, gamma=0.009),
            make_contract("SPY260911C00510000", open_interest=None, iv=0.20, gamma=0.007),
            make_contract("SPY260911P00490000", open_interest=0, iv=0.21, gamma=0.006),
        ],
    )


def _persisted(session_factory, snapshot_id: int) -> tuple[list[tuple], list[tuple]]:
    """Every stored value for a snapshot, as comparable tuples.

    `id` and `computed_at` are excluded deliberately: the row identity and the clock differ
    between two runs by construction, and neither is a computed value. Everything else is.
    """
    with session_factory() as session:
        levels = [
            (
                row.filter,
                row.net_gex,
                row.call_wall,
                row.call_wall_gex,
                row.put_wall,
                row.put_wall_gex,
                row.max_abs_strike,
                row.max_call_gex_strike,
                row.max_put_gex_strike,
                row.flip_point,
                row.spot,
            )
            for row in session.execute(
                select(GexLevel).where(GexLevel.snapshot_id == snapshot_id).order_by(GexLevel.filter)
            )
            .scalars()
            .all()
        ]
        by_strike = [
            (row.filter, row.strike, row.call_gex, row.put_gex, row.net_gex)
            for row in session.execute(
                select(GexByStrike)
                .where(GexByStrike.snapshot_id == snapshot_id)
                .order_by(GexByStrike.filter, GexByStrike.strike)
            )
            .scalars()
            .all()
        ]
    return levels, by_strike


def test_in_memory_snapshot_produces_identical_rows_to_reading_the_file(tmp_path, session_factory):
    """T87's load-bearing equivalence, asserted rather than reasoned about.

    The in-memory object is the *input* to the Parquet round trip, so it should be identical
    coming back -- but "should" is not good enough for the distinction invariant 3 exists to
    protect, and a silent `None` -> `0` on either side would change `net_gex` by a real amount
    while every row still looked plausible.
    """
    snapshot = _oi_mixed_snapshot()
    path = write_snapshot(snapshot, data_dir=tmp_path)
    with session_factory() as session:
        snapshot_id = SnapshotRepository(session).add(snapshot, path, is_eod=True).id

    compute_and_store(snapshot_id, session_factory=session_factory, data_dir=tmp_path)
    from_disk = _persisted(session_factory, snapshot_id)

    # Idempotent by construction (delete-then-insert), so the second call replaces the first
    # rather than accumulating -- which is what makes comparing the two states meaningful.
    compute_and_store(
        snapshot_id, session_factory=session_factory, data_dir=tmp_path, snapshot=snapshot
    )
    from_memory = _persisted(session_factory, snapshot_id)

    assert from_memory == from_disk
    # And the comparison is not vacuous: the chain really does produce numbers, and the
    # contract with `open_interest=0` is carried into the per-strike rows while the one with
    # `None` is not -- the two "no open interest" cases staying apart across both paths.
    level_rows, by_strike_rows = from_memory
    net_gex = {row[0]: row[1] for row in level_rows}
    assert net_gex["ALL"] not in (None, 0.0)
    strikes = {row[1] for row in by_strike_rows if row[0] == "ALL"}
    assert 490.0 in strikes
    assert 510.0 not in strikes


def test_in_memory_snapshot_still_requires_the_snapshot_row(tmp_path, session_factory):
    """Passing the chain skips the file read, never the index row: the levels are keyed to
    `snapshot_id`, and a caller handing over a chain for an id that does not exist is a bug."""
    with pytest.raises(ValueError, match="no snapshot with id=999"):
        compute_and_store(
            999,
            session_factory=session_factory,
            data_dir=tmp_path,
            snapshot=_oi_mixed_snapshot(),
        )


def test_t101_horizons_and_expiry_rollup_are_persisted(tmp_path, session_factory, snapshot_row):
    """T101: the expiry dimension reaches Postgres. `engine.by_expiry` has computed the term
    structure since T08 and it was discarded at persist, so no question about a *past* term
    structure could be answered at any price short of reopening the chain."""
    compute_and_store(snapshot_row, session_factory=session_factory, data_dir=tmp_path)

    with session_factory() as session:
        strikes = session.execute(
            select(GexByStrike).where(
                GexByStrike.snapshot_id == snapshot_row, GexByStrike.filter == "ALL"
            )
        ).scalars().all()
        expiries = session.execute(
            select(GexByExpiry)
            .where(GexByExpiry.snapshot_id == snapshot_row, GexByExpiry.filter == "ALL")
            .order_by(GexByExpiry.expiry)
        ).scalars().all()

    assert strikes, "no strike rows persisted"
    for row in strikes:
        parts = [
            row.net_gex_0dte,
            row.net_gex_this_week,
            row.net_gex_next_30d,
            row.net_gex_beyond_30d,
        ]
        assert all(p is not None for p in parts)
        assert sum(parts) == pytest.approx(row.net_gex, abs=1e-6)

    assert expiries, "no expiry rows persisted"
    assert [e.expiry for e in expiries] == sorted(e.expiry for e in expiries)
    # The term structure sums to the same book the per-strike rollup describes.
    assert sum(e.net_gex for e in expiries) == pytest.approx(
        sum(s.net_gex for s in strikes), abs=1e-6
    )


def test_t101_recompute_replaces_rather_than_accumulates(tmp_path, session_factory, snapshot_row):
    """`--recompute` is how an engine change reaches stored rows, so it runs against snapshots
    that already have them. `compute_and_store` deletes the `(snapshot, filter)` slice first;
    if it did not, a second pass would double every row."""
    compute_and_store(snapshot_row, session_factory=session_factory, data_dir=tmp_path)
    with session_factory() as session:
        first = session.execute(
            select(func.count()).select_from(GexByExpiry).where(
                GexByExpiry.snapshot_id == snapshot_row
            )
        ).scalar_one()

    compute_and_store(snapshot_row, session_factory=session_factory, data_dir=tmp_path)
    with session_factory() as session:
        second = session.execute(
            select(func.count()).select_from(GexByExpiry).where(
                GexByExpiry.snapshot_id == snapshot_row
            )
        ).scalar_one()

    assert first > 0
    assert second == first


def test_t102_session_date_is_derived_at_capture(tmp_path, session_factory):
    """T102: the snapshot records which *session* its chain came from, not just when it was
    captured. Snapshot 178 in production is a Sunday capture of Friday's post-opex book; this
    reproduces that shape and asserts the row says Friday."""
    sunday = dt.datetime(2026, 9, 20, 15, 10, 8, tzinfo=dt.UTC)  # a Sunday, 11:10 ET
    chain = ChainSnapshot(
        underlying=Underlying.SPY,
        spot=100.0,
        captured_at=sunday,
        source="synthetic",
        delayed_minutes=15,
        contracts=(),
    )
    path = write_snapshot(chain, data_dir=tmp_path)
    with session_factory() as session:
        row = SnapshotRepository(session).add(chain, path, is_eod=True)
        assert row.session_date == dt.date(2026, 9, 18), (
            "a Sunday capture holds Friday's book; grouping by captured_at::date would invent "
            "a Sunday session that never traded"
        )
        # `is_eod` keeps its own meaning -- the two answer different questions.
        assert row.is_eod is True
        assert row.captured_at.date() == dt.date(2026, 9, 20)
