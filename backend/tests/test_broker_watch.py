"""The executor watch (T174): the heartbeat and capture-watch's alert on it.

SQLite through the same schema-translate trick as `test_broker_executor.py`. The case this
exists for is 2026-10-09 to 10-15: no executor container at all, so no heartbeat row ever, and
an 18:00 New York entry going unsent with nothing saying so.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.modules.broker.executor import Executor
from app.modules.broker.strategies import GoldAsiaDrift
from app.modules.broker.tables import Base, ExecutorHeartbeat, OrderIntent
from app.modules.broker.watch import check_executor, legs_in_window
from app.workers import capture_watch

UTC = dt.UTC
GA = GoldAsiaDrift()
# Thursday 2026-10-15 (EDT): the entry is due 22:00 UTC, the exit Friday 07:00 UTC.
ENTRY = dt.datetime(2026, 10, 15, 22, 0, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'w.db'}").execution_options(
        schema_translate_map={"broker": None})
    Base.metadata.create_all(engine)
    yield sessionmaker(engine)
    engine.dispose()


@pytest.fixture
def watch(factory, monkeypatch):
    """capture-watch's executor job against the SQLite factory, with alerts collected."""
    sent: list[str] = []
    monkeypatch.setattr(capture_watch, "get_session_factory", lambda: factory)
    monkeypatch.setattr(capture_watch.notify, "send", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(capture_watch.settings, "EXECUTOR_STRATEGIES", "gold_asia")
    monkeypatch.setattr(capture_watch, "_executor_alerted", None)
    monkeypatch.setattr(capture_watch, "_expired_seen", set())
    return sent


def beat(factory, at):
    with factory() as s:
        s.merge(ExecutorHeartbeat(service="executor", beat_at=at, started_at=at, enabled=True,
                                  strategies="gold_asia"))
        s.commit()


def test_the_window_opens_thirty_minutes_before_the_entry():
    assert legs_in_window([GA], ENTRY - dt.timedelta(minutes=31)) == []
    (leg,) = legs_in_window([GA], ENTRY - dt.timedelta(minutes=29))
    assert leg.key == "gold_asia:2026-10-15:open"
    # The entry window is 10 minutes; past it, nothing is due until the exit's window opens.
    assert legs_in_window([GA], ENTRY + dt.timedelta(minutes=11)) == []
    (leg,) = legs_in_window([GA], dt.datetime(2026, 10, 16, 8, 0, tzinfo=UTC))  # Friday morning
    assert leg.key == "gold_asia:2026-10-15:close"


def test_no_heartbeat_ever_is_an_outage_when_a_leg_is_due(factory):
    with factory() as s:
        report = check_executor(s, [GA], ENTRY - dt.timedelta(minutes=20))
    assert report.beat_at is None and report.is_outage
    assert "never" in report.summary() and "gold_asia:2026-10-15:open" in report.summary()


def test_no_leg_due_is_not_an_outage(factory):
    with factory() as s:  # Saturday: no session at all
        assert not check_executor(s, [GA], dt.datetime(2026, 10, 17, 22, tzinfo=UTC)).is_outage


async def test_a_completed_tick_beats(factory):
    class NoPositions:
        async def positions(self, **kw):
            return []

    ex = Executor(NoPositions(), factory, [GA], enabled=False, volume=0.01, max_volume=0.05)
    await ex.tick(ENTRY - dt.timedelta(minutes=25))  # only yesterday's close is due
    with factory() as s:
        report = check_executor(s, [GA], ENTRY - dt.timedelta(minutes=22))
    assert not report.stale and not report.is_outage
    with factory() as s:
        assert check_executor(s, [GA], ENTRY + dt.timedelta(minutes=5)).is_outage  # 30 min old


def test_an_outage_alerts_once_and_the_recovery_says_so(factory, watch):
    for minutes in (-28, -26, -20, -2):
        capture_watch.run_executor_check(now=ENTRY + dt.timedelta(minutes=minutes))
    assert len(watch) == 1 and watch[0].startswith("EXECUTOR NOT RUNNING")
    beat(factory, ENTRY)
    capture_watch.run_executor_check(now=ENTRY + dt.timedelta(minutes=1))
    assert watch[1] == "executor heartbeat is back"
    capture_watch.run_executor_check(now=ENTRY + dt.timedelta(minutes=3))
    assert len(watch) == 2


def test_a_running_executor_sends_nothing(factory, watch):
    for minutes in (-28, -10, 0, 8):
        beat(factory, ENTRY + dt.timedelta(minutes=minutes, seconds=-20))
        capture_watch.run_executor_check(now=ENTRY + dt.timedelta(minutes=minutes))
    assert watch == []


def test_an_expired_leg_alerts_once(factory, watch):
    with factory() as s:
        s.add(OrderIntent(
            key="gold_asia:2026-10-15:open", strategy="gold_asia", symbol="XAUUSD",
            action="open", side="buy", volume=0.01, due_at=ENTRY,
            expires_at=ENTRY + dt.timedelta(minutes=10), status="expired", attempts=0,
            created_at=ENTRY + dt.timedelta(minutes=18),
            note="entry window passed before it could be sent"))
        s.commit()
    later = ENTRY + dt.timedelta(minutes=19)
    beat(factory, later)
    capture_watch.run_executor_check(now=later)
    capture_watch.run_executor_check(now=later + dt.timedelta(minutes=2))
    assert len(watch) == 1
    assert watch[0].startswith("MISSED TRADE: gold_asia:2026-10-15:open (due 2026-10-15 18:00")
