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
    BARS_JOB_ID,
    DECISIONS_JOB_ID,
    EOD_JOB_ID,
    EXTENDED_JOB_ID,
    FLOWS_JOB_ID,
    SAFETY_NET_JOB_ID,
    bars_update_job,
    build_scheduler,
    capture_eod_job,
    capture_eod_safety_net_job,
    capture_extended_job,
    decisions_update_job,
    flows_update_job,
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


# --- T42: 17:30 NY daily-bars job -- additive, must never affect the option capture -----------


def test_build_scheduler_registers_bars_job():
    scheduler = build_scheduler()
    job = scheduler.get_job(BARS_JOB_ID)
    assert job is not None
    assert job.max_instances == 1
    assert job.misfire_grace_time is None
    assert job.coalesce is True


def test_build_scheduler_bars_job_trigger_is_mon_fri_1730_ny():
    scheduler = build_scheduler()
    job = scheduler.get_job(BARS_JOB_ID)
    trigger = job.trigger
    field_strs = {f.name: str(f) for f in trigger.fields}
    assert field_strs["hour"] == "17"
    assert field_strs["minute"] == "30"
    assert field_strs["day_of_week"] == "mon-fri"
    assert str(trigger.timezone) == "America/New_York"


def test_build_scheduler_still_registers_the_two_option_capture_jobs_unchanged():
    """P0 guardrail: adding the bars job must not touch the existing EOD/safety-net jobs."""
    scheduler = build_scheduler()
    assert scheduler.get_job(EOD_JOB_ID) is not None
    assert scheduler.get_job(SAFETY_NET_JOB_ID) is not None
    assert {job.id for job in scheduler.get_jobs()} == {
        EOD_JOB_ID,
        SAFETY_NET_JOB_ID,
        BARS_JOB_ID,
        EXTENDED_JOB_ID,
        FLOWS_JOB_ID,
        DECISIONS_JOB_ID,
    }


async def test_bars_update_job_delegates_to_update_bars_job(monkeypatch):
    called = False

    async def fake_update_bars_job(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(scheduler_module, "update_bars_job", fake_update_bars_job)

    await bars_update_job()

    assert called is True


async def test_bars_update_job_survives_an_unexpected_exception(monkeypatch, caplog):
    """P0 guardrail: a bug in the bars pipeline must not be capable of taking the scheduler
    thread down with it, which would silently deregister the option-capture jobs too."""

    async def boom(*args, **kwargs):
        raise RuntimeError("yahoo is unreachable")

    monkeypatch.setattr(scheduler_module, "update_bars_job", boom)

    with caplog.at_level(logging.ERROR, logger="app.jobs.scheduler"):
        await bars_update_job()  # must not raise

    assert any("unexpected top-level failure" in r.message for r in caplog.records)


# --- T47: 16:45 NY extended (sector/industry ETF) capture job -- additive, P0 guardrail --------


def test_build_scheduler_registers_extended_job():
    scheduler = build_scheduler()
    job = scheduler.get_job(EXTENDED_JOB_ID)
    assert job is not None
    assert job.max_instances == 1
    assert job.misfire_grace_time is None
    assert job.coalesce is True


def test_build_scheduler_extended_job_trigger_is_mon_fri_1645_ny():
    scheduler = build_scheduler()
    job = scheduler.get_job(EXTENDED_JOB_ID)
    trigger = job.trigger
    field_strs = {f.name: str(f) for f in trigger.fields}
    assert field_strs["hour"] == "16"
    assert field_strs["minute"] == "45"
    assert field_strs["day_of_week"] == "mon-fri"
    assert str(trigger.timezone) == "America/New_York"


def test_build_scheduler_adding_the_extended_job_leaves_the_other_three_untouched():
    """P0 guardrail, the acceptance criterion verbatim: the core EOD job, its safety net, and
    the bars job must be provably unchanged by this task -- same ids, same trigger fields, same
    misfire policy -- not merely "still present"."""
    scheduler = build_scheduler()

    eod = scheduler.get_job(EOD_JOB_ID)
    assert eod is not None
    assert eod.name == "EOD option chain capture (SPX/SPY/QQQ/GLD/DIA)"
    assert eod.max_instances == 1
    assert eod.misfire_grace_time is None
    assert eod.coalesce is True
    eod_fields = {f.name: str(f) for f in eod.trigger.fields}
    assert (eod_fields["hour"], eod_fields["minute"], eod_fields["day_of_week"]) == ("16", "20", "mon-fri")

    safety_net = scheduler.get_job(SAFETY_NET_JOB_ID)
    assert safety_net is not None
    assert safety_net.max_instances == 1
    assert safety_net.misfire_grace_time is None
    assert safety_net.coalesce is True
    safety_net_fields = {f.name: str(f) for f in safety_net.trigger.fields}
    assert (safety_net_fields["hour"], safety_net_fields["minute"], safety_net_fields["day_of_week"]) == (
        "20",
        "0",
        "mon-fri",
    )

    bars = scheduler.get_job(BARS_JOB_ID)
    assert bars is not None
    assert bars.max_instances == 1
    assert bars.misfire_grace_time is None
    assert bars.coalesce is True
    bars_fields = {f.name: str(f) for f in bars.trigger.fields}
    assert (bars_fields["hour"], bars_fields["minute"], bars_fields["day_of_week"]) == ("17", "30", "mon-fri")

    assert {job.id for job in scheduler.get_jobs()} == {
        EOD_JOB_ID,
        SAFETY_NET_JOB_ID,
        BARS_JOB_ID,
        EXTENDED_JOB_ID,
        FLOWS_JOB_ID,
        DECISIONS_JOB_ID,
    }


async def test_capture_extended_job_skips_on_a_holiday_without_calling_capture(monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "is_trading_day", lambda day: False)

    called = False

    async def fake_capture_all_symbols(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(scheduler_module, "capture_all_symbols", fake_capture_all_symbols)

    with caplog.at_level(logging.INFO, logger="app.jobs.scheduler"):
        await capture_extended_job()

    assert called is False
    assert any("capture_extended_skipped" in r.message for r in caplog.records)


async def test_capture_extended_job_calls_capture_all_symbols_with_extended_symbols(monkeypatch):
    monkeypatch.setattr(scheduler_module, "is_trading_day", lambda day: True)

    seen_symbols = None
    seen_is_eod = None

    async def fake_capture_all_symbols(symbols, *, is_eod, **kwargs):
        nonlocal seen_symbols, seen_is_eod
        seen_symbols = symbols
        seen_is_eod = is_eod
        return []

    monkeypatch.setattr(scheduler_module, "capture_all_symbols", fake_capture_all_symbols)

    await capture_extended_job()

    # settings.extended_symbols, not settings.symbols -- the whole point of T47's separate
    # job is that the two lists never mix.
    assert seen_symbols == scheduler_module.settings.extended_symbols
    assert "SPX" not in seen_symbols
    assert "XLK" in seen_symbols
    assert seen_is_eod is True


async def test_capture_extended_job_one_symbol_failing_does_not_stop_the_others(monkeypatch):
    """Mirrors the acceptance criterion: a mock provider where one extended symbol fails must
    still leave every other symbol captured. `capture_all_symbols` already guarantees this
    (see `test_capture.py`); this test pins that `capture_extended_job` actually delegates to
    it rather than some other, less resilient call path."""
    monkeypatch.setattr(scheduler_module, "is_trading_day", lambda day: True)

    from app.jobs.capture import CaptureResult

    async def fake_capture_all_symbols(symbols, *, is_eod, **kwargs):
        return [
            CaptureResult(underlying=sym, ok=(sym != symbols[0]))
            for sym in symbols
        ]

    monkeypatch.setattr(scheduler_module, "capture_all_symbols", fake_capture_all_symbols)

    # Must not raise, and must not short-circuit -- there is nothing here to assert the
    # *results* of (capture_extended_job discards the return value, matching capture_eod_job),
    # so this test's real assertion is simply that the call completes normally even though the
    # fake provider reports one failure.
    await capture_extended_job()


async def test_capture_extended_job_survives_an_unexpected_exception(monkeypatch, caplog):
    """Belt-and-suspenders, same as capture_eod_job's equivalent test: even if
    capture_all_symbols itself somehow raised, the job function must not propagate -- a bug
    here must be structurally incapable of taking the scheduler thread (and therefore the core
    EOD job) down with it."""
    monkeypatch.setattr(scheduler_module, "is_trading_day", lambda day: True)

    async def boom(*args, **kwargs):
        raise RuntimeError("cboe is unreachable for an extended symbol")

    monkeypatch.setattr(scheduler_module, "capture_all_symbols", boom)

    with caplog.at_level(logging.ERROR, logger="app.jobs.scheduler"):
        await capture_extended_job()  # must not raise

    assert any("unexpected top-level failure" in r.message for r in caplog.records)


# --- T52: 18:30 NY ETF shares-outstanding flows job -- additive, must never affect capture -----


def test_build_scheduler_registers_flows_job():
    scheduler = build_scheduler()
    job = scheduler.get_job(FLOWS_JOB_ID)
    assert job is not None
    assert job.max_instances == 1
    assert job.misfire_grace_time is None
    assert job.coalesce is True


def test_build_scheduler_flows_job_trigger_is_mon_fri_1830_ny():
    scheduler = build_scheduler()
    job = scheduler.get_job(FLOWS_JOB_ID)
    trigger = job.trigger
    field_strs = {f.name: str(f) for f in trigger.fields}
    assert field_strs["hour"] == "18"
    assert field_strs["minute"] == "30"
    assert field_strs["day_of_week"] == "mon-fri"
    assert str(trigger.timezone) == "America/New_York"


def test_build_scheduler_adding_the_flows_job_leaves_the_other_four_untouched():
    """P0 guardrail, the acceptance criterion verbatim: the core EOD job, its safety net, the
    bars job and the extended-capture job must be provably unchanged by this task."""
    scheduler = build_scheduler()

    eod = scheduler.get_job(EOD_JOB_ID)
    assert eod is not None
    eod_fields = {f.name: str(f) for f in eod.trigger.fields}
    assert (eod_fields["hour"], eod_fields["minute"], eod_fields["day_of_week"]) == ("16", "20", "mon-fri")

    safety_net = scheduler.get_job(SAFETY_NET_JOB_ID)
    assert safety_net is not None
    safety_net_fields = {f.name: str(f) for f in safety_net.trigger.fields}
    assert (safety_net_fields["hour"], safety_net_fields["minute"], safety_net_fields["day_of_week"]) == (
        "20",
        "0",
        "mon-fri",
    )

    bars = scheduler.get_job(BARS_JOB_ID)
    assert bars is not None
    bars_fields = {f.name: str(f) for f in bars.trigger.fields}
    assert (bars_fields["hour"], bars_fields["minute"], bars_fields["day_of_week"]) == ("17", "30", "mon-fri")

    extended = scheduler.get_job(EXTENDED_JOB_ID)
    assert extended is not None
    extended_fields = {f.name: str(f) for f in extended.trigger.fields}
    assert (extended_fields["hour"], extended_fields["minute"], extended_fields["day_of_week"]) == (
        "16",
        "45",
        "mon-fri",
    )

    assert {job.id for job in scheduler.get_jobs()} == {
        EOD_JOB_ID,
        SAFETY_NET_JOB_ID,
        BARS_JOB_ID,
        EXTENDED_JOB_ID,
        FLOWS_JOB_ID,
        DECISIONS_JOB_ID,
    }


async def test_flows_update_job_delegates_to_update_flows_job(monkeypatch):
    called = False

    async def fake_update_flows_job(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(scheduler_module, "update_flows_job", fake_update_flows_job)

    await flows_update_job()

    assert called is True


async def test_flows_update_job_survives_an_unexpected_exception(monkeypatch, caplog):
    """P0 guardrail: a bug in the flows pipeline must not be capable of taking the scheduler
    thread down with it, which would silently deregister the option-capture jobs too."""

    async def boom(*args, **kwargs):
        raise RuntimeError("an issuer endpoint is unreachable")

    monkeypatch.setattr(scheduler_module, "update_flows_job", boom)

    with caplog.at_level(logging.ERROR, logger="app.jobs.scheduler"):
        await flows_update_job()  # must not raise

    assert any("unexpected top-level failure" in r.message for r in caplog.records)


# --- T61: decisions job ---------------------------------------------------------------------


def test_build_scheduler_registers_decisions_job_at_1745_ny():
    scheduler = build_scheduler()
    job = scheduler.get_job(DECISIONS_JOB_ID)
    assert job is not None
    assert job.max_instances == 1
    assert job.misfire_grace_time is None
    assert job.coalesce is True
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"], fields["day_of_week"]) == ("17", "45", "mon-fri")


async def test_decisions_update_job_survives_an_unexpected_exception(monkeypatch, caplog):
    async def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr("app.jobs.scheduler.record_decisions_job", boom)
    with caplog.at_level("ERROR"):
        await decisions_update_job()  # must not raise
    assert "decisions_update_job: unexpected top-level failure" in caplog.text
