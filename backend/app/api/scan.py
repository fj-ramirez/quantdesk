"""Breakout ledger read API (T43, plans/continuation/01-breakout-ledger.md).

Two routes, both computing on request from `daily_bars` -- no new table, per the plan and
CLAUDE.md invariant 5's spirit (Postgres holds computed results plus an index; this is cheap
enough to just compute every time for a single-user app, so nothing is persisted at all):

* `GET /api/scan/breakouts?n=&k=&lookback=` -- one `app.scan.breakouts.BreakoutSummary` per
  symbol in `settings.scan_universe`, plus the union of every still-open (`pending`) event
  across the universe for the "Open breakouts" panel, plus an `excluded` list naming any
  symbol dropped for having too gappy a bars history to trust (see `_expected_trading_days`).
* `GET /api/scan/breakouts/{symbol}?n=&k=&lookback=` -- the raw event list for one symbol, for
  the detail view (event table + chart markers). Never 404s: a symbol with no bars at all (a
  typo, or one newly added to `SCAN_UNIVERSE` before its first bars job run) returns a clean
  empty `events` list, the same "clean empty state, not a synthetic error" contract
  `app.api.bars.get_bars` already uses for exactly this situation.

Response models are defined locally rather than in `app/api/schemas.py`: T43's edit list does
not include that file (a parallel T47 agent is working elsewhere in the API package at the same
time), and `app.api.bars` -- T42's own router, landed just before this one -- already
establishes the "small router-local Pydantic models" pattern for a new, self-contained router
that shares no response shape with the rest of the API.

`n`/`k`/`lookback` are validated here, not in `app.scan.breakouts`: the pure module accepts any
`n, k >= 1` (a test fixture may want a short, cheap-to-hand-build series), while the plan's
design document fixes the *product's* menu to `n in {20, 55}` and `k in {3, 5, 10}` -- an API
concern about what the frontend is allowed to ask for, not a mathematical constraint the engine
must enforce on itself.

**Gap detection uses `app.jobs.calendar.is_trading_day`, not a weekday approximation.** This
router does I/O already and is not bound by `app.scan`'s purity contract, so unlike
`app.scan.breakouts` it is free to import the calendar. That turned out not to be optional: an
early version approximated "expected trading days" as plain Mon-Fri weekdays and, measured
against the live database (2026-09-09), excluded **every single symbol** in the universe -- a
126-bar (~6-month) lookback ordinarily spans 4-5 real NYSE holidays, which alone read as ~3.8%
"missing" under a weekday-only count, comfortably past the plan's 2% exclusion threshold before
a single real gap was involved. `is_trading_day` fixes that by knowing about actual holidays
for the years the default lookback falls in (2026-2027 today; see that module's own maintenance
note about keeping the table current). See `docs/validation-scan.md` for the measurement.

**Measured timing (2026-09-09, `GET /api/scan/breakouts?n=20&k=5&lookback=126`, default query
params, against the live Docker Postgres with the full T42 backfill -- 47 symbols, most with
~1,250 bars of real history, i.e. *2.5x* the plan's "45 symbols x 500 bars" acceptance
scenario):** three consecutive requests via `curl -w '%{time_total}'` measured **0.90s, 0.93s,
1.03s** end to end (includes FastAPI request handling, 47 sequential `read_bars` round trips to
Postgres, `detect_events`/`summarize` for each, and JSON serialization) -- comfortably inside
the plan's 2s budget with roughly 1s of headroom, on a heavier data set than the acceptance
scenario specifies. `read_bars` is one indexed `SELECT ... WHERE symbol = ?` per symbol (see
`app.storage.bars_repository`), so this scales linearly in symbol count; no caching layer was
added, matching the single-user "not a scaling problem worth the complexity" reasoning
`app.api.gex`'s own module docstring already gives for the same trade-off.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.jobs.calendar import is_trading_day
from app.scan.breakouts import (
    DEFAULT_K,
    DEFAULT_LOOKBACK,
    DEFAULT_N,
    BreakoutEvent,
    BreakoutSummary,
    Outcome,
    detect_events,
    summarize,
)
from app.storage.bars_repository import get_session_factory, read_bars

__all__ = ["router"]

router = APIRouter(prefix="/scan", tags=["scan"])

#: The plan's "Design decisions" menu -- see the module docstring for why this is enforced here
#: and not inside `app.scan.breakouts`.
_VALID_N = (20, 55)
_VALID_K = (3, 5, 10)

#: Above this fraction of missing exchange trading days in the lookback window, a symbol is
#: excluded from the universe scan rather than fed a gappy series (plan's "Likely first-contact
#: failures"). Calibrated against real holiday-free data -- see the module docstring for why a
#: calendar-blind version of this check could not be calibrated at all.
_MAX_MISSING_BAR_FRACTION = 0.02


def _expected_trading_days(start: dt.date, end: dt.date) -> int:
    """Count of actual NYSE/Cboe trading days in `[start, end]`, holiday-aware.

    A plain day-by-day walk rather than a vectorized approach: `is_trading_day` is a Python
    function over `_HOLIDAYS_BY_YEAR`, not something `pandas`/`numpy` can vectorize over, and
    the lookback windows this is ever called with (a few hundred days at most) make the walk
    trivial regardless -- this is not the cost center in this route (fetching bars is).
    """
    if start > end:
        return 0
    count = 0
    day = start
    one_day = dt.timedelta(days=1)
    while day <= end:
        if is_trading_day(day):
            count += 1
        day += one_day
    return count


class BreakoutEventOut(BaseModel):
    """Mirrors `app.scan.breakouts.BreakoutEvent` -- see that dataclass's docstring for what
    each excursion/follow-through field means and exactly when it is populated versus `None`.
    """

    date: dt.date
    direction: str
    level: float
    close: float
    outcome: str
    resolved_at: dt.date | None
    bars_elapsed: int
    follow_through_atr: float | None
    excursion_atr: float | None
    mfe_atr: float | None
    mae_atr: float | None

    @classmethod
    def from_event(cls, event: BreakoutEvent) -> BreakoutEventOut:
        # `to_dict()` renders `date`/`resolved_at` as ISO strings (its JSON contract); pydantic
        # parses them straight back into `datetime.date` via the field annotation, so this is
        # not a lossy round trip -- just reuse of the one dict-building method the dataclass
        # already has, rather than a second hand-written field-by-field mapping to keep in sync.
        return cls(**event.to_dict())


class SymbolBreakoutSummaryOut(BaseModel):
    """One row of the "Breakouts" table: `Symbol | Events | Continued | Rate | Follow-through
    (ATR) | Last event | Status`. `status` is the last event's outcome (`"continued"`,
    `"failed"`, `"pending"`, or `None` for a symbol with zero events in the window) -- a plain
    read of `last_event.outcome`, not a separate computation, so the table's "Status" column
    and its "Last event" column can never disagree.
    """

    symbol: str
    events: int
    continued: int
    failed: int
    pending: int
    rate: float | None
    mean_follow_through_atr: float | None
    last_event: BreakoutEventOut | None
    status: str | None

    @classmethod
    def from_summary(cls, symbol: str, summary: BreakoutSummary) -> SymbolBreakoutSummaryOut:
        last = None if summary.last_event is None else BreakoutEventOut.from_event(summary.last_event)
        return cls(
            symbol=symbol,
            events=summary.events,
            continued=summary.continued,
            failed=summary.failed,
            pending=summary.pending,
            rate=summary.rate,
            mean_follow_through_atr=summary.mean_follow_through_atr,
            last_event=last,
            status=None if last is None else last.outcome,
        )


class OpenBreakoutOut(BaseModel):
    """One row of the "Open breakouts" panel: symbol, direction, level, bars elapsed, current
    excursion in ATR -- exactly the plan's "What the user sees" column list for that panel.
    """

    symbol: str
    direction: str
    level: float
    date: dt.date
    bars_elapsed: int
    excursion_atr: float | None


class ExcludedSymbolOut(BaseModel):
    """A symbol dropped from the universe scan, and why -- see `_MAX_MISSING_BAR_FRACTION`."""

    symbol: str
    reason: str


class BreakoutsResponse(BaseModel):
    n: int
    k: int
    lookback: int
    summaries: list[SymbolBreakoutSummaryOut]
    open_breakouts: list[OpenBreakoutOut]
    excluded: list[ExcludedSymbolOut]


class SymbolEventsResponse(BaseModel):
    symbol: str
    n: int
    k: int
    lookback: int
    events: list[BreakoutEventOut]


def _validate_n(n: int) -> int:
    if n not in _VALID_N:
        raise HTTPException(
            status_code=422, detail=f"n must be one of {_VALID_N}, got {n}"
        )
    return n


def _validate_k(k: int) -> int:
    if k not in _VALID_K:
        raise HTTPException(
            status_code=422, detail=f"k must be one of {_VALID_K}, got {k}"
        )
    return k


def _lookback_events(
    symbol: str,
    n: int,
    k: int,
    lookback: int,
    session_factory: sessionmaker,
) -> tuple[list[BreakoutEvent], float]:
    """Fetch `symbol`'s full stored history, detect every event in it, and return only the
    events within the last `lookback` bars -- plus the gap fraction over that same window.

    The *full* history (not a `start=`-bounded read) is fetched deliberately: `detect_events`
    needs `n` bars of context *before* the lookback window even begins in order to compute a
    correct range boundary for the earliest events inside that window, and reading everything
    is simpler and was measured fast enough in practice (see the module docstring's timing
    figure) that trimming the read itself was not worth the extra date arithmetic and its own
    edge cases.
    """
    bars = read_bars(symbol, session_factory=session_factory)
    if bars.empty:
        return [], 0.0

    window = bars.tail(lookback)
    if window.empty:
        return [], 0.0

    expected = _expected_trading_days(window["date"].iloc[0], window["date"].iloc[-1])
    gap_fraction = 0.0 if expected <= 0 else max(0.0, 1.0 - len(window) / expected)

    all_events = detect_events(bars, n, k)
    cutoff = window["date"].iloc[0]
    windowed = [e for e in all_events if e.date >= cutoff]
    return windowed, gap_fraction


@router.get("/breakouts", response_model=BreakoutsResponse)
def get_breakouts(
    n: Annotated[int, Query(description=f"Range length in bars, one of {_VALID_N}.")] = DEFAULT_N,
    k: Annotated[int, Query(description=f"Holding period in bars, one of {_VALID_K}.")] = DEFAULT_K,
    lookback: Annotated[
        int, Query(gt=0, description="Bars of history the summary covers.")
    ] = DEFAULT_LOOKBACK,
) -> BreakoutsResponse:
    """Per-symbol breakout summaries plus open events, for the whole `SCAN_UNIVERSE`.

    A symbol whose lookback window is missing more than `_MAX_MISSING_BAR_FRACTION` of its
    expected trading days is left out of `summaries`/`open_breakouts` and named in `excluded`
    instead, rather than being scanned against a series with unknown holes in it (plan's
    "Likely first-contact failures": "Symbols with gaps ... producing false breakouts across
    the gap").
    """
    _validate_n(n)
    _validate_k(k)

    session_factory = get_session_factory()
    summaries: list[SymbolBreakoutSummaryOut] = []
    open_breakouts: list[OpenBreakoutOut] = []
    excluded: list[ExcludedSymbolOut] = []

    for symbol in settings.scan_universe:
        events, gap_fraction = _lookback_events(symbol, n, k, lookback, session_factory)
        if gap_fraction > _MAX_MISSING_BAR_FRACTION:
            excluded.append(
                ExcludedSymbolOut(
                    symbol=symbol,
                    reason=(
                        f"{gap_fraction:.1%} of expected trading days missing bars in the last "
                        f"{lookback} bars (max {_MAX_MISSING_BAR_FRACTION:.0%})"
                    ),
                )
            )
            continue

        summary = summarize(events, lookback)
        summaries.append(SymbolBreakoutSummaryOut.from_summary(symbol, summary))
        open_breakouts.extend(
            OpenBreakoutOut(
                symbol=symbol,
                direction=str(event.direction),
                level=event.level,
                date=event.date,
                bars_elapsed=event.bars_elapsed,
                excursion_atr=event.excursion_atr,
            )
            for event in events
            if event.outcome is Outcome.PENDING
        )

    # Worst continuation rate first is not a well-defined sort with `rate=None` in the mix
    # (a symbol below the five-event floor has no rate to be worse or better than one), so the
    # ordering the plan asks for -- "sorted by continuation rate" -- puts every quoted rate
    # ahead of every `None` one, worst-quoted-rate first within that, ties broken by symbol so
    # the table is deterministic across requests.
    summaries.sort(key=lambda s: (s.rate is None, s.rate if s.rate is not None else 0.0, s.symbol))

    return BreakoutsResponse(
        n=n,
        k=k,
        lookback=lookback,
        summaries=summaries,
        open_breakouts=open_breakouts,
        excluded=excluded,
    )


@router.get("/breakouts/{symbol}", response_model=SymbolEventsResponse)
def get_symbol_breakouts(
    symbol: str,
    n: Annotated[int, Query(description=f"Range length in bars, one of {_VALID_N}.")] = DEFAULT_N,
    k: Annotated[int, Query(description=f"Holding period in bars, one of {_VALID_K}.")] = DEFAULT_K,
    lookback: Annotated[
        int, Query(gt=0, description="Bars of history the event list covers.")
    ] = DEFAULT_LOOKBACK,
) -> SymbolEventsResponse:
    """The event list for one symbol, for the detail view (event table + chart markers).

    `symbol` is normalized the same way `app.api.bars.get_bars` normalizes it
    (`.strip().upper()`), accepting the same `^VIX` / `%5EVIX` forms for the same reason. A
    symbol with no stored bars at all returns a clean empty `events` list -- not a 404 or a
    500 -- matching `get_bars`'s contract for exactly the same situation (never captured yet, a
    typo, or newly added to `SCAN_UNIVERSE` before its first bars job run). Unlike the universe
    route, a gappy history is **not** excluded here: this is a single, explicit lookup a user
    asked for by name, not a scan where a bad series would silently pollute an aggregate table.
    """
    _validate_n(n)
    _validate_k(k)

    canonical = symbol.strip().upper()
    session_factory = get_session_factory()
    events, _gap_fraction = _lookback_events(canonical, n, k, lookback, session_factory)

    return SymbolEventsResponse(
        symbol=canonical,
        n=n,
        k=k,
        lookback=lookback,
        events=[BreakoutEventOut.from_event(e) for e in events],
    )
