"""`GET /api/gex/health/capture` -- "is my dataset still whole?" (TASKS.md T29).

One URL, per symbol: the last time anything was captured, the last time an `is_eod=True` row
landed, whether today's EOD row exists yet, and a `stale` flag an external monitor (or T28's
backup job) can alert on without re-deriving the trading-calendar logic itself. T28 (backups)
mentions a health endpoint too but explicitly leaves this one to T29 -- see TASKS.md's T29
scope note -- so the "EOD capture is more than one trading day old" alerting T28 describes is
implemented here, next to the calendar logic it depends on, rather than duplicated there.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.db import get_session_factory
from app.modules.gex.jobs.catchup import (
    has_eod_snapshot_today,
    last_completed_trading_day,
    previous_trading_day,
)
from app.modules.gex.providers.etf_flows import FAMILY_SYMBOLS, UNSUPPORTED_SYMBOLS
from app.modules.gex.storage.bars_repository import get_session_factory as get_bars_session_factory
from app.modules.gex.storage.bars_repository import last_bar_date
from app.modules.gex.storage.flows_repository import (
    get_session_factory as get_flows_session_factory,
)
from app.modules.gex.storage.flows_repository import last_as_of_date
from app.modules.gex.storage.repository import SnapshotRepository

__all__ = ["router"]

logger = logging.getLogger("app.modules.gex.api.health")

router = APIRouter(prefix="/health", tags=["health"])

# Same rationale as app/modules/gex/jobs/catchup.py's `_TZ`: NY calendar dates, derived from `settings.TZ`
# rather than hardcoded, so this endpoint and the catch-up guard it reads from never disagree
# about what "today" means.
_TZ = ZoneInfo(settings.TZ)


class SymbolCaptureHealth(BaseModel):
    underlying: str
    last_capture_at: dt.datetime | None
    last_eod_capture_at: dt.datetime | None
    eod_captured_today: bool
    stale: bool


class SymbolBarsHealth(BaseModel):
    """T42: per-symbol freshness for `daily_bars`, same shape as `SymbolCaptureHealth` above
    but keyed on `symbol` (a `SCAN_UNIVERSE` ticker, which may not have an option chain at all)
    rather than `underlying`.
    """

    symbol: str
    last_bar_date: dt.date | None
    stale: bool


class BarsHealthBlock(BaseModel):
    """T42 addition to `CaptureHealthResponse` -- a bars-job outage must be visible the same
    way a broken option capture already is, per the plan's "What the user sees" section.
    """

    symbols: list[SymbolBarsHealth]
    stale_count: int


class SymbolFlowsHealth(BaseModel):
    """T52: per-symbol freshness for `etf_shares_outstanding`, keyed on `symbol` (same
    universe as `SymbolBarsHealth`'s convention) rather than `underlying`."""

    symbol: str
    last_as_of_date: dt.date | None


class FlowsFamilyHealth(BaseModel):
    """One supported fund family's freshness: every symbol it covers plus the newest as-of
    date across all of them. `last_as_of_date` is deliberately the max of the *stored rows'*
    own as-of dates, not the last time the 18:30 job ran or last returned 200 -- per
    `docs/etf-flows-sources.md`'s corrected freshness rule, a fetch that succeeds and returns a
    week-old file (an issuer's URL silently changed shape, say) is not freshness, and reporting
    "last successful fetch" would hide exactly that failure mode.
    """

    family: str
    last_as_of_date: dt.date | None
    symbols: list[SymbolFlowsHealth]


class FlowsHealthBlock(BaseModel):
    """T52 addition to `CaptureHealthResponse`. `unsupported_symbols` names the four symbols
    (`app.modules.gex.providers.etf_flows.UNSUPPORTED_SYMBOLS`) with no working source at all, per the
    survey -- listed here so a reader of this endpoint sees the honest "no flow data" set
    rather than having to cross-reference the provider module to know it exists.
    """

    families: list[FlowsFamilyHealth]
    unsupported_symbols: list[str]


def _flows_health() -> FlowsHealthBlock:
    """T52: per-family `etf_shares_outstanding` freshness, one row per `FAMILY_SYMBOLS` entry.

    No staleness threshold is applied here, unlike `_bars_health`/`_is_stale` -- issuer files
    already lag a full trading day by design (the survey's central finding), so "one trading
    day behind" is this data's *normal* state, not a threshold worth alerting on the same way
    a broken option capture is. The as-of date is reported plainly; a human (or a future task)
    decides what counts as stale for this specific lag.
    """
    session_factory = get_flows_session_factory()
    families_out: list[FlowsFamilyHealth] = []
    for family, symbols in FAMILY_SYMBOLS.items():
        symbol_rows: list[SymbolFlowsHealth] = []
        dates: list[dt.date] = []
        for symbol in symbols:
            last = last_as_of_date(symbol, session_factory=session_factory)
            symbol_rows.append(SymbolFlowsHealth(symbol=symbol, last_as_of_date=last))
            if last is not None:
                dates.append(last)
        families_out.append(
            FlowsFamilyHealth(
                family=family,
                last_as_of_date=max(dates) if dates else None,
                symbols=symbol_rows,
            )
        )
    return FlowsHealthBlock(families=families_out, unsupported_symbols=list(UNSUPPORTED_SYMBOLS))


class CaptureHealthResponse(BaseModel):
    generated_at: dt.datetime
    symbols: list[SymbolCaptureHealth]
    #: T42, additive: daily-bars freshness alongside option-capture freshness. Existing fields
    #: above are untouched -- see this task's "additive only" constraint on `app/modules/gex/api/health.py`.
    bars: BarsHealthBlock
    #: T47, additive: sector/industry ETF capture freshness, kept in its own field rather than
    #: merged into `symbols` -- the plan's instruction is "extended symbols appear in capture
    #: freshness, labelled", and a reader that cannot tell `symbols` from `extended` apart
    #: cannot distinguish "the P0 16:20 job is broken" from "one sector ETF's 16:45 job had a
    #: thin day", which is exactly the ambiguity this split exists to avoid.
    extended: list[SymbolCaptureHealth]
    #: T52, additive: ETF shares-outstanding flows freshness, one entry per supported fund
    #: family. See `FlowsHealthBlock`'s own docstring for why this reports the as-of date of
    #: the newest *stored row*, not the last time the job merely ran.
    flows: FlowsHealthBlock


def _is_stale(last_eod_date: dt.date | None, completed: dt.date) -> bool:
    """More than one trading day behind `completed` (T28's alerting rule).

    `completed` is the most recent trading day whose EOD capture is expected to exist by now
    (`last_completed_trading_day`). Being exactly one trading day behind it is normal for most
    of any given trading day -- today's own EOD row does not exist until 16:20 -- so only two
    or more missing days counts as stale: `last_eod_date` older than the trading day
    immediately before `completed`.
    """
    if last_eod_date is None:
        return True
    return last_eod_date < previous_trading_day(completed)


def _bars_health(completed: dt.date) -> BarsHealthBlock:
    """T42: per-symbol `daily_bars` freshness for every symbol in `settings.scan_universe`.

    Reuses `_is_stale`'s exact rule (more than one trading day behind `completed`) rather than
    a separate bars-specific threshold -- the bars job runs well after the close (17:30 ET,
    same evening as the option EOD capture's 16:20 run and 20:00 safety net), so "stale"
    meaning the same thing in both blocks keeps one alerting rule instead of two a human has to
    remember are different.
    """
    session_factory = get_bars_session_factory()
    symbols_out: list[SymbolBarsHealth] = []
    stale_count = 0
    for symbol in settings.scan_universe:
        last = last_bar_date(symbol, session_factory=session_factory)
        stale = _is_stale(last, completed)
        if stale:
            stale_count += 1
        symbols_out.append(SymbolBarsHealth(symbol=symbol, last_bar_date=last, stale=stale))
    return BarsHealthBlock(symbols=symbols_out, stale_count=stale_count)


def _symbol_capture_health(
    underlying: str,
    repo: SnapshotRepository,
    session_factory: sessionmaker[Session],
    today: dt.date,
    completed: dt.date,
) -> SymbolCaptureHealth:
    """One underlying's row, shared by the core (`symbols`) and T47's `extended` blocks below
    so the two loops in `capture_health` cannot drift into computing staleness differently.
    """
    latest = repo.latest(underlying)
    eod_rows = repo.list(underlying, eod_only=True)
    last_eod = eod_rows[-1] if eod_rows else None
    last_eod_date = last_eod.captured_at.astimezone(_TZ).date() if last_eod is not None else None
    eod_today = has_eod_snapshot_today(underlying, session_factory, today)
    stale = _is_stale(last_eod_date, completed)
    if stale:
        logger.error(
            "health.capture: %s EOD capture is stale (last=%s, expected up to=%s)",
            underlying,
            last_eod_date.isoformat() if last_eod_date else None,
            completed.isoformat(),
        )
    return SymbolCaptureHealth(
        underlying=underlying,
        last_capture_at=latest.captured_at if latest is not None else None,
        last_eod_capture_at=last_eod.captured_at if last_eod is not None else None,
        eod_captured_today=eod_today,
        stale=stale,
    )


@router.get("/capture", response_model=CaptureHealthResponse)
def capture_health() -> CaptureHealthResponse:
    """Per-symbol capture freshness, computed fresh from the database on every call (no
    caching) -- this is a low-traffic, human-or-monitor-driven endpoint, not a hot path.
    """
    now = dt.datetime.now(dt.UTC)
    now_ny = now.astimezone(_TZ)
    today = now_ny.date()
    completed = last_completed_trading_day(now)

    session_factory = get_session_factory()
    symbols_out: list[SymbolCaptureHealth] = []
    extended_out: list[SymbolCaptureHealth] = []
    with session_factory() as session:
        repo = SnapshotRepository(session)
        for underlying in settings.symbols:
            symbols_out.append(
                _symbol_capture_health(underlying, repo, session_factory, today, completed)
            )
        # T47: same computation, same staleness rule, over `settings.extended_symbols` --
        # kept as a second loop rather than concatenating the two symbol lists so a stale
        # sector ETF never gets averaged into (or mistaken for) the P0 `symbols` block above.
        for underlying in settings.extended_symbols:
            extended_out.append(
                _symbol_capture_health(underlying, repo, session_factory, today, completed)
            )
    return CaptureHealthResponse(
        generated_at=now,
        symbols=symbols_out,
        bars=_bars_health(completed),
        extended=extended_out,
        flows=_flows_health(),
    )
