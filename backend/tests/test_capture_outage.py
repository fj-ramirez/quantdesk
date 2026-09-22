"""Tests for the capture watchdog (T104).

Two halves, and the second is the one that decides whether the alert survives contact:

**It must fire** when a trading session produced nothing at all. That is the September 2026
shape -- five open sessions, the whole universe, silence for twelve days.

**It must not fire** on anything ordinary. `scan/regime.STALE_THRESHOLD_MINUTES` documents
measured per-symbol lags of hours; a weekend is not a session; a partial recovery is degraded
rather than silent. An alert that cries wolf gets muted, and a muted alert is worse than none
because it reads as coverage.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from app.core import notify
from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.jobs.outage import (
    check_capture_outage,
    recent_trading_sessions,
)
from app.modules.gex.models.db import Base, Snapshot


@pytest.fixture
def session_factory(tmp_path):
    # `get_engine`, not a bare `create_engine`: the GEX models carry `MetaData(schema="gex")`
    # (invariant 8) and SQLite has no schemas, so the engine factory is what maps them.
    engine = get_engine(f"sqlite:///{tmp_path / 'outage.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def _snapshot(session, *, underlying: str, session_date: dt.date, n: int = 0) -> None:
    captured = dt.datetime.combine(session_date, dt.time(20, 20), tzinfo=dt.UTC)
    session.add(
        Snapshot(
            underlying=underlying,
            captured_at=captured + dt.timedelta(seconds=n),
            source="synthetic",
            spot=100.0,
            contract_count=10,
            parquet_path=f"{underlying}/{session_date}.parquet",
            is_eod=True,
            session_date=session_date,
        )
    )


# 2026-09-21 is a Monday; the week before it is the missing September opex week.
_MON_21 = dt.date(2026, 9, 21)
_FRI_18 = dt.date(2026, 9, 18)
_THU_17 = dt.date(2026, 9, 17)
_FRI_11 = dt.date(2026, 9, 11)


def test_recent_sessions_skip_weekends_and_the_day_itself():
    """Strictly before `today`: a session still in progress has not had its chance to capture,
    and alerting on it at 09:31 would fire every morning."""
    sessions = recent_trading_sessions(_MON_21, count=4)
    assert sessions == [_FRI_18, _THU_17, dt.date(2026, 9, 16), dt.date(2026, 9, 15)]
    assert _MON_21 not in sessions
    assert all(d.weekday() < 5 for d in sessions)


def test_a_silent_session_is_an_outage(session_factory):
    """The September shape. Every session in range traded; one produced nothing."""
    with session_factory() as session:
        for i, sym in enumerate(("SPX", "SPY", "QQQ")):
            _snapshot(session, underlying=sym, session_date=_THU_17, n=i)
        session.commit()
        report = check_capture_outage(session, today=dt.date(2026, 9, 19), lookback=2)

    assert report.is_outage
    assert _FRI_18 in report.missing
    assert (_THU_17, 3) in report.covered
    assert "CAPTURE OUTAGE" in report.summary()
    assert "cannot be backfilled" in report.summary()


def test_a_fully_captured_week_is_not_an_outage(session_factory):
    with session_factory() as session:
        for day in recent_trading_sessions(_MON_21, count=5):
            for i, sym in enumerate(("SPX", "SPY", "QQQ")):
                _snapshot(session, underlying=sym, session_date=day, n=i)
        session.commit()
        report = check_capture_outage(session, today=_MON_21, lookback=5)

    assert not report.is_outage
    assert report.missing == ()
    assert "healthy" in report.summary()


def test_a_weekend_is_not_a_missing_session(session_factory):
    """A Saturday with no capture is not an outage -- nothing traded. This is the false
    positive that would fire every single weekend."""
    with session_factory() as session:
        for i, sym in enumerate(("SPX", "SPY")):
            _snapshot(session, underlying=sym, session_date=_FRI_18, n=i)
        session.commit()
        # Sunday: the only completed session in range is Friday, which was captured.
        report = check_capture_outage(session, today=dt.date(2026, 9, 20), lookback=1)

    assert not report.is_outage
    assert report.checked == (_FRI_18,)


def test_one_stale_symbol_is_not_an_outage(session_factory):
    """`STALE_THRESHOLD_MINUTES` documents per-symbol lags of hours as normal. A session where
    27 of 28 symbols captured is not silent, and alerting on it trains the user to ignore the
    alert -- which is how the next real outage is missed."""
    with session_factory() as session:
        _snapshot(session, underlying="SPX", session_date=_FRI_18)
        session.commit()
        report = check_capture_outage(session, today=dt.date(2026, 9, 19), lookback=1)

    assert not report.is_outage


def test_a_session_captured_only_on_the_weekend_after_still_counts(session_factory):
    """The staggered recovery the September outage ended with: Saturday and Sunday captures
    carrying Friday's book. Keyed on `session_date`, those *are* Friday's session -- keying on
    `captured_at` would both miss that and invent a Sunday session (T102)."""
    with session_factory() as session:
        _snapshot(session, underlying="SPX", session_date=_FRI_18)  # captured Sunday, say
        session.commit()
        report = check_capture_outage(session, today=dt.date(2026, 9, 19), lookback=1)

    assert not report.is_outage
    assert report.covered == ((_FRI_18, 1),)


def test_nothing_in_range_is_not_reported_as_an_outage(session_factory):
    with session_factory() as session:
        report = check_capture_outage(session, today=_MON_21, lookback=0)
    assert not report.is_outage
    assert "nothing to check" in report.summary()


# --- delivery is opt-in ----------------------------------------------------------------------


def test_notify_logs_and_reports_no_channel_when_unconfigured(monkeypatch, caplog):
    """The default. Detection still produces a record; nothing leaves the box, and the caller
    is told which of those two happened rather than having to infer it."""
    monkeypatch.setattr(notify.settings, "TELEGRAM_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(notify.settings, "TELEGRAM_CHAT_ID", "", raising=False)
    with caplog.at_level(logging.WARNING):
        result = notify.send("capture outage on 2026-09-18")

    assert result.logged
    assert not result.delivered
    assert result.reason == "no channel configured"
    assert "capture outage on 2026-09-18" in caplog.text


def test_notify_never_raises_when_delivery_fails(monkeypatch, caplog):
    """A watchdog that dies trying to alert has the same failure mode as the thing it watches:
    silence. The message must still reach the log."""
    monkeypatch.setattr(notify.settings, "TELEGRAM_BOT_TOKEN", "token", raising=False)
    monkeypatch.setattr(notify.settings, "TELEGRAM_CHAT_ID", "42", raising=False)

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    with caplog.at_level(logging.WARNING):
        result = notify.send("outage")

    assert result.logged
    assert not result.delivered
    assert "delivery failed" in result.reason
    assert "outage" in caplog.text


def test_notify_does_not_log_the_token_on_failure(monkeypatch, caplog):
    """The token is in the URL. `logger.exception` here would put the request -- and so the
    bot token -- into the log, which is why this path logs the exception *type* only."""
    monkeypatch.setattr(notify.settings, "TELEGRAM_BOT_TOKEN", "SUPERSECRET", raising=False)
    monkeypatch.setattr(notify.settings, "TELEGRAM_CHAT_ID", "42", raising=False)

    def boom(*args, **kwargs):
        raise RuntimeError("https://api.telegram.org/botSUPERSECRET/sendMessage refused")

    monkeypatch.setattr(notify.httpx, "post", boom)
    with caplog.at_level(logging.DEBUG):
        notify.send("outage")

    assert "SUPERSECRET" not in caplog.text


def test_notifier_status_names_the_mode(monkeypatch):
    """Said at boot so "I thought alerting was on" is not a thing that happens quietly."""
    monkeypatch.setattr(notify.settings, "TELEGRAM_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(notify.settings, "TELEGRAM_CHAT_ID", "", raising=False)
    assert "DISABLED" in notify.notifier_status()

    monkeypatch.setattr(notify.settings, "TELEGRAM_BOT_TOKEN", "t", raising=False)
    assert "DISABLED" in notify.notifier_status()  # only half configured is still off

    monkeypatch.setattr(notify.settings, "TELEGRAM_CHAT_ID", "c", raising=False)
    assert "ENABLED" in notify.notifier_status()
