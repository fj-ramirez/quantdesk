"""Breakout ledger and trend/chop scorer read API (T43, plans/continuation/01-breakout-
ledger.md; T45, plans/continuation/02-trend-chop-scorer.md adds the `/trend` routes).

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
from app.scan.trend import (
    TREND_LOOKBACK,
    TrendComponents,
    TrendRow,
    rank_universe,
    score_symbol,
)
from app.storage.bars_repository import get_session_factory, read_bars
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
