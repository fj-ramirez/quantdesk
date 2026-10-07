"""Tests for `app/modules/research/jobs/signals.py` (T136). Offline: synthetic bars, SQLite, and
`notify.send` captured rather than called."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.models.bars import DailyBar
from app.modules.gex.models.db import Base as GexBase
from app.modules.gex.storage.bars_repository import upsert_bars
from app.modules.research.jobs import signals as job
from app.modules.research.models.db import Base as ResearchBase
from app.modules.research.signals import SignalEvent

T0 = dt.datetime(2026, 10, 6, 14, tzinfo=dt.UTC)


def _ev(symbol: str, signal: str, action: str = "ENTER", **params) -> SignalEvent:
    p = {"n": 160, "entry_z": 1.5, "exit_z": 0.5, "long_short": False, "regime": "high_vol", **params}
    return SignalEvent(
        signal=signal, family="zscore_meanrev", symbol=symbol, timeframe="1h", bar_ts=T0,
        action=action, side="LONG", price=6500.25, reason="z -1.62 < -1.5", late=False, params=p,
    )


def test_futures_digest_counts_ranges_and_names_only_the_watchlist():
    msg = job.futures_digest([
        _ev("ES=F", "ZScoreDip_N160_E1p5_X0p5_HighVol"),
        _ev("ES=F", "ZScoreDip_N80_E1_X0_Any", n=80, entry_z=1.0, exit_z=0.0, regime="any"),
        _ev("ES=F", "ZScoreDip_N240_E2_X0_Any", "EXIT", n=240, entry_z=2.0, exit_z=0.0, regime="any"),
        _ev("NQ=F", "ZScoreDip_N160_E1p5_X0p5_HighVol"),  # the ES watchlist row, but on NQ
    ])
    assert msg is not None
    assert msg.startswith("quantdesk signals: 1h bar 2026-10-06 14:00 UTC")
    assert "ES=F 6500.25: ENTER LONG ×2, EXIT ×1" in msg
    assert "★ ZScoreDip_N160_E1p5_X0p5_HighVol ENTER" in msg
    assert msg.count("★") == 1  # not named on NQ, where it was never promoted
    assert "enters: n 80,160 · entry_z 1,1.5 · exit_z 0,0.5 · any,high_vol" in msg
    assert "ZScoreDip_N80_E1_X0_Any" not in msg  # counted, not listed
    assert "roll" in msg
    assert job.futures_digest([]) is None


def test_continuation_digest_reads_like_orders():
    enter = SignalEvent(
        signal="ContinuationProxy_Short", family="continuation_proxy", symbol="HYG", timeframe="1d",
        bar_ts=dt.datetime(2026, 10, 6, tzinfo=dt.UTC), action="ENTER", side="SHORT", price=80.0,
        reason="5-session return -1.20%; enter at the next open", late=False, stop=80.6, target=78.8,
    )
    msg = job.continuation_digest([enter])
    assert msg is not None
    assert "• HYG SHORT at the next open · stop 80.6 · target 78.8 (5-session return -1.20%)" in msg
    assert "no gamma filter" in msg
    assert job.continuation_digest([]) is None


@pytest.fixture
def factories(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    ResearchBase.metadata.create_all(engine)
    GexBase.metadata.create_all(engine)
    yield get_sessionmaker(engine)
    engine.dispose()


@pytest.fixture
def sent(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(job.notify, "send", lambda m, **_: messages.append(m))
    return messages


def _hourly_with_a_dip_on_the_last_bar() -> pd.DataFrame:
    closes = [100.1 if i % 2 else 99.9 for i in range(300)] + [96.0]
    idx = pd.date_range("2026-09-20", periods=len(closes), freq="1h", tz="UTC")
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1.0})


def test_hourly_run_alerts_fresh_events_once(factories, sent):
    df = _hourly_with_a_dip_on_the_last_bar()
    loader = {"ES=F": df}.get  # the other three have no data: skipped, not failed
    first = job.run_futures_signals(refresh=False, loader=loader, session_factory=factories)
    assert first > 0 and len(sent) == 1 and "ES=F" in sent[0] and "ENTER LONG" in sent[0]

    again = job.run_futures_signals(refresh=False, loader=loader, session_factory=factories)
    assert again == 0 and len(sent) == 1  # the same bar re-read: recorded once, sent once


def test_daily_run_reads_gex_bars_and_alerts_the_proxy(factories, sent):
    closes = [100.0] * 20 + [99.0, 98.0, 97.0, 96.0, 95.0]
    day, rows = dt.date(2026, 9, 1), []
    for c in closes:
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        rows.append(DailyBar(symbol="HYG", date=day, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=1, source="test"))
        day += dt.timedelta(days=1)
    upsert_bars(rows, session_factory=factories)

    n = job.run_continuation_signals(universe=["HYG", "XLU"], bars_session_factory=factories, session_factory=factories)
    # On the last session the previous short reaches its target and the same close signals again.
    assert n == 2 and len(sent) == 1
    assert "HYG SHORT at the next open" in sent[0] and "HYG short exit: target" in sent[0]
    assert sent[0].index("at the next open") < sent[0].index("exit:")  # entries read first


def test_jobs_register_unless_off(monkeypatch):
    sched = AsyncIOScheduler()
    assert job.add_signal_jobs(sched) == [job.HOURLY_JOB_ID, job.DAILY_JOB_ID]
    assert {j.id for j in sched.get_jobs()} == {job.HOURLY_JOB_ID, job.DAILY_JOB_ID}

    monkeypatch.setattr(job.settings, "SIGNALS_HOURLY_CRON", "off")
    assert job.add_signal_jobs(AsyncIOScheduler()) == [job.DAILY_JOB_ID]


async def test_a_failing_run_never_reaches_the_scheduler(monkeypatch, caplog):
    def boom():
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(job, "run_futures_signals", boom)
    await job.hourly_signals_job()
    assert "signals hourly run failed" in caplog.text
