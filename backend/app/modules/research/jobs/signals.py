"""Live price signals on a schedule, to Telegram (T136, plans/signal-alerts/).

Two jobs in the `research-search` worker, registered by :func:`add_signal_jobs`:

* **Hourly** (`SIGNALS_HOURLY_CRON`, default :05): force-refresh ES/NQ/YM/RTY 1h from Yahoo,
  evaluate the 120 z-score variants on each, record every transition in
  `research.signal_events`, and send one digest of the **fresh** ones.
* **Daily** (`SIGNALS_DAILY_CRON`, default 17:50 and 20:00 ET on weekdays): the continuation
  proxy, short and long, over `SIGNALS_CONTINUATION_UNIVERSE`'s daily bars from `gex.daily_bars`.

**Fresh only.** An event is alerted when its bar is the newest bar at the time it was recorded.
Events caught up after a gap are recorded with `late` set and never sent as though they were
current, because a demo entry taken on a twenty-hour-old signal tests nothing.

**Summarise, don't list.** Measured at T135: an active hour carries a median of 9 and up to 87
z-score transitions, because grid variants share thresholds and fire together. The digest gives
counts per instrument and action, plus the range of parameters that fired, and names only the
paper-watchlist variants -- the forward record of every variant is the table, not the message.

**Never raises.** Same posture as every scheduled job here: a failure is logged and the next
run is the retry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from app.core import notify
from app.core.config import settings
from app.modules.gex.storage.bars_repository import read_bars_many
from app.modules.research import data
from app.modules.research.config import load_config
from app.modules.research.signals import (
    CONTINUATION_SIDES,
    FUTURES_SYMBOLS,
    SignalEvent,
    continuation_events,
    zscore_events,
    zscore_variants,
)
from app.modules.research.storage.signal_events import record_events

__all__ = [
    "DAILY_JOB_ID",
    "HOURLY_JOB_ID",
    "WATCHLIST",
    "add_signal_jobs",
    "continuation_digest",
    "futures_digest",
    "run_continuation_signals",
    "run_futures_signals",
]

logger = logging.getLogger("app.modules.research.jobs.signals")

HOURLY_JOB_ID = "signals_hourly"
DAILY_JOB_ID = "signals_daily"

#: The paper-watchlist rows inside the grid, by the symbol they were promoted on. Named in the
#: digest when they fire there; every other variant is counted, not listed.
WATCHLIST = {
    "ES=F": "ZScoreDip_N160_E1p5_X0p5_HighVol",
    "YM=F": "ZScoreDip_N160_E3_X0p5_TrendDown",
    "RTY=F": "ZScoreDip_N80_E1p5_X0_Any",
}

_FUTURES_CAVEAT = (
    "Yahoo continuous futures: near a quarterly roll a signal can differ from the back-adjusted "
    "NinjaTrader backtest. Unproven variants; analysis only."
)
_CONTINUATION_CAVEAT = "Price-only proxy of CONTINUATION, no gamma filter. Unproven; analysis only."


def _p(x: float | None) -> str:
    return "·" if x is None else f"{x:.2f}".rstrip("0").rstrip(".")


def _values(events: Sequence[SignalEvent], key: str) -> str:
    vals = sorted({e.params[key] for e in events}, key=lambda v: (isinstance(v, str), v))
    return ",".join(f"{v:g}" if isinstance(v, float) else str(v) for v in vals)


def futures_digest(events: Sequence[SignalEvent]) -> str | None:
    """One message for fresh z-score events, or `None` when there are none. Pure."""
    if not events:
        return None
    newest = max(e.bar_ts for e in events)
    lines = [f"quantdesk signals: 1h bar {newest:%Y-%m-%d %H:%M} UTC"]
    for symbol in FUTURES_SYMBOLS:
        sym = [e for e in events if e.symbol == symbol]
        if not sym:
            continue
        enters = [e for e in sym if e.action == "ENTER"]
        exits = [e for e in sym if e.action == "EXIT"]
        counts = ", ".join(
            part for part in (
                f"ENTER LONG ×{len(enters)}" if enters else "",
                f"EXIT ×{len(exits)}" if exits else "",
            ) if part
        )
        lines.append("")
        lines.append(f"{symbol} {_p(sym[0].price)}: {counts}")
        for e in sym:
            if WATCHLIST.get(symbol) == e.signal:
                lines.append(f"  ★ {e.signal} {e.action} ({e.reason}) [paper watchlist]")
        for label, group in (("enters", enters), ("exits", exits)):
            if group:
                lines.append(
                    f"  {label}: n {_values(group, 'n')} · entry_z {_values(group, 'entry_z')} · "
                    f"exit_z {_values(group, 'exit_z')} · {_values(group, 'regime')}"
                )
    lines.append("")
    lines.append(_FUTURES_CAVEAT)
    return "\n".join(lines)


def continuation_digest(events: Sequence[SignalEvent]) -> str | None:
    """One message for fresh continuation-proxy events, or `None` when there are none. Pure."""
    if not events:
        return None
    session = max(e.bar_ts for e in events).date()
    lines = [f"quantdesk continuation proxy: {session} close", ""]
    for e in sorted(events, key=lambda e: (e.action != "ENTER", e.symbol, e.side)):
        if e.action == "ENTER":
            lines.append(
                f"• {e.symbol} {e.side} at the next open · stop {_p(e.stop)} · target {_p(e.target)} ({e.reason.split(';')[0]})"
            )
        else:
            lines.append(f"• {e.symbol} {e.side.lower()} exit: {e.reason} at {_p(e.price)}")
    lines.append("")
    lines.append(_CONTINUATION_CAVEAT)
    return "\n".join(lines)


def _send(message: str | None) -> None:
    if message is not None:
        notify.send(message, level=logging.INFO)


def run_futures_signals(
    *,
    refresh: bool = True,
    loader: Callable[[str], pd.DataFrame | None] | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """The hourly run, synchronously. Returns the number of fresh events alerted."""
    cfg = load_config()
    mcfg = cfg["markets"]["futures"]
    if refresh:
        data.refresh("futures", mcfg, list(FUTURES_SYMBOLS), "1h")
    load = loader or (lambda symbol: data.load_ohlcv("futures", mcfg, symbol, "1h"))
    events: list[SignalEvent] = []
    for symbol in FUTURES_SYMBOLS:
        df = load(symbol)
        if df is None or df.empty:
            logger.warning("signals: no 1h data for %s; skipped", symbol)
            continue
        for name, params in zscore_variants():
            events.extend(zscore_events(df, symbol, name, params))
    inserted = record_events(events, session_factory=session_factory)
    fresh = [e for e in inserted if not e.late]
    _send(futures_digest(fresh))
    logger.info("signals hourly: %d transitions in window, %d new, %d fresh", len(events), len(inserted), len(fresh))
    return len(fresh)


def run_continuation_signals(
    *,
    universe: Sequence[str] | None = None,
    bars_session_factory: sessionmaker[Session] | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """The daily run, synchronously. Returns the number of fresh events alerted."""
    symbols = list(universe) if universe is not None else [
        s.strip().upper() for s in settings.SIGNALS_CONTINUATION_UNIVERSE.split(",") if s.strip()
    ]
    bars_by_symbol = read_bars_many(symbols, session_factory=bars_session_factory)
    events: list[SignalEvent] = []
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol)
        if bars is None or bars.empty:
            logger.warning("signals: no daily bars for %s; skipped", symbol)
            continue
        for side in CONTINUATION_SIDES:
            events.extend(continuation_events(bars, symbol, side))
    inserted = record_events(events, session_factory=session_factory)
    fresh = [e for e in inserted if not e.late]
    _send(continuation_digest(fresh))
    logger.info("signals daily: %d transitions in window, %d new, %d fresh", len(events), len(inserted), len(fresh))
    return len(fresh)


async def _guarded(name: str, fn: Callable[[], int]) -> None:
    try:
        await asyncio.to_thread(fn)
    except Exception:  # a scheduled job must not take the scheduler down; the next run retries
        logger.exception("signals %s run failed", name)


async def hourly_signals_job() -> None:
    await _guarded("hourly", run_futures_signals)


async def daily_signals_job() -> None:
    await _guarded("daily", run_continuation_signals)


def add_signal_jobs(scheduler: AsyncIOScheduler) -> list[str]:
    """Register the two jobs on `scheduler` unless their cron setting is `off`; return the ids.

    Separate from `build_research_scheduler` on purpose: the search cycle's own policy (one job,
    no catch-up) is pinned by its tests, and these jobs have nothing to do with it.
    """
    tz = ZoneInfo(settings.TZ)
    added = []
    for job_id, cron, fn, name in (
        (HOURLY_JOB_ID, settings.SIGNALS_HOURLY_CRON, hourly_signals_job, "z-score signals on 1h futures"),
        (DAILY_JOB_ID, settings.SIGNALS_DAILY_CRON, daily_signals_job, "continuation proxy on daily bars"),
    ):
        if cron.strip().lower() == "off":
            logger.info("%s is off", job_id)
            continue
        scheduler.add_job(
            fn, trigger=CronTrigger.from_crontab(cron, timezone=tz), id=job_id, name=name,
            coalesce=True, max_instances=1, misfire_grace_time=600, replace_existing=True,
        )
        added.append(job_id)
    logger.info("signal jobs scheduled: %s (%s)", added, notify.notifier_status())
    return added

