"""Tests for `app/modules/gex/jobs/decisions.py` and `app/modules/gex/storage/decisions_repository.py` (T61). Offline:
one SQLite engine serves the `decisions`, bars and snapshot tables; `app.modules.gex.api.scan`'s two
module-level factories are monkeypatched onto it exactly as `test_decisions_api.py` does.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.jobs.decisions import record_decisions_job
from app.modules.gex.models.bars import DailyBar as DailyBarIn
from app.modules.gex.models.db import Base
from app.modules.gex.storage import decisions_repository as repo
from app.modules.gex.storage.bars_repository import upsert_bars
from tests.test_decisions_api import _seed_bars, _seed_fade_snapshot


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    monkeypatch.setattr("app.modules.gex.api.scan.get_session_factory", lambda: factory)
    monkeypatch.setattr("app.modules.gex.api.scan.get_gex_session_factory", lambda: factory)
    from app.core import config

    monkeypatch.setattr(config.settings, "SCAN_UNIVERSE", "SPY")
    yield factory
    engine.dispose()


async def test_job_records_once_and_is_idempotent(session_factory):
    _seed_bars(session_factory, "SPY", 60)
    _seed_fade_snapshot(session_factory)

    first = await record_decisions_job(session_factory=session_factory, bars_session_factory=session_factory)
    assert first.errors == ()
    assert first.recorded == 2  # FADE_CALL_WALL + FADE_PUT_WALL
    assert first.evaluated == 2
    assert first.resolved == 0  # no bars after the decision date yet

    second = await record_decisions_job(session_factory=session_factory, bars_session_factory=session_factory)
    assert second.recorded == 0
    assert len(repo.read_history(session_factory=session_factory)) == 2


async def test_job_scores_a_recorded_fade_against_later_bars(session_factory):
    _seed_bars(session_factory, "SPY", 60)
    _seed_fade_snapshot(session_factory)
    await record_decisions_job(session_factory=session_factory, bars_session_factory=session_factory)

    rows = repo.read_history(session_factory=session_factory)
    short = next(r for r in rows if r.key == "FADE_CALL_WALL")
    assert short.outcome == "pending"
    assert short.entry == 101.5 and short.stop == pytest.approx(102.5) and short.target == 98.5
    assert short.opportunity["thesis"]  # the full payload round-trips
    decided_on = short.decided_on
    assert decided_on == dt.date(2026, 1, 5)  # the seeded 16:20 ET snapshot's trading date

    # Next two sessions: the first touches the call wall (fill 101.5), the second falls to
    # the put wall (target 98.5) without ever trading through the 102.5 stop.
    later = [
        DailyBarIn(symbol="SPY", date=dt.date(2026, 1, 6), open=100.5, high=101.8, low=100.0, close=101.0, volume=1, source="test"),
        DailyBarIn(symbol="SPY", date=dt.date(2026, 1, 7), open=100.8, high=101.0, low=98.0, close=98.6, volume=1, source="test"),
    ]
    upsert_bars(later, session_factory=session_factory)

    result = await record_decisions_job(session_factory=session_factory, bars_session_factory=session_factory)
    assert result.resolved >= 1
    short = next(r for r in repo.read_history(session_factory=session_factory) if r.key == "FADE_CALL_WALL")
    assert short.outcome == "target"
    assert short.fill == 101.5
    assert short.result_r == pytest.approx(3.0)  # (101.5 - 98.5) / 1.0
    assert short.triggered_on == dt.date(2026, 1, 6)
    assert short.resolved_on == dt.date(2026, 1, 7)
    # A resolved row is final: a third run leaves it untouched and only re-scores pending ones.
    third = await record_decisions_job(session_factory=session_factory, bars_session_factory=session_factory)
    assert third.evaluated == 1  # only the put-wall fade is still pending
    assert repo.unresolved(session_factory=session_factory)[0].key == "FADE_PUT_WALL"


async def test_job_never_raises_when_the_pipeline_fails(session_factory, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("pipeline down")

    monkeypatch.setattr("app.modules.gex.jobs.decisions.build_regime_rows", boom)
    result = await record_decisions_job(session_factory=session_factory, bars_session_factory=session_factory)
    assert result.recorded == 0
    assert result.errors and "pipeline down" in result.errors[0]
