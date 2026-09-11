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
    BARS_PREOPEN_JOB_ID,
    DECISIONS_JOB_ID,
    EOD_JOB_ID,
    EXTENDED_JOB_ID,
    FLOWS_JOB_ID,
    INTRADAY_FIRST_CAPTURE,
    INTRADAY_JOB_ID,
    INTRADAY_LAST_CAPTURE,
    RETENTION_JOB_ID,
    SAFETY_NET_JOB_ID,
    bars_update_job,
    build_scheduler,
    capture_eod_job,
    capture_eod_safety_net_job,
    capture_extended_job,
    capture_intraday_job,
    decisions_update_job,
    flows_update_job,
    retention_prune_job,
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
        RETENTION_JOB_ID,
        BARS_PREOPEN_JOB_ID,
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
        RETENTION_JOB_ID,
        BARS_PREOPEN_JOB_ID,
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
        RETENTION_JOB_ID,
        BARS_PREOPEN_JOB_ID,
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


# --- T32: nightly retention prune ------------------------------------------------------------


def test_build_scheduler_registers_retention_job_at_2100_every_day():
    """21:00 ET, an hour after the 20:00 EOD safety net, so a late-recovered capture is stored
    and flagged `is_eod=True` before anything considers deleting strike rows. Daily rather than
    Mon-Fri: retention is a function of row age, and a weekend is a fine time to delete."""
    scheduler = build_scheduler()
    job = scheduler.get_job(RETENTION_JOB_ID)
    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("21", "0")
    assert fields["day_of_week"] == "*"


def test_retention_job_does_not_use_the_capture_misfire_policy():
    """Deliberately unlike every capture job here. Those set `misfire_grace_time=None` because
    a late capture still captures something irreplaceable; a prune has nothing irreplaceable to
    catch, since a run skipped tonight deletes the same rows plus a day's worth tomorrow."""
    scheduler = build_scheduler()
    assert scheduler.get_job(RETENTION_JOB_ID).misfire_grace_time is not None
    assert scheduler.get_job(EOD_JOB_ID).misfire_grace_time is None


def test_build_scheduler_adding_the_retention_job_leaves_the_capture_jobs_untouched():
    """The P0 guardrail every additive job in this module is held to."""
    scheduler = build_scheduler()
    for job_id, hour, minute in (
        (EOD_JOB_ID, "16", "20"),
        (SAFETY_NET_JOB_ID, "20", "0"),
        (BARS_JOB_ID, "17", "30"),
        (EXTENDED_JOB_ID, "16", "45"),
        (FLOWS_JOB_ID, "18", "30"),
        (DECISIONS_JOB_ID, "17", "45"),
    ):
        job = scheduler.get_job(job_id)
        assert job is not None, job_id
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert (fields["hour"], fields["minute"]) == (hour, minute), job_id
        assert job.misfire_grace_time is None, job_id


async def test_retention_prune_job_survives_an_unexpected_exception(monkeypatch, caplog):
    def boom(**kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("app.jobs.scheduler.prune_intraday_strike_detail", boom)
    monkeypatch.setattr("app.jobs.scheduler.get_session_factory", lambda: None)
    with caplog.at_level("ERROR"):
        await retention_prune_job()  # must not raise
    assert "retention_prune_job: unexpected top-level failure" in caplog.text


async def test_retention_prune_job_delegates_to_the_prune(monkeypatch):
    calls = []

    def fake_prune(*, session_factory):
        calls.append(session_factory)

    monkeypatch.setattr("app.jobs.scheduler.prune_intraday_strike_detail", fake_prune)
    monkeypatch.setattr("app.jobs.scheduler.get_session_factory", lambda: "factory")
    await retention_prune_job()
    assert calls == ["factory"]


# --- T18: 15-minute intraday polling ----------------------------------------------------------


def _enable_intraday(monkeypatch):
    monkeypatch.setattr(scheduler_module.settings, "INTRADAY_ENABLED", True)


def test_intraday_job_is_not_registered_when_disabled():
    """The default. Registration is conditional so `get_jobs()` states what will actually run
    rather than listing a job that always no-ops."""
    assert scheduler_module.settings.INTRADAY_ENABLED is False
    assert build_scheduler().get_job(INTRADAY_JOB_ID) is None


def test_intraday_job_is_registered_when_enabled(monkeypatch):
    _enable_intraday(monkeypatch)
    job = build_scheduler().get_job(INTRADAY_JOB_ID)
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day_of_week"] == "mon-fri"
    assert fields["hour"] == "9-16"
    assert fields["minute"] == "0,15,30,45"


def test_intraday_job_does_not_use_the_eod_misfire_policy(monkeypatch):
    """The inverse of every other capture job, deliberately: an intraday slot has nothing to
    rescue, since the endpoint serves only "now". A run that fires hours late adds an off-grid
    reading rather than recovering the missed one."""
    _enable_intraday(monkeypatch)
    scheduler = build_scheduler()
    assert scheduler.get_job(INTRADAY_JOB_ID).misfire_grace_time == 300
    assert scheduler.get_job(EOD_JOB_ID).misfire_grace_time is None


def test_enabling_intraday_leaves_every_other_job_untouched(monkeypatch):
    """The P0 guardrail. Turning polling on must not move the EOD capture by a minute."""
    _enable_intraday(monkeypatch)
    scheduler = build_scheduler()
    for job_id, hour, minute in (
        (EOD_JOB_ID, "16", "20"),
        (SAFETY_NET_JOB_ID, "20", "0"),
        (BARS_JOB_ID, "17", "30"),
        (EXTENDED_JOB_ID, "16", "45"),
        (FLOWS_JOB_ID, "18", "30"),
        (DECISIONS_JOB_ID, "17", "45"),
    ):
        job = scheduler.get_job(job_id)
        assert job is not None, job_id
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert (fields["hour"], fields["minute"]) == (hour, minute), job_id
        assert job.misfire_grace_time is None, job_id


def test_the_trigger_and_window_together_give_27_fires_a_session():
    """The number in the brief, derived rather than asserted by hand: the cron trigger fires on
    every quarter hour from 09:00 to 16:45, and the window guard keeps 09:45 through 16:15."""
    fires = [
        dt.time(hour, minute)
        for hour in range(9, 17)
        for minute in (0, 15, 30, 45)
    ]
    kept = [t for t in fires if INTRADAY_FIRST_CAPTURE <= t <= INTRADAY_LAST_CAPTURE]
    assert len(fires) == 32
    assert len(kept) == 27
    assert kept[0] == dt.time(9, 45)
    assert kept[-1] == dt.time(16, 15)


def test_the_window_ends_before_the_eod_job_fires():
    """16:15 is the last poll and 16:20 is the EOD capture. If the window ever reached 16:20 the
    two jobs would race for the same vendor payload."""
    assert INTRADAY_LAST_CAPTURE < dt.time(16, 20)


async def test_intraday_job_does_nothing_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler_module.settings, "INTRADAY_ENABLED", False)
    monkeypatch.setattr(
        scheduler_module, "capture_all_symbols", lambda *a, **k: calls.append(a)
    )
    await capture_intraday_job()
    assert calls == []


async def test_intraday_job_skips_outside_the_window(monkeypatch, caplog):
    """A 09:15 fire is inside the cron trigger but before the window, and must not capture --
    the feed is 15 minutes delayed, so it would return the pre-open book."""
    calls = []

    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 9, 11, 9, 15, tzinfo=_NY)

    _enable_intraday(monkeypatch)
    monkeypatch.setattr(scheduler_module.dt, "datetime", FakeDatetime)
    monkeypatch.setattr(
        scheduler_module, "capture_all_symbols", lambda *a, **k: calls.append(a)
    )
    with caplog.at_level(logging.DEBUG, logger="app.jobs.scheduler"):
        await capture_intraday_job()
    assert calls == []
    assert "outside the 09:45-16:15 polling window" in caplog.text


async def test_intraday_job_skips_on_a_holiday(monkeypatch, caplog):
    calls = []

    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            # 2026-01-01, a Thursday and a market holiday, at a time inside the window.
            return dt.datetime(2026, 1, 1, 12, 0, tzinfo=_NY)

    _enable_intraday(monkeypatch)
    monkeypatch.setattr(scheduler_module.dt, "datetime", FakeDatetime)
    monkeypatch.setattr(
        scheduler_module, "capture_all_symbols", lambda *a, **k: calls.append(a)
    )
    with caplog.at_level(logging.INFO, logger="app.jobs.scheduler"):
        await capture_intraday_job()
    assert calls == []
    assert "not a trading day" in caplog.text


async def test_intraday_job_captures_with_is_eod_false(monkeypatch):
    """The one property that separates an intraday row from an EOD row everywhere downstream --
    T32's retention, the catch-up guard, and every `eod_only` query."""
    seen = {}

    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 9, 11, 12, 0, tzinfo=_NY)

    async def fake_capture(symbols, *, is_eod, **kwargs):
        seen["symbols"] = list(symbols)
        seen["is_eod"] = is_eod
        return []

    _enable_intraday(monkeypatch)
    monkeypatch.setattr(scheduler_module.dt, "datetime", FakeDatetime)
    monkeypatch.setattr(scheduler_module, "capture_all_symbols", fake_capture)
    await capture_intraday_job()
    assert seen["is_eod"] is False
    assert seen["symbols"] == scheduler_module.settings.symbols


async def test_intraday_job_survives_an_unexpected_exception(monkeypatch, caplog):
    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 9, 11, 12, 0, tzinfo=_NY)

    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    _enable_intraday(monkeypatch)
    monkeypatch.setattr(scheduler_module.dt, "datetime", FakeDatetime)
    monkeypatch.setattr(scheduler_module, "capture_all_symbols", boom)
    with caplog.at_level("ERROR"):
        await capture_intraday_job()  # must not raise
    assert "capture_intraday_job: unexpected top-level failure" in caplog.text


# --- T73: pre-open bars refresh ---------------------------------------------------------------


def test_build_scheduler_registers_the_preopen_bars_job_at_0815_ny():
    """Before the 09:30 open and long after any overnight vendor publication, so a pre-session
    read of the regime strip is at worst one session behind rather than two."""
    scheduler = build_scheduler()
    job = scheduler.get_job(BARS_PREOPEN_JOB_ID)
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"], fields["day_of_week"]) == ("8", "15", "mon-fri")
    assert job.misfire_grace_time is None  # same policy as the 17:30 run it doubles


def test_the_two_bars_jobs_share_a_function_but_not_an_id():
    """One job function, two triggers. Distinct ids so either can be inspected, paused or run by
    hand without touching the other -- and so the 17:30 run stays exactly as it was."""
    scheduler = build_scheduler()
    preopen = scheduler.get_job(BARS_PREOPEN_JOB_ID)
    evening = scheduler.get_job(BARS_JOB_ID)
    assert preopen.func is evening.func
    assert preopen.id != evening.id
    evening_fields = {f.name: str(f) for f in evening.trigger.fields}
    assert (evening_fields["hour"], evening_fields["minute"]) == ("17", "30")
