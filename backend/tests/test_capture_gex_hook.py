"""Tests for the T09 seam in `app.jobs.capture.capture_snapshot`: GEX levels are computed and
stored right after a successful persist, and a failure there must never flip `result.ok` back
to `False` -- the raw Parquet capture is durable and already returned as a success by the time
this hook runs.

Deliberately its own file rather than an addition to `tests/test_capture.py`: T09 only touches
the seam `capture.py` already marks, and another agent may be independently reviewing/patching
`app/jobs/` and `app/storage/` in a parallel worktree -- keeping this in a separate file avoids
adding merge-conflict surface to a file T09 doesn't otherwise need to change.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest
from sqlalchemy import select

from app.gex.store import DEFAULT_FILTERS
from app.jobs.capture import capture_snapshot
from app.models.chain import ChainSnapshot, OptionContract, Underlying
from app.models.db import Base, GexLevel, get_engine, get_sessionmaker


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def make_contract(symbol: str, **kw) -> OptionContract:
    return OptionContract.from_occ(symbol, **kw)


def make_snapshot() -> ChainSnapshot:
    return ChainSnapshot(
        underlying=Underlying.SPY,
        spot=500.0,
        captured_at=dt.datetime(2026, 9, 4, 20, 20, 0, tzinfo=dt.UTC),
        source="stub",
        delayed_minutes=15,
        contracts=[
            make_contract("SPY261218C00505000", open_interest=1000, iv=0.2, gamma=0.01),
            make_contract("SPY261218P00495000", open_interest=900, iv=0.2, gamma=0.01),
        ],
    )


class StubProvider:
    """Same minimal duck-typed stub `tests/test_capture.py` uses."""

    name = "stub"
    delayed_minutes = 15

    def __init__(self, snapshot: ChainSnapshot):
        self._snapshot = snapshot

    async def fetch_chain(self, underlying: str) -> ChainSnapshot:
        return self._snapshot


async def test_successful_capture_writes_gex_levels_for_default_filters(tmp_path, session_factory):
    result = await capture_snapshot(
        "SPY",
        is_eod=True,
        provider=StubProvider(make_snapshot()),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    assert result.ok is True

    with session_factory() as session:
        rows = (
            session.execute(select(GexLevel).where(GexLevel.snapshot_id == result.snapshot_id))
            .scalars()
            .all()
        )
    assert {r.filter for r in rows} == {f.value for f in DEFAULT_FILTERS}


async def test_level_computation_failure_does_not_fail_an_already_durable_capture(
    tmp_path, session_factory, monkeypatch, caplog
):
    """The load-bearing guarantee: a bug in level computation must not retroactively turn a
    successful, durable Parquet capture into a reported failure."""

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated GEX computation bug")

    monkeypatch.setattr("app.jobs.capture.compute_and_store", _boom)

    with caplog.at_level(logging.ERROR, logger="app.jobs.capture"):
        result = await capture_snapshot(
            "SPY",
            is_eod=True,
            provider=StubProvider(make_snapshot()),
            session_factory=session_factory,
            data_dir=tmp_path,
        )

    assert result.ok is True
    assert result.snapshot_id is not None
    assert result.error is None
    assert any("GEX level computation failed" in r.message for r in caplog.records)

    # And no levels were written for the snapshot that did persist -- backfill's job now.
    with session_factory() as session:
        rows = (
            session.execute(select(GexLevel).where(GexLevel.snapshot_id == result.snapshot_id))
            .scalars()
            .all()
        )
    assert rows == []
