"""Tests for `app/jobs/scheduler.py`.

Only ever builds the scheduler, never starts it -- these tests confirm the job is
registered with the right id/trigger/misfire policy, and that `capture_eod_job` itself
skips correctly on a non-trading day and never raises. No 16:20 wait, no network: see the
T05 acceptance note ("confirm the scheduler starts on lifespan without firing a capture").
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from app.jobs import scheduler as scheduler_module
from app.jobs.scheduler import (
    EOD_JOB_ID,
    SAFETY_NET_JOB_ID,
    build_scheduler,
    capture_eod_job,
    capture_eod_safety_net_job,
)

_NY = ZoneInfo("America/New_York")


def test_build_scheduler_registers_capture_eod_job():
    scheduler = build_scheduler()
    job = scheduler.get_job(EOD_JOB_ID)
    assert job is not None
    assert job.name == "EOD option chain capture (SPX/SPY/QQQ/GLD/DIA)"
    assert job.max_instances == 1
    # coalesce/misfire_grace_time aren't exposed as public attributes on every APScheduler
    # version in the same way, but the job's kwargs dict on the trigger is stable enough to
    # assert the intent directly through the job's own stored config.
    assert job.misfire_grace_time is None
    assert job.coalesce is True


def test_build_scheduler_job_trigger_is_mon_fri_1620_ny():
    scheduler = build_scheduler()
    job = scheduler.get_job(EOD_JOB_ID)
    trigger = job.trigger
    field_strs = {f.name: str(f) for f in trigger.fields}
    assert field_strs["hour"] == "16"
    assert field_strs["minute"] == "20"
    assert field_strs["day_of_week"] == "mon-fri"
    assert str(trigger.timezone) == "America/New_York"


def test_build_scheduler_next_run_time_is_a_future_weekday_1620_ny():
    """Sanity check that the trigger actually computes a sensible next fire time, without
    ever starting the scheduler (so this never risks firing a real capture)."""
    scheduler = build_scheduler()
    job = scheduler.get_job(EOD_JOB_ID)
    now = dt.datetime.now(_NY)
    next_fire = job.trigger.get_next_fire_time(None, now)
    assert next_fire is not None
    assert next_fire > now
    assert next_fire.weekday() < 5
    assert (next_fire.hour, next_fire.minute) == (16, 20)


def test_build_scheduler_does_not_start_or_fire_anything():
    """Building must be inert -- confirms the lifespan's build/.start() split (main.py)
    actually keeps construction side-effect-free."""
    scheduler = build_scheduler()
    assert scheduler.running is False


async def test_capture_eod_job_skips_on_a_holiday_without_calling_capture(monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "is_trading_day", lambda day: False)

    called = False

    async def fake_capture_all_symbols(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(scheduler_module, "capture_all_symbols", fake_capture_all_symbols)

    with caplog.at_level(logging.INFO, logger="app.jobs.scheduler"):
        await capture_eod_job()

    assert called is False
    assert any("capture_eod_skipped" in r.message for r in caplog.records)


async def test_capture_eod_job_calls_capture_all_symbols_on_a_trading_day(monkeypatch):
    monkeypatch.setattr(scheduler_module, "is_trading_day", lambda day: True)

    seen_symbols = None
    seen_is_eod = None

    async def fake_capture_all_symbols(symbols, *, is_eod, **kwargs):
        nonlocal seen_symbols, seen_is_eod
        seen_symbols = symbols
        seen_is_eod = is_eod
        return []

    monkeypatch.setattr(scheduler_module, "capture_all_symbols", fake_capture_all_symbols)

    await capture_eod_job()

    assert seen_symbols == ["SPX", "SPY", "QQQ", "GLD", "DIA"]
    assert seen_is_eod is True


async def test_capture_eod_job_survives_an_unexpected_exception(monkeypatch, caplog):
    """Belt-and-suspenders: even if capture_all_symbols itself somehow raised (rather than
    returning CaptureResult(ok=False) as designed), the job function must not propagate."""
    monkeypatch.setattr(scheduler_module, "is_trading_day", lambda day: True)

    async def boom(*args, **kwargs):
        raise RuntimeError("something no CaptureResult could catch")

    monkeypatch.setattr(scheduler_module, "capture_all_symbols", boom)

    with caplog.at_level(logging.ERROR, logger="app.jobs.scheduler"):
        await capture_eod_job()  # must not raise

    assert any("unexpected top-level failure" in r.message for r in caplog.records)


# --- T29: 20:00 NY safety-net job -------------------------------------------------------------


def test_build_scheduler_registers_safety_net_job():
    scheduler = build_scheduler()
    job = scheduler.get_job(SAFETY_NET_JOB_ID)
    assert job is not None
    assert job.max_instances == 1
    assert job.misfire_grace_time is None
    assert job.coalesce is True


def test_build_scheduler_safety_net_job_trigger_is_mon_fri_2000_ny():
    scheduler = build_scheduler()
    job = scheduler.get_job(SAFETY_NET_JOB_ID)
    trigger = job.trigger
    field_strs = {f.name: str(f) for f in trigger.fields}
    assert field_strs["hour"] == "20"
    assert field_strs["minute"] == "0"
    assert field_strs["day_of_week"] == "mon-fri"
    assert str(trigger.timezone) == "America/New_York"


async def test_capture_eod_safety_net_job_delegates_to_catch_up_missed_eod(monkeypatch):
    seen = {}

    async def fake_catch_up(symbols, **kwargs):
        seen["symbols"] = symbols
        return []

    monkeypatch.setattr(scheduler_module, "catch_up_missed_eod", fake_catch_up)

    await capture_eod_safety_net_job()

    assert seen["symbols"] == ["SPX", "SPY", "QQQ", "GLD", "DIA"]


async def test_capture_eod_safety_net_job_survives_an_unexpected_exception(monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("cboe is unreachable")

    monkeypatch.setattr(scheduler_module, "catch_up_missed_eod", boom)

    with caplog.at_level(logging.ERROR, logger="app.jobs.scheduler"):
        await capture_eod_safety_net_job()  # must not raise

    assert any("unexpected top-level failure" in r.message for r in caplog.records)
