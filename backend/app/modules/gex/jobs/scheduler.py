"""APScheduler wiring for the EOD capture job (PLAN.md Phase 1, TASKS.md T05).

`build_scheduler()` constructs an `AsyncIOScheduler` with `capture_eod` registered on a
Mon-Fri 16:20 America/New_York cron trigger; `app/main.py`'s FastAPI lifespan starts it. Kept
separate from `app/modules/gex/jobs/capture.py` (the actual fetch/persist logic) so the trigger/misfire
policy below -- the part that is easy to get subtly wrong and hard to notice being wrong -- is
readable in one place, and so tests can build a scheduler and inspect its registered job
without ever starting it (no real network, no real 16:20 wait).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.db import get_session_factory
from app.modules.gex.jobs.bars import update_bars_job
from app.modules.gex.jobs.calendar import is_trading_day
from app.modules.gex.jobs.capture import capture_all_symbols
from app.modules.gex.jobs.catchup import catch_up_missed_eod
from app.modules.gex.jobs.decisions import record_decisions_job
from app.modules.gex.jobs.flows import update_flows_job
from app.modules.gex.jobs.intraday_bars import update_intraday_bars
from app.modules.gex.jobs.memory import release_allocator
from app.modules.gex.jobs.retention import prune_intraday_strike_detail

__all__ = [
    "BARS_JOB_ID",
    "BARS_PREOPEN_JOB_ID",
    "DECISIONS_JOB_ID",
    "EOD_JOB_ID",
    "EXTENDED_JOB_ID",
    "FLOWS_JOB_ID",
    "INTRADAY_BARS_JOB_ID",
    "INTRADAY_FIRST_CAPTURE",
    "INTRADAY_JOB_ID",
    "INTRADAY_LAST_CAPTURE",
    "RETENTION_JOB_ID",
    "SAFETY_NET_JOB_ID",
    "bars_update_job",
    "build_scheduler",
    "capture_eod_job",
    "capture_eod_safety_net_job",
    "capture_extended_job",
    "capture_intraday_job",
    "decisions_update_job",
    "flows_update_job",
    "intraday_bars_job",
    "release_memory_listener",
    "retention_prune_job",
]

logger = logging.getLogger("app.modules.gex.jobs.scheduler")

EOD_JOB_ID = "capture_eod"
SAFETY_NET_JOB_ID = "capture_eod_safety_net"
BARS_JOB_ID = "bars_update"
BARS_PREOPEN_JOB_ID = "bars_update_preopen"
EXTENDED_JOB_ID = "capture_extended"
FLOWS_JOB_ID = "flows_update"
DECISIONS_JOB_ID = "decisions_update"
RETENTION_JOB_ID = "retention_prune"
INTRADAY_JOB_ID = "capture_intraday"
INTRADAY_BARS_JOB_ID = "intraday_bars_update"

#: T18's polling window, NY local and inclusive at both ends: 09:45 through 16:15, every 15
#: minutes, which is 27 fires per session.
#:
#: 09:45 rather than 09:30 because the feed is 15 minutes delayed -- a 09:30 poll would return
#: the pre-open book, not the first quarter hour of the session. 16:15 for the mirror-image
#: reason: it is the first poll whose delayed data reflects the 16:00 close, and it is where the
#: intraday series should end rather than duplicating what the 16:20 EOD job already stores.
#:
#: Deliberately not derived from `app.modules.gex.jobs.calendar.MARKET_OPEN`/`MARKET_CLOSE`: those are the
#: exchange's own hours, and conflating "when the market traded" with "when a delayed feed has
#: something new to say about it" is exactly the confusion that module's docstring warns about.
INTRADAY_FIRST_CAPTURE = dt.time(9, 45)
INTRADAY_LAST_CAPTURE = dt.time(16, 15)

#: T74's intraday-bar polling window, NY local. Wider than the capture window above: bars are
#: not delayed, so the 09:30 opening bucket is immediately useful, and 16:05 leaves the vendor a
#: few minutes to publish the bucket that closes the session.
INTRADAY_BARS_FIRST = dt.time(9, 30)
INTRADAY_BARS_LAST = dt.time(16, 5)

# `settings.TZ` (default "America/New_York") drives both the trigger's wall-clock time and
# the weekday/holiday check inside the job -- if this were ever pointed at another zone, both
# would move together rather than one silently staying NY-local.
_TZ = ZoneInfo(settings.TZ)


async def capture_eod_job() -> None:
    """Capture every symbol in `settings.symbols`, marking each `is_eod=True`.

    Guards against firing on a closed day even though the cron trigger already restricts to
    Mon-Fri: the trigger alone does not know about holidays, and this is the belt to that
    trigger's suspenders. Wrapped in a top-level try/except as a last line of defense --
    `capture_all_symbols`/`capture_snapshot` already turn every provider and storage failure
    into a logged `CaptureResult` rather than an exception, but a scheduler job that could
    still crash on some future bug (e.g. once T09's level-computation hook is wired in at the
    end of `capture_snapshot`) would silently deregister itself in APScheduler; this makes
    that structurally impossible.
    """
    today = dt.datetime.now(_TZ).date()
    if not is_trading_day(today):
        logger.info(
            json.dumps(
                {"event": "capture_eod_skipped", "date": today.isoformat(), "reason": "not a trading day"}
            )
        )
        return
    try:
        await capture_all_symbols(settings.symbols, is_eod=True)
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("capture_eod_job: unexpected top-level failure")


async def capture_eod_safety_net_job() -> None:
    """20:00 NY guard-railed retry of the 16:20 capture (T29).

    `capture_eod_job` above fires unconditionally at 16:20 -- if the process is alive to run
    it, fine. This job exists for the case that job never got the chance to run at all: a
    process that started at 16:05 (too early for the FastAPI-lifespan startup catch-up in
    `app/modules/gex/jobs/catchup.py` to find anything missing yet) and then crashed, rebooted, or was
    `docker compose down`-ed at 16:21 never gets another shot at 16:20 today. `_TZ`-local
    20:00 is late enough that almost any same-evening restart happens before it, early enough
    that it is still well within Cboe's "keeps serving the settled chain through the evening"
    window (see `catch_up_missed_eod`'s docstring).

    Delegates entirely to `catch_up_missed_eod`, which is what makes this safe to run even on
    a day the 16:20 job fired normally: the per-symbol "does today's is_eod row already exist"
    check makes an on-time day's 20:00 run a no-op, not a second capture.
    """
    try:
        await catch_up_missed_eod(settings.symbols)
    except Exception:  # same rationale as capture_eod_job: never take the scheduler down
        logger.exception("capture_eod_safety_net_job: unexpected top-level failure")


async def bars_update_job() -> None:
    """17:30 ET daily-bars update (T42), scheduled well after the 16:20 EOD capture and its
    20:00 safety net so the two data pipelines never compete for a slow evening connection.

    **P0 guardrail (T42 brief): a bars failure must be structurally incapable of affecting the
    option capture.** `update_bars_job` already turns every provider failure into a logged
    `BarUpdateResult` per symbol rather than raising (see its own docstring), but this wrapper
    exists for the same belt-and-suspenders reason `capture_eod_job` wraps
    `capture_all_symbols`: a future bug in this job must not be able to crash the scheduler
    thread and silently deregister *every* job on it, `capture_eod`/`capture_eod_safety_net`
    included. No trading-day guard is applied here (unlike `capture_eod_job`) -- Yahoo serves
    bars for weekends and holidays too (simply repeating the prior close), so there is no
    "closed today" case worth special-casing the way there is for a live options chain.
    """
    try:
        await update_bars_job()
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("bars_update_job: unexpected top-level failure")


async def capture_extended_job() -> None:
    """16:45 ET capture of `settings.extended_symbols` -- T47's sector/industry ETFs.

    Structurally the same job as `capture_eod_job` above (same trading-day guard, same
    `capture_all_symbols` call with `is_eod=True`, same top-level try/except), on a separate
    job id and 25 minutes later, for one reason: **this job must be structurally incapable of
    delaying or breaking the 16:20 EOD capture**, the P0 guardrail this task's brief states
    explicitly. Sharing `capture_eod_job`'s code path (even by calling it with a different
    symbol list) would put twenty-three extra sequential Cboe fetches between "the core five
    are safe" and "the job returned"; a separate job with its own trigger means the core
    capture is done and durably persisted for 25 minutes before this one even starts, and a
    bug or a slow evening here (see the plan's "likely first-contact failures": twenty-three
    captures might run long enough to approach the 17:30 bars job) cannot touch
    `capture_eod`'s result either way -- APScheduler jobs on the same scheduler share only the
    event loop, never each other's state.

    Per-symbol failures are already isolated by `capture_all_symbols`/`capture_snapshot` (one
    ETF's thin chain or a transient Cboe hiccup returns a `CaptureResult(ok=False)` for that
    symbol only); the try/except below is the same last-line-of-defense belt-and-suspenders as
    every other job in this module, not the primary isolation mechanism.
    """
    today = dt.datetime.now(_TZ).date()
    if not is_trading_day(today):
        logger.info(
            json.dumps(
                {
                    "event": "capture_extended_skipped",
                    "date": today.isoformat(),
                    "reason": "not a trading day",
                }
            )
        )
        return
    try:
        await capture_all_symbols(settings.extended_symbols, is_eod=True)
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("capture_extended_job: unexpected top-level failure")


async def flows_update_job() -> None:
    """18:30 ET ETF shares-outstanding update (T52), scheduled after the 17:30 ET bars job so
    the three evening data pipelines (option capture, bars, flows) never compete for a slow
    evening connection at the same moment.

    **P0 guardrail (T52 brief), same wording as `bars_update_job`'s own: a flows failure must
    be structurally incapable of affecting the option capture.** `update_flows_job` already
    turns every provider failure into a logged `FlowsFamilyResult` per family rather than
    raising (see its own docstring), but this wrapper exists for the identical
    belt-and-suspenders reason every other job in this module wraps its worker call: a future
    bug here must not be able to crash the scheduler thread and silently deregister every job
    on it, `capture_eod`/`capture_eod_safety_net` included. No trading-day guard, matching
    `bars_update_job`'s own reasoning -- issuer files simply repeat Friday's value over a
    weekend rather than erroring, so there is no "closed today" case worth special-casing.
    """
    try:
        await update_flows_job()
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("flows_update_job: unexpected top-level failure")


async def decisions_update_job() -> None:
    """17:45 ET decision-engine record-and-score (T61), fifteen minutes after the 17:30 bars
    job so today's bar is stored before today's opportunities are written and yesterday's are
    scored against it. Same P0 guardrail wording as `bars_update_job`: `record_decisions_job`
    already never raises, and this wrapper is the belt to those braces -- a bug here must not
    be able to deregister the capture jobs. No trading-day guard: on a holiday the pipeline
    finds no new snapshot, `record_decisions` inserts nothing, and evaluation is a no-op.
    """
    try:
        await record_decisions_job()
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("decisions_update_job: unexpected top-level failure")


async def capture_intraday_job() -> None:
    """One 15-minute intraday capture of every symbol in `settings.symbols`, `is_eod=False`.

    Three guards, in order, each for a different reason:

    1. `settings.INTRADAY_ENABLED` -- off by default; see the setting's own comment for why.
       Checked here rather than only at registration so that the reason a fire did nothing is
       in the logs, not merely absent from them.
    2. `is_trading_day` -- the cron trigger knows about weekends but not holidays, the same
       belt-and-suspenders `capture_eod_job` uses.
    3. The window. The trigger fires on every quarter hour from 09:00 to 16:45 (32 fires);
       this drops the five outside 09:45-16:15, leaving 27. Expressing it as a trigger plus a
       guard rather than as a more intricate cron expression keeps the window readable as two
       named constants that a human can check against the delay policy.

    Wrapped in the same top-level try/except as every other job here: `capture_all_symbols`
    already turns each symbol's provider and storage failures into a logged `CaptureResult`
    rather than an exception, and this is the belt to those braces -- a bug that escaped would
    otherwise deregister this job *and* the P0 capture jobs sharing the scheduler.

    **No retry on failure.** The cadence sits exactly on the free source's informal one request
    per symbol per 15 minutes, with no headroom, so a failed slot is skipped and logged. The
    next slot is fifteen minutes away and will ask again.
    """
    if not settings.INTRADAY_ENABLED:
        logger.debug(
            json.dumps({"event": "capture_intraday_skipped", "reason": "INTRADAY_ENABLED is false"})
        )
        return

    now_ny = dt.datetime.now(_TZ)
    if not is_trading_day(now_ny.date()):
        logger.info(
            json.dumps(
                {
                    "event": "capture_intraday_skipped",
                    "date": now_ny.date().isoformat(),
                    "reason": "not a trading day",
                }
            )
        )
        return
    if not (INTRADAY_FIRST_CAPTURE <= now_ny.time() <= INTRADAY_LAST_CAPTURE):
        logger.debug(
            json.dumps(
                {
                    "event": "capture_intraday_skipped",
                    "time": now_ny.time().isoformat(timespec="minutes"),
                    "reason": "outside the 09:45-16:15 polling window",
                }
            )
        )
        return

    try:
        await capture_all_symbols(settings.symbols, is_eod=False)
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("capture_intraday_job: unexpected top-level failure")


async def intraday_bars_job() -> None:
    """Five-minute intraday bar poll (T74), gated on `settings.INTRADAY_BARS_ENABLED`.

    Window guard is wider than T18's capture window and intentionally so: these are *bars*, not
    a delayed option chain, so the first useful bucket is the 09:30 open itself rather than
    09:45, and the last is the one that closes the 16:00 session. 16:05 gives the vendor a few
    minutes to publish the closing bucket.

    No trading-day guard beyond the window: on a holiday the vendor simply publishes no buckets
    for the day and `update_intraday_bars` upserts an empty list, which is a no-op. That is
    cheaper and less to get wrong than a second calendar check, and unlike an option capture
    there is no risk of storing a misleading row.
    """
    if not settings.INTRADAY_BARS_ENABLED:
        return
    now_ny = dt.datetime.now(_TZ)
    if not (INTRADAY_BARS_FIRST <= now_ny.time() <= INTRADAY_BARS_LAST):
        return
    try:
        await update_intraday_bars()
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("intraday_bars_job: unexpected top-level failure")


async def retention_prune_job() -> None:
    """21:00 ET prune of intraday `gex_by_strike` detail past its retention window (T32).

    Last of the evening's jobs, deliberately: it runs an hour after the 20:00 EOD safety net,
    so on a day when the 16:20 capture was missed and recovered late, the recovered snapshot is
    already stored and correctly flagged `is_eod=True` before anything considers deleting
    strike rows. Pruning by the *stored* flag rather than by wall-clock time is what makes that
    ordering merely tidy rather than load-bearing, but the margin costs nothing.

    Runs every day, not just Mon-Fri: retention is a function of row age, and a weekend is a
    perfectly good time to do the deleting. Same P0 guardrail as every other job here -- the
    prune already never raises for an empty table or a disabled setting, and this wrapper
    exists so that a bug which somehow did raise cannot deregister the capture jobs.

    Off the event loop via `asyncio.to_thread` for the same reason as the capture path's own DB
    work: `prune_intraday_strike_detail` is synchronous and commits per chunk, so calling it
    inline would block the loop for the length of a multi-hundred-thousand-row delete.
    """
    try:
        await asyncio.to_thread(
            prune_intraday_strike_detail, session_factory=get_session_factory()
        )
    except Exception:  # must never take the scheduler thread down with it
        logger.exception("retention_prune_job: unexpected top-level failure")


def release_memory_listener(event) -> None:
    """T88. After every job, ask Arrow and glibc to hand back what the job has already freed.

    On the scheduler rather than inside `capture_all_symbols`, for two reasons. It covers
    every job this worker runs -- capture, extended capture, bars, flows, decisions, intraday,
    retention -- instead of only the one that was investigated; and it fires *between* pieces
    of work by construction, rather than between symbols inside the fifteen minutes a capture
    has to complete in.

    `EVENT_JOB_ERROR` as well as `EVENT_JOB_EXECUTED`: a job that died partway has usually
    allocated the most and freed it on the way out, which is exactly when the arenas are worth
    walking.

    The INFO line is the point as much as the release is. This whole initiative exists because
    the growth was invisible until someone sampled the cgroup by hand, and a number in
    `docker compose logs` beside the job that produced it is the cheapest possible version of
    that instrument. `rss_delta_bytes` is `null` where RSS is unreadable (the Windows dev
    host) -- "not measured", never `0`.
    """
    result = release_allocator()
    logger.info(
        json.dumps(
            {
                "event": "release_allocator",
                "job_id": getattr(event, "job_id", None),
                "rss_before_bytes": result.rss_before,
                "rss_after_bytes": result.rss_after,
                "rss_delta_bytes": result.freed_bytes,
                "arrow_released": result.arrow_released,
                "malloc_trimmed": result.trimmed,
                "duration_ms": round(result.duration_ms, 3),
            }
        )
    )


def build_scheduler() -> AsyncIOScheduler:
    """Construct (but do not start) the scheduler with `capture_eod` registered.

    Misfire/coalesce policy -- the part that matters for "a laptop closed at 16:20 is the
    normal case for this user, not an edge case" (T05 brief):

    * `misfire_grace_time=None` -- APScheduler's default grace window (a handful of seconds)
      would treat a run that was due while the laptop was asleep as "too late" and drop it
      entirely once the process wakes up. That is exactly backwards for this app: the free
      Cboe source has no history, so a capture that runs hours late is still infinitely
      better than a capture that never runs. `None` means never drop a due run as stale.
    * `coalesce=True` -- if several fire times were missed while the process was down (e.g.
      the laptop was off for two trading days), run once on wake-up, not once per missed
      fire back-to-back. There is nothing to gain from replaying stale runs: Cboe only ever
      answers "now," so a coalesced catch-up run captures today's chain exactly like an
      on-time one would have, and the missed prior day is simply gone either way (a hole in
      the dataset the free tier can never backfill, no matter how the misfired job behaves).
    * `max_instances=1` -- a single daily job for three symbols does not need overlap
      protection in the ordinary case, but keeps a coalesced catch-up run from ever
      double-firing against an already-running one.
    """
    scheduler = AsyncIOScheduler(timezone=_TZ)
    scheduler.add_job(
        capture_eod_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=20, timezone=_TZ),
        id=EOD_JOB_ID,
        name="EOD option chain capture (SPX/SPY/QQQ/GLD/DIA)",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T29 safety net: same misfire policy as the 16:20 job, for the same reason -- a run that
    # is hours late is still better than one that never happened on this app's history-less
    # data source. See `capture_eod_safety_net_job`'s own docstring for why 20:00 and why it
    # is safe to be a no-op most days.
    scheduler.add_job(
        capture_eod_safety_net_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=20, minute=0, timezone=_TZ),
        id=SAFETY_NET_JOB_ID,
        name="EOD capture safety net (catches a missed 16:20 run)",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T42: daily bars, additive -- registered on its own job id/trigger, sharing only the
    # scheduler instance with the two option-capture jobs above, never their code path. Same
    # misfire/coalesce policy for the same reason as the EOD jobs: a bars update hours late
    # (laptop asleep, container down) is still better than one that never ran, since a missed
    # day is a permanent gap in the free Yahoo source's history this app keeps.
    scheduler.add_job(
        bars_update_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone=_TZ),
        id=BARS_JOB_ID,
        name="Daily bars update (SCAN_UNIVERSE)",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T73: a second bars run before the open, on the same job function and the same 5-day
    # overlap. Not redundant with 17:30 -- it exists because `app.modules.gex.providers.cboe_index` had not
    # published the current session's row when the 17:30 run asked for it on 2026-09-10, so the
    # six Cboe index symbols (^VIX, ^VIX9D, ^VIX3M, ^VIX6M, ^VVIX, ^SKEW) sat a day behind every
    # Yahoo-backed symbol. Between one evening's run and the next, `/api/gex/scan/cross-asset` was
    # therefore reporting a term structure and VRP from **two** sessions ago, which is what the
    # user saw on Opportunities.
    #
    # 08:15 NY is before the 09:30 open and long after any overnight publication, so the
    # pre-session read of the regime strip is at worst one session behind rather than two. The
    # job is idempotent by construction (`upsert_bars` no-ops on unchanged values), so running
    # the whole universe twice a day costs one extra pass and no duplicate rows.
    scheduler.add_job(
        bars_update_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=8, minute=15, timezone=_TZ),
        id=BARS_PREOPEN_JOB_ID,
        name="Daily bars pre-open refresh (catches vendors that publish overnight)",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T47: extended (sector/industry ETF) option capture, additive and separate from the core
    # 16:20/20:00 jobs above -- see `capture_extended_job`'s own docstring for why 16:45 (25
    # minutes after the core capture, still an hour before the 17:30 bars job) and why it is a
    # distinct job id rather than a parameter to `capture_eod_job`. Same misfire/coalesce
    # policy as every other capture job here, for the identical reason: a run hours late still
    # beats one that never happens on this free, history-less data source.
    scheduler.add_job(
        capture_extended_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=45, timezone=_TZ),
        id=EXTENDED_JOB_ID,
        name="Extended (sector/industry ETF) option chain capture",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T52: ETF shares-outstanding flows, additive and separate from every job above -- see
    # `flows_update_job`'s own docstring for why 18:30 (an hour after the 17:30 bars job) and
    # why it is its own job id. Same misfire/coalesce policy as every other job here, for the
    # identical reason: a run hours late still beats one that never happens, and this table
    # has no backfill path at all (issuer pages serve only "the latest published value").
    scheduler.add_job(
        flows_update_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=18, minute=30, timezone=_TZ),
        id=FLOWS_JOB_ID,
        name="ETF shares-outstanding flows update",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T61: same misfire/coalesce policy. A run hours late still records that day's levels
    # (the snapshot is already stored) and scores whatever bars have arrived since.
    scheduler.add_job(
        decisions_update_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=17, minute=45, timezone=_TZ),
        id=DECISIONS_JOB_ID,
        name="Decision engine record and score",
        coalesce=True,
        misfire_grace_time=None,
        max_instances=1,
        replace_existing=True,
    )
    # T18: 15-minute intraday polling, registered only when enabled so that
    # `scheduler.get_jobs()` is an honest statement of what will actually run rather than a list
    # containing a job that always no-ops.
    #
    # The misfire policy is the **opposite** of every capture job above, and this is the part
    # that is easy to get wrong. Those use `misfire_grace_time=None` because a late EOD capture
    # still captures the settled close, which is irreplaceable. An intraday slot has nothing to
    # rescue: the endpoint serves only "now", so a run that fires at 14:03 for the 10:00 slot
    # does not recover the 10:00 reading -- it just adds an off-grid one, which T20's timeline
    # would then plot between two real readings as though it belonged there. Five minutes of
    # grace covers an ordinary scheduling hiccup and nothing more.
    if settings.INTRADAY_ENABLED:
        scheduler.add_job(
            capture_intraday_job,
            trigger=CronTrigger(
                day_of_week="mon-fri", hour="9-16", minute="0,15,30,45", timezone=_TZ
            ),
            id=INTRADAY_JOB_ID,
            name="Intraday option chain capture (every 15 min, 09:45-16:15 NY)",
            coalesce=True,
            misfire_grace_time=300,
            max_instances=1,
            replace_existing=True,
        )

    # T74: five-minute intraday bars, registered only when enabled -- same rationale as the
    # capture job above, and the same inverted misfire policy for the same reason: a poll that
    # fires late does not recover the slot it missed, and here it does not even need to, since
    # every poll re-fetches the whole session.
    if settings.INTRADAY_BARS_ENABLED:
        scheduler.add_job(
            intraday_bars_job,
            trigger=CronTrigger(day_of_week="mon-fri", minute="*/5", hour="9-16", timezone=_TZ),
            id=INTRADAY_BARS_JOB_ID,
            name="Intraday bars poll (every 5 min, 09:30-16:05 NY)",
            coalesce=True,
            misfire_grace_time=120,
            max_instances=1,
            replace_existing=True,
        )

    # T32: nightly retention prune. The misfire policy here is the *opposite* of every capture
    # job above, and deliberately so. Those use `misfire_grace_time=None` because a capture
    # that runs hours late still captures something irreplaceable. A prune has nothing
    # irreplaceable to catch: it deletes by row age, so a run skipped tonight deletes exactly
    # the same rows plus one day's worth tomorrow. `coalesce=True` keeps a week of missed runs
    # from replaying as seven identical deletes on wake-up.
    scheduler.add_job(
        retention_prune_job,
        trigger=CronTrigger(hour=21, minute=0, timezone=_TZ),
        id=RETENTION_JOB_ID,
        name="Prune intraday gex_by_strike detail past its retention window",
        coalesce=True,
        # One hour, explicitly, rather than the module-wide `None`. A run that fires at 03:00
        # because the laptop was asleep at 21:00 deletes exactly what the 21:00 run would have;
        # a run that is dropped entirely costs one night of deferred deletion and nothing else.
        # Stating it rather than leaving it to APScheduler's default also keeps the attribute
        # present on the Job, which the scheduler tests read directly.
        misfire_grace_time=3600,
        max_instances=1,
        replace_existing=True,
    )

    # T88. Registered here rather than in `app/workers/gex_capture.py` so that a test can
    # assert it without starting a process, and so every scheduler built from this function
    # carries it -- the listener is part of what this scheduler *is*, not part of how the
    # worker happens to run it.
    scheduler.add_listener(release_memory_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    return scheduler
