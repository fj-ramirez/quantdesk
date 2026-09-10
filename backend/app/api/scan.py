"""Breakout ledger, trend/chop scorer and sector-rotation read API (T43, plans/continuation/01-
breakout-ledger.md; T45, plans/continuation/02-trend-chop-scorer.md adds the `/trend` routes;
T50, plans/continuation/04-sector-rotation.md adds `/rotation`).

Routes, all computing on request from `daily_bars` -- no new table, per the plan and
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
* `GET /api/scan/trend` -- one `app.scan.trend.TrendRow` per `settings.scan_universe` symbol
  (T45): the ADX/ER/CHOP/VR components, RV20/IV30/IV-RV hint, cross-sectional percentiles and
  the composite. Never excludes a symbol the way `/breakouts` does for gappy history -- every
  universe symbol gets a row, with `None` components wherever its history or chain does not
  support them (see `_lookup_iv30` below and `app.scan.trend.score_symbol`'s own docstring).
* `GET /api/scan/trend/{symbol}` -- one symbol's current components plus their rolling
  126-bar history, for the detail panel's sparklines. Same "no bars at all is a clean empty
  result, not a 404" contract as `/breakouts/{symbol}`.
* `GET /api/scan/rotation?group=&benchmark=&weeks=` -- one RRG-approximation trail
  (`rs_ratio_approx`/`rs_momentum_approx` per week) plus 1/4/13-week relative return per symbol
  in the chosen `app.scan.groups.GROUPS` group, plus one sector-level breadth reading (T50).
  Unlike every other route in this module, `group` has a fixed 422-rejected menu (`sectors`,
  `industries`, `assets` -- `app.scan.groups.GROUPS`'s own keys) rather than an open string,
  because there is no sensible "compute it anyway" fallback for a group name this module does
  not know the membership of, unlike `n`/`k`/`lookback`'s open-but-restricted-by-convention
  numeric ranges. See this route's own docstring below for the daily-bars lookback window it
  fetches and why, and `app.scan.rotation`'s module docstring for the cross-symbol alignment
  discipline every division in this route's pipeline depends on.

**T45's IV lookup lives here, not in `app.scan.trend`** (that module's purity contract:
CLAUDE.md invariant 1, and the same caller/pure split T43 already used for its own calendar
dependency). `_lookup_iv30` mirrors `app.api.report._build`'s read path -- one `select` for the
latest `Snapshot` row, `resolve_snapshot_path` + `read_snapshot` + `to_frame`, then
`app.gex.report.iv_regime` -- trimmed to the single `atm_iv` number `score_symbol` needs;
running the *full* `compute_all`/`build_report` pipeline here (walls, net GEX, max pain) would
be wasted work this route never reads. A symbol that fails at any step of that lookup --
not one of the 28 option-covered `Underlying` members at all, never captured, or indexed with a
Parquet file that has since gone missing -- degrades to `iv30=None` rather than failing the
whole universe scan: most of `SCAN_UNIVERSE` (19 of 47 symbols) has no chain at all, and that
is the ordinary case this route runs against on every request, not an error condition.

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

**T45's `GET /api/scan/trend` measured timing (2026-09-09, same live database, full universe,
no query params):** four consecutive requests via `curl -w '%{time_total}'` measured **3.51s,
3.61s, 3.72s, 3.79s** end to end. Unlike `/breakouts`, this is dominated by `_lookup_iv30`, not
by bars I/O: measured directly (a plain in-process timer, no HTTP) against the same database,
`read_bars` for all 47 symbols is only **0.57s**, while `_lookup_iv30` for the same 47 symbols
(most of them a fast `None` -- see below -- but 28 of them a real Parquet read) is **3.09s**.
That is not a caching gap this route failed to add; it is the honest cost of flattening 28
real option chains (`to_frame`, same flatten `app.api.report` names as "the expensive step") on
every single request, one of which (SPX) alone carries on the order of 25,000+ contract rows.
No caching layer was added for the same single-user reasoning `/breakouts` already gives, and
there is no plan-stated latency budget for `/trend` the way T43's plan names one for
`/breakouts` (2s) -- this paragraph exists so a reader has the real number and its breakdown
rather than an unstated implicit target. `GET /api/scan/trend/{symbol}` (single symbol, `SPY`,
which does have a chain) measured **0.26-0.33s** across three requests -- one `read_bars` call
plus one `_lookup_iv30` call, not 47 of each. `GET /api/scan/trend/NOPEXYZ` (zero stored bars)
measured **0.01s**, `200 OK`, empty `history`, all-`None` `current` -- confirming the same
"clean empty result, not a 404 or 500" contract `/breakouts/{symbol}` already established,
against the live server rather than only `tests/test_scan_api.py`'s offline equivalent.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.gex.engine import to_frame
from app.gex.report import iv_regime
from app.jobs.calendar import is_trading_day
from app.jobs.capture import get_session_factory as get_gex_session_factory
from app.models.chain import Underlying
from app.models.db import Snapshot
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
from app.scan.groups import BENCHMARKS, DEFAULT_BENCHMARK, GROUPS, SECTORS
from app.scan.indicators import (
    ADX_PERIOD,
    CHOP_PERIOD,
    ER_PERIOD,
    RV_PERIOD,
    adx,
    choppiness,
    efficiency_ratio,
    realized_vol,
)
from app.scan.rotation import (
    RELATIVE_RETURN_WINDOWS,
    RRG_ZSCORE_WINDOW,
    SectorBreadth,
    relative_returns,
    rrg_approx,
    sector_breadth,
    weekly_closes,
)
from app.scan.trend import (
    TREND_LOOKBACK,
    TrendComponents,
    TrendRow,
    rank_universe,
    score_symbol,
)
from app.storage.bars_repository import get_session_factory, read_bars, read_universe_closes
from app.storage.parquet import read_snapshot, resolve_snapshot_path

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


# --------------------------------------------------------------------------------------------
# T45: trend/chop scorer routes (plans/continuation/02-trend-chop-scorer.md)
# --------------------------------------------------------------------------------------------


def _lookup_iv30(
    symbol: str,
    gex_session_factory: sessionmaker,
    *,
    data_dir: str | Path | None = None,
) -> float | None:
    """The ATM ~30-day implied vol off `symbol`'s most recently captured option-chain snapshot,
    or `None` -- see this module's docstring for the three distinct reasons that can happen and
    why none of them is an error worth failing the whole universe scan over.

    Mirrors `app.api.report._build`'s read path (one `Snapshot` lookup, `resolve_snapshot_path`
    + `read_snapshot` + `to_frame`, then `app.gex.report.iv_regime`) trimmed to the single
    number `app.scan.trend.score_symbol` needs. `data_dir` defaults to `settings.DATA_DIR` (via
    `resolve_snapshot_path`'s own default) so production code never passes it; tests inject a
    `tmp_path` the same way `write_snapshot`/`resolve_snapshot_path` already support elsewhere.
    """
    try:
        canonical = Underlying(symbol).value
    except ValueError:
        return None  # not one of the 28 option-covered underlyings at all

    with gex_session_factory() as session:
        stmt = (
            select(Snapshot)
            .where(Snapshot.underlying == canonical)
            .order_by(Snapshot.captured_at.desc())
            .limit(1)
        )
        row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None  # a covered underlying, but never captured yet

    try:
        path = resolve_snapshot_path(row, data_dir=data_dir)
        snapshot = read_snapshot(path)
    except FileNotFoundError:
        # Indexed but the Parquet file is missing. `app.api.report` 404s the whole request for
        # this (it is the one thing the caller explicitly asked for); a universe scan instead
        # degrades this one symbol's IV to `None` and keeps going, the same "one bad symbol
        # does not poison the table" posture `/breakouts`'s `excluded` list embodies for a
        # gappy bars history.
        return None

    frame = to_frame(snapshot)
    regime = iv_regime(frame, snapshot.spot)
    return regime.atm_iv


class TrendComponentsOut(BaseModel):
    """Mirrors `app.scan.trend.TrendComponents` -- see that dataclass's docstring for exactly
    when each field is `None` versus a number.
    """

    adx14: float | None
    er20: float | None
    chop14: float | None
    vr: float | None
    vr_z: float | None
    rv20: float | None
    iv30: float | None
    iv_rv_ratio: float | None

    @classmethod
    def from_components(cls, components: TrendComponents) -> TrendComponentsOut:
        return cls(**components.to_dict())


class TrendRowOut(TrendComponentsOut):
    """One row of the `/scan?view=trend` table: `symbol` plus every `TrendComponentsOut` field
    plus each component's cross-sectional percentile and the composite. Flat (not nested)
    because the frontend table is one row per symbol with one cell per column -- there is no
    natural place a nested `components` object would be unpacked to anyway.
    """

    symbol: str
    adx_pct: float | None
    er_pct: float | None
    chop_pct: float | None
    vr_pct: float | None
    composite: float | None

    @classmethod
    def from_row(cls, row: TrendRow) -> TrendRowOut:
        # `to_dict()` already merges `components` flat with `symbol`/`*_pct`/`composite` --
        # reuse that one dict-building method rather than a second field-by-field mapping that
        # could drift out of sync with it, the same reasoning `BreakoutEventOut.from_event`
        # gives for doing the same thing with `BreakoutEvent.to_dict()`.
        return cls(**row.to_dict())


class TrendResponse(BaseModel):
    rows: list[TrendRowOut]


class TrendHistoryPointOut(BaseModel):
    """One day of the detail panel's sparkline history. `vr`/`vr_z`/`iv30`/`iv_rv_ratio` are
    deliberately absent here -- `app.scan.indicators.variance_ratio` is not a rolling `Series`
    (see that module's docstring) so there is no per-day VR reading to plot, and no IV history
    is ever persisted past the latest snapshot (`app.api.report.get_report`'s own docstring:
    "Nothing in the schema persists past ATM implied vols"). Both are still returned once, as
    the current single value, in `SymbolTrendResponse.current`.
    """

    date: dt.date
    adx14: float | None
    er20: float | None
    chop14: float | None
    rv20: float | None


class SymbolTrendResponse(BaseModel):
    symbol: str
    current: TrendComponentsOut
    history: list[TrendHistoryPointOut]


def _symbol_components(
    symbol: str,
    bars_session_factory: sessionmaker,
    gex_session_factory: sessionmaker,
    *,
    data_dir: str | Path | None = None,
) -> tuple[TrendComponents, pd.DataFrame]:
    """Fetch `symbol`'s bars, look up its `iv30`, and score it -- the one sequence both trend
    routes need, factored out so the universe route and the single-symbol route can never
    silently diverge on how a row is built. Returns `(components, bars)` because the detail
    route also needs `bars` itself to build the sparkline history; the universe route discards
    the second element.
    """
    bars = read_bars(symbol, session_factory=bars_session_factory)
    iv30 = _lookup_iv30(symbol, gex_session_factory, data_dir=data_dir)
    return score_symbol(bars, iv30), bars


@router.get("/trend", response_model=TrendResponse)
def get_trend() -> TrendResponse:
    """Trend/chop components, percentiles and composite for every symbol in `SCAN_UNIVERSE`.

    Unlike `/breakouts`, no symbol is ever excluded for a gappy history -- `app.scan.indicators`
    already reports `None` per-component for whatever it cannot compute (this route does not
    duplicate that decision with a second, coarser gap check), and a `None` component just
    narrows that one symbol's contribution to the cross-sectional percentiles rather than
    invalidating the whole row (see `app.scan.trend.rank_universe`'s docstring).

    Rows are sorted by `composite` descending (most "trending" first), symbols with no
    composite at all (nothing finite to average) sorted last, ties broken by symbol -- the
    same "quoted values first, `None` last, deterministic" ordering `/breakouts` already uses
    for `rate`.
    """
    bars_session_factory = get_session_factory()
    gex_session_factory = get_gex_session_factory()

    components_by_symbol: dict[str, TrendComponents] = {}
    for symbol in settings.scan_universe:
        components, _bars = _symbol_components(symbol, bars_session_factory, gex_session_factory)
        components_by_symbol[symbol] = components

    rows = rank_universe(components_by_symbol)
    rows.sort(
        key=lambda r: (r.composite is None, -(r.composite or 0.0), r.symbol)
    )

    return TrendResponse(rows=[TrendRowOut.from_row(r) for r in rows])


@router.get("/trend/{symbol}", response_model=SymbolTrendResponse)
def get_symbol_trend(symbol: str) -> SymbolTrendResponse:
    """One symbol's current trend/chop components plus their rolling `TREND_LOOKBACK`-bar
    history, for the detail panel's sparklines.

    `symbol` is normalized the same way `app.api.bars.get_bars` and `get_symbol_breakouts`
    normalize it (`.strip().upper()`). A symbol with no stored bars at all returns a clean
    `current` full of `None`s and an empty `history` -- not a 404 or a 500 -- the same
    "never captured yet, a typo, or newly added to `SCAN_UNIVERSE`" contract those two routes
    already use.
    """
    canonical = symbol.strip().upper()
    bars_session_factory = get_session_factory()
    gex_session_factory = get_gex_session_factory()

    components, bars = _symbol_components(canonical, bars_session_factory, gex_session_factory)

    history: list[TrendHistoryPointOut] = []
    if not bars.empty:
        adx_series = adx(bars, ADX_PERIOD)
        er_series = efficiency_ratio(bars, ER_PERIOD)
        chop_series = choppiness(bars, CHOP_PERIOD)
        rv_series = realized_vol(bars, RV_PERIOD)

        window = bars.tail(TREND_LOOKBACK)
        for i in window.index:
            history.append(
                TrendHistoryPointOut(
                    date=bars.loc[i, "date"],
                    adx14=None if pd.isna(adx_series.loc[i]) else float(adx_series.loc[i]),
                    er20=None if pd.isna(er_series.loc[i]) else float(er_series.loc[i]),
                    chop14=None if pd.isna(chop_series.loc[i]) else float(chop_series.loc[i]),
                    rv20=None if pd.isna(rv_series.loc[i]) else float(rv_series.loc[i]),
                )
            )

    return SymbolTrendResponse(
        symbol=canonical,
        current=TrendComponentsOut.from_components(components),
        history=history,
    )


# --------------------------------------------------------------------------------------------
# T50: sector-rotation routes (plans/continuation/04-sector-rotation.md)
# --------------------------------------------------------------------------------------------

#: The plan's "the common open approximation ... never claiming parity" disclaimer, carried
#: into the API response as both a documented `note` field and the `_approx`-suffixed field
#: names below -- CLAUDE.md's/this task's correctness-of-claims requirement, verbatim in the
#: response a page's info tooltip (T51/07-ui.md) reads from, not only in this module's or
#: `app.scan.rotation`'s own docstring.
_RRG_APPROXIMATION_NOTE = (
    "rs_ratio_approx / rs_momentum_approx are the common open approximation of JdK RS-Ratio / "
    "RS-Momentum (rolling z-scores of price/benchmark and its own week-over-week change). "
    "JdK RS-Ratio and RS-Momentum are a proprietary, patented construction; this is not that "
    "indicator and does not claim to match it."
)

#: How many calendar days of *daily* closes this route fetches before resampling to weekly and
#: running `app.scan.rotation.rrg_approx`. Sized for the largest `weeks` this route accepts
#: (`_MAX_ROTATION_WEEKS`) plus `2 * RRG_ZSCORE_WINDOW` weeks of z-score warm-up (the first
#: rolling z-score needs `RRG_ZSCORE_WINDOW` weeks to seed `rs_ratio_approx`, and the second
#: needs another `RRG_ZSCORE_WINDOW` on top of that to seed `rs_momentum_approx` -- see
#: `app.scan.rotation.rrg_approx`'s own docstring), converted to calendar days at roughly 7
#: days/week and doubled again as a margin for weekends/holidays that thin out a week's own
#: bin without changing how many *weeks* of history exist: `(26 + 28) * 7 * 2` rounds up to
#: 730 (two years) -- comfortably inside the ~5 years of history T42's backfill actually holds
#: (see this route's own live measurement below) and this route's own test fixtures build much
#: shorter series directly, so this constant is exercised for real only against the live
#: database, never by a unit test.
_ROTATION_LOOKBACK_DAYS = 730

#: `weeks=` query menu: plan default 10 (the "standard reading" 10-week RRG trail); capped at
#: 26 (half a year of weekly points) so a request can never ask for more trail than
#: `_ROTATION_LOOKBACK_DAYS` was sized to comfortably support.
_DEFAULT_ROTATION_WEEKS = 10
_MAX_ROTATION_WEEKS = 26

#: Same rationale as `app.api.health`'s own `_TZ`: "today" for a daily-bars lookback window
#: must be the exchange-local (NY) calendar date, not whatever date UTC happens to be at the
#: moment of the request (the two disagree for several hours every trading day).
_TZ = ZoneInfo(settings.TZ)


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).astimezone(_TZ).date()


def _validate_group(group: str) -> str:
    if group not in GROUPS:
        raise HTTPException(
            status_code=422,
            detail=f"group must be one of {sorted(GROUPS)}, got {group!r}",
        )
    return group


def _validate_benchmark(benchmark: str) -> str:
    if benchmark not in BENCHMARKS:
        raise HTTPException(
            status_code=422,
            detail=f"benchmark must be one of {BENCHMARKS}, got {benchmark!r}",
        )
    return benchmark


class RotationPointOut(BaseModel):
    """One week of one symbol's RRG-approximation trail. `rs_ratio_approx`/`rs_momentum_approx`
    are `None` during the z-score's own warm-up (see `app.scan.rotation.rrg_approx`'s
    docstring for exactly how long that is) -- never a partially-windowed number.
    """

    date: dt.date
    rs_ratio_approx: float | None
    rs_momentum_approx: float | None


class RotationSymbolOut(BaseModel):
    """One symbol's full RRG trail (oldest to newest, already trimmed to the requested `weeks`)
    plus its 1/4/13-week relative return versus the chosen benchmark. `return_*` fields mirror
    `app.scan.rotation.relative_returns`'s own `f"return_{n}"` columns, named out here rather
    than left as a dict so the response schema is self-documenting.
    """

    symbol: str
    trail: list[RotationPointOut]
    return_5: float | None
    return_20: float | None
    return_65: float | None


class SectorBreadthOut(BaseModel):
    """Mirrors `app.scan.rotation.SectorBreadth` -- see that dataclass's docstring for exactly
    when each field is `None`/excluded rather than a fabricated reading. `label` is the plan's
    own required wording ("sector-level breadth") carried onto the wire so a frontend need not
    hard-code the disclaimer itself.
    """

    label: str = "sector-level breadth"
    equal_weight_ratio: float | None
    equal_weight_ratio_change_20d: float | None
    above_20d: int
    evaluated_20d: int
    above_50d: int
    evaluated_50d: int

    @classmethod
    def from_breadth(cls, breadth: SectorBreadth) -> SectorBreadthOut:
        return cls(**breadth.to_dict())


class RotationResponse(BaseModel):
    group: str
    benchmark: str
    weeks: int
    w: int
    symbols: list[RotationSymbolOut]
    breadth: SectorBreadthOut
    note: str


def _clean_float(value: float) -> float | None:
    """`NaN`/`inf` -> `None`, everything else passed through as a plain `float`. Pydantic (and
    the `json` module underneath it) would otherwise serialize a bare `float('nan')` as the
    bare token `NaN`, which is not valid JSON -- every numeric field this route returns must be
    run through this (or already be a Python `None`) before it reaches a response model.
    """
    return None if pd.isna(value) or not np.isfinite(value) else float(value)


@router.get("/rotation", response_model=RotationResponse)
def get_rotation(
    group: Annotated[
        str, Query(description=f"One of {sorted(GROUPS)}.")
    ] = "sectors",
    benchmark: Annotated[
        str, Query(description=f"One of {BENCHMARKS}.")
    ] = DEFAULT_BENCHMARK,
    weeks: Annotated[
        int,
        Query(gt=0, le=_MAX_ROTATION_WEEKS, description="RRG trail length, in weeks."),
    ] = _DEFAULT_ROTATION_WEEKS,
) -> RotationResponse:
    """RRG-approximation trails, relative returns and sector-level breadth for one rotation
    group (T50, plans/continuation/04-sector-rotation.md).

    Fetches `_ROTATION_LOOKBACK_DAYS` of *daily* closes in one `read_universe_closes` call for
    the union of the requested group's symbols, `app.scan.groups.SECTORS` (breadth needs these
    regardless of which group was requested -- the plan's breadth block sits next to every
    group tab, not only the "sectors" one) and both benchmark symbols (`SPY`, `RSP` -- breadth
    always reads both regardless of which one is this request's own `benchmark`). Fetching
    everything in **one** wide frame, rather than one `read_bars` call per symbol, is what
    makes every division below a same-`DataFrame`-column divide instead of a positional one --
    see `app.scan.rotation`'s module docstring for why that specific discipline is this route's
    single most important property.

    `app.scan.rotation.weekly_closes` resamples that one daily frame **once**, so the group's
    symbols and the chosen benchmark share the exact same weekly index before
    `app.scan.rotation.rrg_approx` ever divides one by the other -- never two separate resample
    calls that could disagree on a bin boundary.

    Measured against the live Docker Postgres (2026-09-09, 47-symbol universe, full T42
    backfill): see the module docstring's "Live measurement" note added alongside `/breakouts`
    and `/trend` above -- this route's own timing is recorded in `docs/validation-scan.md`
    rather than repeated inline here, since (unlike those two) there is no query-parameter-free
    "default" call this route can point to without also picking a `group`.
    """
    _validate_group(group)
    _validate_benchmark(benchmark)

    group_symbols = list(GROUPS[group])
    universe = sorted(set(group_symbols) | set(SECTORS) | set(BENCHMARKS))

    end = _today()
    start = end - dt.timedelta(days=_ROTATION_LOOKBACK_DAYS)

    session_factory = get_session_factory()
    daily = read_universe_closes(universe, start, end, session_factory=session_factory)

    weekly = weekly_closes(daily)
    rrg = rrg_approx(weekly[group_symbols], weekly[benchmark], w=RRG_ZSCORE_WINDOW)

    rel_ret = relative_returns(
        daily[list(dict.fromkeys(group_symbols + [benchmark]))],
        benchmark,
        windows=RELATIVE_RETURN_WINDOWS,
    )

    breadth = sector_breadth(daily, sectors=SECTORS)

    symbols_out: list[RotationSymbolOut] = []
    for symbol in group_symbols:
        symbol_rows = rrg[rrg["symbol"] == symbol].tail(weeks)
        trail = [
            RotationPointOut(
                date=row.date.date() if hasattr(row.date, "date") else row.date,
                rs_ratio_approx=_clean_float(row.rs_ratio_approx),
                rs_momentum_approx=_clean_float(row.rs_momentum_approx),
            )
            for row in symbol_rows.itertuples(index=False)
        ]
        ret_row = rel_ret.loc[symbol] if symbol in rel_ret.index else None
        symbols_out.append(
            RotationSymbolOut(
                symbol=symbol,
                trail=trail,
                # `_clean_float` is applied even though `relative_returns` already returns
                # plain `None`/`float`, never `NaN`/`inf` -- `pd.DataFrame.from_dict` can
                # up-cast a column mixing `None` and `float` to `float64` with `NaN` standing
                # in for `None` (a pandas construction detail, not a contract `relative_returns`
                # itself breaks), so this route re-cleans defensively rather than assuming the
                # dict-like `.get()` below handed back the exact Python object that was put in.
                return_5=None if ret_row is None else _clean_float(ret_row.get("return_5")),
                return_20=None if ret_row is None else _clean_float(ret_row.get("return_20")),
                return_65=None if ret_row is None else _clean_float(ret_row.get("return_65")),
            )
        )

    return RotationResponse(
        group=group,
        benchmark=benchmark,
        weeks=weeks,
        w=RRG_ZSCORE_WINDOW,
        symbols=symbols_out,
        breadth=SectorBreadthOut.from_breadth(breadth),
        note=_RRG_APPROXIMATION_NOTE,
    )


# --------------------------------------------------------------------------------------------
# T52: ETF shares-outstanding flows route (plans/continuation/05-etf-flows.md;
# docs/etf-flows-sources.md). Kept as one self-contained, additive block -- including its own
# local imports below, rather than editing this file's shared import list at the top -- so a
# concurrent edit to this same module (another agent's task, landing in the same window) has
# nothing here to conflict with beyond this block's own boundaries.
# --------------------------------------------------------------------------------------------

_VALID_FLOW_WINDOWS = (5, 20, 60)

#: Calendar days of shares-outstanding history to read before computing flows. This table
#: accumulates one row per symbol per issuer-publishing-day starting from whenever T52's job
#: first ran (no backfill exists, per the survey) -- 400 days comfortably covers the largest
#: supported window (60 trading days) many times over while staying cheap to query long after
#: the table has years of history.
_FLOWS_LOOKBACK_DAYS = 400


def _validate_flow_window(window: int) -> int:
    if window not in _VALID_FLOW_WINDOWS:
        raise HTTPException(
            status_code=422,
            detail=f"window must be one of {_VALID_FLOW_WINDOWS}, got {window}",
        )
    return window


class FlowSymbolOut(BaseModel):
    """One row of the flows bar chart: a fund's net flow (dollars and percent of AUM) over
    the requested window. `flow`/`flow_pct` are `None` -- with `message` explaining why --
    whenever fewer than `window + 1` days of paired shares-outstanding/NAV history exist yet
    (see `app.scan.flows.compute_flows`'s own docstring); the plan's "never draw a symbol with
    no usable source at zero" rule for *unsupported* symbols is enforced separately by
    `no_flow_data` below, not by this model.
    """

    symbol: str
    flow: float | None
    flow_pct: float | None
    history_since: dt.date | None
    message: str | None


class NoFlowDataOut(BaseModel):
    """A symbol with no supported shares-outstanding source at all -- the plan's "no flow
    data" list, never drawn as a zero bar."""

    symbol: str
    reason: str


class FlowsFamilySourceOut(BaseModel):
    """One supported family's data-source banner: which family, and the as-of date of its
    newest stored row -- mirrors `app.api.health.FlowsFamilyHealth` exactly (same field
    meaning, same "stored row date, not last fetch time" freshness rule) so the flows page's
    own source banner and `/api/health/capture`'s `flows` block can never disagree.
    """

    family: str
    last_as_of_date: dt.date | None


class FlowsResponse(BaseModel):
    window: int
    symbols: list[FlowSymbolOut]
    no_flow_data: list[NoFlowDataOut]
    sources: list[FlowsFamilySourceOut]


def _clean_float_or_none(value: float | None) -> float | None:
    """Same `NaN`/`inf` -> `None` cleaning as `_clean_float` above, but tolerating a `None`
    input too -- `app.scan.flows.compute_flows` already returns plain `None` for an
    insufficient-history window (never a fabricated `NaN`), so this is a defensive pass
    guarding only against pandas up-casting a mixed `None`/`float` column, exactly the
    rationale `get_rotation`'s own `return_5`/etc. cleaning gives for the identical re-clean.
    """
    return None if value is None else _clean_float(value)


@router.get("/flows", response_model=FlowsResponse)
def get_flows(
    window: Annotated[
        int, Query(description=f"Flow window in trading days, one of {_VALID_FLOW_WINDOWS}.")
    ] = 20,
) -> FlowsResponse:
    """Net ETF creation/redemption flow over `window` trading days, in dollars and as a
    percent of AUM, for every symbol with a working shares-outstanding source (T52).

    This is the "honest version of a money-flow indicator" the plan requires: every number
    here comes from `app.scan.flows.compute_flows`'s `flow_t = (SO_t - SO_{t-1}) * NAV_t`
    definition over real issuer-published shares outstanding, never a volume/price proxy.
    `no_flow_data` names the four symbols (`app.providers.etf_flows.UNSUPPORTED_SYMBOLS`) this
    task's survey found no working source for at all -- they are never drawn as a zero bar,
    per the plan's explicit requirement.
    """
    from app.providers.etf_flows import ALL_SUPPORTED_SYMBOLS, FAMILY_SYMBOLS, UNSUPPORTED_SYMBOLS
    from app.scan.flows import compute_flows
    from app.storage.flows_repository import (
        get_session_factory as get_flows_session_factory,
    )
    from app.storage.flows_repository import (
        last_as_of_date,
        read_universe_nav,
        read_universe_shares_outstanding,
    )

    _validate_flow_window(window)

    session_factory = get_flows_session_factory()
    end = _today()
    start = end - dt.timedelta(days=_FLOWS_LOOKBACK_DAYS)

    supported = list(ALL_SUPPORTED_SYMBOLS)
    so_frame = read_universe_shares_outstanding(supported, start, end, session_factory=session_factory)
    nav_frame = read_universe_nav(supported, start, end, session_factory=session_factory)

    flows = compute_flows(so_frame, nav_frame, windows=[window])

    symbols_out: list[FlowSymbolOut] = []
    for row in flows.itertuples(index=False):
        flow_value = getattr(row, f"flow_{window}")
        flow_pct = getattr(row, f"flow_pct_{window}")
        message = None
        if flow_value is None:
            message = (
                f"history since {row.history_since.isoformat()}"
                if row.history_since is not None
                else "no data yet"
            )
        symbols_out.append(
            FlowSymbolOut(
                symbol=row.symbol,
                flow=_clean_float_or_none(flow_value),
                flow_pct=_clean_float_or_none(flow_pct),
                history_since=row.history_since,
                message=message,
            )
        )

    # Worst-to-best is not this route's contract (unlike /breakouts' rate sort) -- the plan
    # asks for a sorted bar chart without naming a direction, so this sorts largest-outflow
    # first (most negative flow_pct first), symbols with no computable flow last, ties broken
    # by symbol for determinism.
    symbols_out.sort(
        key=lambda s: (s.flow_pct is None, s.flow_pct if s.flow_pct is not None else 0.0, s.symbol)
    )

    no_flow_data = [
        NoFlowDataOut(symbol=symbol, reason="no supported shares-outstanding source (see docs/etf-flows-sources.md)")
        for symbol in UNSUPPORTED_SYMBOLS
    ]

    sources = [
        FlowsFamilySourceOut(
            family=family,
            last_as_of_date=max(
                (d for d in (last_as_of_date(s, session_factory=session_factory) for s in symbols) if d is not None),
                default=None,
            ),
        )
        for family, symbols in FAMILY_SYMBOLS.items()
    ]

    return FlowsResponse(
        window=window, symbols=symbols_out, no_flow_data=no_flow_data, sources=sources
    )
