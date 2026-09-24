"""Tests for `app.modules.gex.gex.backfill` -- the CLI that computes GEX levels for any snapshot lacking
them. Entirely offline, same SQLite + `tmp_path` pattern as `tests/test_gex_store.py`.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.gex.backfill import backfill, backfill_atm_iv
from app.modules.gex.gex.store import DEFAULT_FILTERS
from app.modules.gex.models.chain import ChainSnapshot, OptionContract, Underlying
from app.modules.gex.models.db import Base, GexByStrike, GexLevel, Snapshot
from app.modules.gex.storage.parquet import write_snapshot
from app.modules.gex.storage.repository import SnapshotRepository


def make_contract(symbol: str, **kw) -> OptionContract:
    return OptionContract.from_occ(symbol, **kw)


def make_snapshot(captured_at: dt.datetime) -> ChainSnapshot:
    contracts = [
        make_contract("SPY261218C00505000", open_interest=1500, iv=0.18, gamma=0.008),
        make_contract("SPY261218P00495000", open_interest=1200, iv=0.19, gamma=0.009),
    ]
    return ChainSnapshot(
        underlying=Underlying.SPY,
        spot=500.0,
        captured_at=captured_at,
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


def add_snapshot(tmp_path, session_factory, *, minute: int, parquet_path: str | None = None):
    """Index one snapshot, writing a real Parquet file unless `parquet_path` overrides it
    (used to simulate a broken/missing file)."""
    captured_at = dt.datetime(2026, 9, 4, 20, minute, 0, tzinfo=dt.UTC)
    snapshot = make_snapshot(captured_at)
    with session_factory() as session:
        if parquet_path is None:
            path = write_snapshot(snapshot, data_dir=tmp_path)
            row = SnapshotRepository(session).add(snapshot, path, is_eod=True)
        else:
            row = SnapshotRepository(session).add(snapshot, parquet_path, is_eod=True)
        return row.id


def _counts(session_factory) -> tuple[int, int]:
    with session_factory() as session:
        levels = session.execute(select(func.count()).select_from(GexLevel)).scalar_one()
        by_strike = session.execute(select(func.count()).select_from(GexByStrike)).scalar_one()
    return levels, by_strike


def test_backfill_computes_for_every_snapshot_lacking_levels(tmp_path, session_factory):
    add_snapshot(tmp_path, session_factory, minute=0)
    add_snapshot(tmp_path, session_factory, minute=15)

    summary = backfill(session_factory=session_factory, data_dir=tmp_path)

    assert summary == {
        "total": 2,
        "processed": 2,
        "failed": 0,
        "skipped_up_to_date": 0,
    }
    levels, _by_strike = _counts(session_factory)
    assert levels == 2 * len(DEFAULT_FILTERS)


def test_backfill_second_run_is_a_noop(tmp_path, session_factory):
    """Idempotency, demonstrated the way the T09 acceptance run does: run twice, row counts
    (and the summary) must be identical the second time."""
    add_snapshot(tmp_path, session_factory, minute=0)
    add_snapshot(tmp_path, session_factory, minute=15)

    first = backfill(session_factory=session_factory, data_dir=tmp_path)
    first_counts = _counts(session_factory)

    second = backfill(session_factory=session_factory, data_dir=tmp_path)
    second_counts = _counts(session_factory)

    assert first["processed"] == 2
    assert second == {"total": 2, "processed": 0, "failed": 0, "skipped_up_to_date": 2}
    assert first_counts == second_counts


def test_backfill_only_processes_snapshots_missing_levels(tmp_path, session_factory):
    id_a = add_snapshot(tmp_path, session_factory, minute=0)
    add_snapshot(tmp_path, session_factory, minute=15)

    from app.modules.gex.gex.store import compute_and_store

    compute_and_store(id_a, session_factory=session_factory, data_dir=tmp_path)

    summary = backfill(session_factory=session_factory, data_dir=tmp_path)
    assert summary == {"total": 2, "processed": 1, "failed": 0, "skipped_up_to_date": 1}


def test_backfill_skips_a_broken_snapshot_and_continues(tmp_path, session_factory):
    """One snapshot pointing at a Parquet file that doesn't exist must not stop the run --
    it's counted as failed and the other, good snapshot still gets processed."""
    add_snapshot(tmp_path, session_factory, minute=0, parquet_path="does/not/exist.parquet")
    add_snapshot(tmp_path, session_factory, minute=15)

    summary = backfill(session_factory=session_factory, data_dir=tmp_path)

    assert summary["total"] == 2
    assert summary["processed"] == 1
    assert summary["failed"] == 1


# --- --atm-iv (T124) ---------------------------------------------------------------------------


def test_backfill_atm_iv_fills_a_pre_t103_snapshot_and_touches_nothing_else(tmp_path, session_factory):
    """A snapshot indexed without its ATM vol (as every capture before T103 was) gets the same
    value `compute_and_store` would have stored; levels are left alone, and a second run finds
    nothing pending."""
    from app.modules.gex.gex.engine import to_frame
    from app.modules.gex.gex.report import iv_regime
    from tests.test_scan_api import _fixture_snapshot

    snapshot = _fixture_snapshot("SPY", "spy.json")
    with session_factory() as session:
        snapshot_id = SnapshotRepository(session).add(
            snapshot, write_snapshot(snapshot, data_dir=tmp_path), is_eod=True
        ).id
        assert session.get(Snapshot, snapshot_id).atm_iv is None

    summary = backfill_atm_iv(session_factory=session_factory)

    assert summary == {"pending": 1, "filled": 1, "still_null": 0, "failed": 0}
    expected = iv_regime(to_frame(snapshot), snapshot.spot)
    with session_factory() as session:
        row = session.get(Snapshot, snapshot_id)
        assert row.atm_iv == pytest.approx(expected.atm_iv)
        assert row.atm_iv_target_dte == expected.target_dte
    assert _counts(session_factory) == (0, 0), "--atm-iv must not compute levels"
    assert backfill_atm_iv(session_factory=session_factory)["pending"] == 0


def test_backfill_atm_iv_skips_a_missing_parquet_file_and_continues(tmp_path, session_factory):
    add_snapshot(tmp_path, session_factory, minute=1, parquet_path=str(tmp_path / "gone.parquet"))
    add_snapshot(tmp_path, session_factory, minute=2)

    summary = backfill_atm_iv(session_factory=session_factory)

    assert summary["pending"] == 2
    assert summary["failed"] == 1
    assert summary["filled"] + summary["still_null"] == 1
