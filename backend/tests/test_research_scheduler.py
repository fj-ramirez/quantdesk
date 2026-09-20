"""T77: the research search scheduler and its worker.

Everything here inspects a *built* scheduler without starting one -- the same discipline
`tests/test_scheduler.py` applies to GEX, and for the same reason: the trigger and misfire
policy is the part that is easy to get subtly wrong and impossible to notice being wrong, and
a test that waited for a real 02:00 would never run.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import app.workers.research_search as worker
from app.modules.research.jobs import scheduler as sched
from app.modules.research.jobs.scheduler import SEARCH_JOB_ID, build_research_scheduler


@pytest.fixture
def cron_settings(monkeypatch):
    monkeypatch.setattr(sched.settings, "RESEARCH_SCHEDULE", "cron")
    monkeypatch.setattr(sched.settings, "RESEARCH_CRON", "0 2 * * *")
    monkeypatch.setattr(sched.settings, "TZ", "America/New_York")


# --- trigger shapes --------------------------------------------------------------------------


def test_cron_is_the_default_shape_and_reproduces_the_0200_habit(cron_settings):
    """EdgeLab's Windows task ran at 02:00 daily; that is what `cron` has to mean."""
    scheduler = build_research_scheduler()
    jobs = scheduler.get_jobs()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == SEARCH_JOB_ID
    assert isinstance(job.trigger, CronTrigger)

    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "2"
    assert fields["minute"] == "0"
    assert fields["day_of_week"] == "*"
    assert str(job.trigger.timezone) == "America/New_York"


def test_interval_shape_reproduces_the_vps_loop(monkeypatch):
    """The systemd unit ran `nightly.py --loop 60`."""
    monkeypatch.setattr(sched.settings, "RESEARCH_SCHEDULE", "interval")
    monkeypatch.setattr(sched.settings, "RESEARCH_INTERVAL_MINUTES", 60)

    jobs = build_research_scheduler().get_jobs()
    assert len(jobs) == 1
    assert isinstance(jobs[0].trigger, IntervalTrigger)
    assert jobs[0].trigger.interval == dt.timedelta(minutes=60)


def test_off_registers_nothing(monkeypatch):
    """A host that only ever runs cycles by hand."""
    monkeypatch.setattr(sched.settings, "RESEARCH_SCHEDULE", "off")
    assert build_research_scheduler().get_jobs() == []


def test_an_unknown_schedule_fails_loudly(monkeypatch):
    """A typo in `.env` must not silently mean "off" -- that is a worker that does nothing."""
    monkeypatch.setattr(sched.settings, "RESEARCH_SCHEDULE", "nightly")
    with pytest.raises(ValueError, match="RESEARCH_SCHEDULE"):
        build_research_scheduler()


def test_the_schedule_setting_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setattr(sched.settings, "RESEARCH_SCHEDULE", "  OFF ")
    assert build_research_scheduler().get_jobs() == []


# --- job policies ----------------------------------------------------------------------------


def test_a_second_cycle_cannot_start_while_one_is_running(cron_settings):
    """`max_instances=1`.

    A cycle that overruns its next fire must never run alongside itself: two concurrent searches
    sample the same combinations and race each other's writes for no gain. APScheduler refuses
    the second fire rather than queueing it.
    """
    job = build_research_scheduler().get_jobs()[0]
    assert job.max_instances == 1


def test_missed_fires_coalesce_into_one_run(cron_settings):
    """Four nights down is one cycle on return, not four."""
    assert build_research_scheduler().get_jobs()[0].coalesce is True


def test_misfire_grace_is_set_explicitly_and_generously(cron_settings):
    """APScheduler's default drops a job fired more than one second late.

    On a busy host that silently means a nightly cycle that never runs -- no error, no row, just
    a gap. This is the one policy whose default is actively wrong for a job like this.
    """
    grace = build_research_scheduler().get_jobs()[0].misfire_grace_time
    assert grace is not None
    assert grace >= 600


def test_there_is_no_catchup_job(cron_settings):
    """Deliberately unlike GEX, and the difference is the point.

    GEX catches up a missed capture because the Cboe feed serves only "now" and a missed 16:20
    is gone forever. A missed research cycle costs nothing: the registry remembers every
    combination tried, so the next cycle continues. A catch-up here would add a failure mode and
    buy nothing.
    """
    jobs = build_research_scheduler().get_jobs()
    assert [j.id for j in jobs] == [SEARCH_JOB_ID]


# --- the worker -------------------------------------------------------------------------------


async def test_worker_starts_the_scheduler_and_stops_on_signal(monkeypatch):
    """The container entrypoint's whole contract, without a database or a clock."""

    class _FakeScheduler:
        def __init__(self):
            self.started = False
            self.shutdown_calls: list[bool] = []

        def start(self):
            self.started = True

        def get_jobs(self):
            return [type("Job", (), {"id": SEARCH_JOB_ID})()]

        def shutdown(self, wait=True):
            self.shutdown_calls.append(wait)

    fake = _FakeScheduler()
    monkeypatch.setattr(worker, "build_research_scheduler", lambda: fake)

    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop=stop, wait_for_schema=False))
    await asyncio.sleep(0)
    assert fake.started
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    # `wait=False`: a cycle is tens of minutes of backtesting and SIGTERM must not queue behind
    # it. The in-flight cycle loses only its uncommitted trials.
    assert fake.shutdown_calls == [False]


async def test_worker_warns_when_it_has_no_jobs(monkeypatch, caplog):
    """`RESEARCH_SCHEDULE=off` is legitimate, but a silent worker and a broken one look alike."""

    class _EmptyScheduler:
        def start(self):
            pass

        def get_jobs(self):
            return []

        def shutdown(self, wait=True):
            pass

    monkeypatch.setattr(worker, "build_research_scheduler", lambda: _EmptyScheduler())

    stop = asyncio.Event()
    with caplog.at_level("WARNING", logger="app.workers.research_search"):
        task = asyncio.create_task(worker.run(stop=stop, wait_for_schema=False))
        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    assert "no jobs registered" in caplog.text


async def test_the_schema_probe_is_qualified_to_the_research_schema(monkeypatch):
    """T76's lesson, applied to the second worker before it could bite.

    `research.trials` is not in the connection's default schema (`public`, pinned there by
    `connect_args_for`), so an unqualified `has_table` finds nothing and the worker waits out
    its whole timeout against a perfectly migrated database.
    """
    monkeypatch.setattr(worker, "get_engine", lambda: object())

    seen: dict[str, object] = {}

    class _Inspector:
        def has_table(self, name: str, schema: str | None = None) -> bool:
            seen["name"] = name
            seen["schema"] = schema
            return True

    monkeypatch.setattr(worker, "inspect", lambda _engine: _Inspector())

    assert await worker._wait_for_schema(timeout=5.0) is True
    assert seen == {"name": "trials", "schema": "research"}
