"""Breakout ledger, trend/chop scorer and sector-rotation read API (T43, plans/continuation/01-
breakout-ledger.md; T45, plans/continuation/02-trend-chop-scorer.md adds the `/trend` routes;
T50, plans/continuation/04-sector-rotation.md adds `/rotation`).

Routes, all computing on request from `daily_bars` -- no new table, per the plan and
CLAUDE.md invariant 5's spirit (Postgres holds computed results plus an index; this is cheap
enough to just compute every time for a single-user app, so nothing is persisted at all):

* `GET /api/gex/scan/breakouts?n=&k=&lookback=` -- one `app.modules.gex.scan.breakouts.BreakoutSummary` per
  symbol in `settings.scan_universe`, plus the union of every still-open (`pending`) event
  across the universe for the "Open breakouts" panel, plus an `excluded` list naming any
  symbol dropped for having too gappy a bars history to trust (see `_expected_trading_days`).
* `GET /api/gex/scan/breakouts/{symbol}?n=&k=&lookback=` -- the raw event list for one symbol, for
  the detail view (event table + chart markers). Never 404s: a symbol with no bars at all (a
  typo, or one newly added to `SCAN_UNIVERSE` before its first bars job run) returns a clean
  empty `events` list, the same "clean empty state, not a synthetic error" contract
  `app.modules.gex.api.bars.get_bars` already uses for exactly this situation.
* `GET /api/gex/scan/trend` -- one `app.modules.gex.scan.trend.TrendRow` per `settings.scan_universe` symbol
  (T45): the ADX/ER/CHOP/VR components, RV20/IV30/IV-RV hint, cross-sectional percentiles and
  the composite. Never excludes a symbol the way `/breakouts` does for gappy history -- every
  universe symbol gets a row, with `None` components wherever its history or chain does not
  support them (see `_lookup_iv30` below and `app.modules.gex.scan.trend.score_symbol`'s own docstring).
* `GET /api/gex/scan/trend/{symbol}` -- one symbol's current components plus their rolling
  126-bar history, for the detail panel's sparklines. Same "no bars at all is a clean empty
  result, not a 404" contract as `/breakouts/{symbol}`.
* `GET /api/gex/scan/rotation?group=&benchmark=&weeks=` -- one RRG-approximation trail
  (`rs_ratio_approx`/`rs_momentum_approx` per week) plus 1/4/13-week relative return per symbol
  in the chosen `app.modules.gex.scan.groups.GROUPS` group, plus one sector-level breadth reading (T50).
  Unlike every other route in this module, `group` has a fixed 422-rejected menu (`sectors`,
  `industries`, `assets` -- `app.modules.gex.scan.groups.GROUPS`'s own keys) rather than an open string,
  because there is no sensible "compute it anyway" fallback for a group name this module does
  not know the membership of, unlike `n`/`k`/`lookback`'s open-but-restricted-by-convention
  numeric ranges. See this route's own docstring below for the daily-bars lookback window it
  fetches and why, and `app.modules.gex.scan.rotation`'s module docstring for the cross-symbol alignment
  discipline every division in this route's pipeline depends on.

**T45's IV lookup lives here, not in `app.modules.gex.scan.trend`** (that module's purity contract:
CLAUDE.md invariant 1, and the same caller/pure split T43 already used for its own calendar
dependency). `_lookup_iv30` mirrors `app.modules.gex.api.report._build`'s read path -- one `select` for the
latest `Snapshot` row, `resolve_snapshot_path` + `read_snapshot` + `to_frame`, then
`app.modules.gex.gex.report.iv_regime` -- trimmed to the single `atm_iv` number `score_symbol` needs;
running the *full* `compute_all`/`build_report` pipeline here (walls, net GEX, max pain) would
be wasted work this route never reads. A symbol that fails at any step of that lookup --
not one of the 28 option-covered `Underlying` members at all, never captured, or indexed with a
Parquet file that has since gone missing -- degrades to `iv30=None` rather than failing the
whole universe scan: most of `SCAN_UNIVERSE` (19 of 47 symbols) has no chain at all, and that
is the ordinary case this route runs against on every request, not an error condition.

Response models are defined locally rather than in `app/modules/gex/api/schemas.py`: T43's edit list does
not include that file (a parallel T47 agent is working elsewhere in the API package at the same
time), and `app.modules.gex.api.bars` -- T42's own router, landed just before this one -- already
establishes the "small router-local Pydantic models" pattern for a new, self-contained router
that shares no response shape with the rest of the API.

`n`/`k`/`lookback` are validated here, not in `app.modules.gex.scan.breakouts`: the pure module accepts any
`n, k >= 1` (a test fixture may want a short, cheap-to-hand-build series), while the plan's
design document fixes the *product's* menu to `n in {20, 55}` and `k in {3, 5, 10}` -- an API
concern about what the frontend is allowed to ask for, not a mathematical constraint the engine
must enforce on itself.

**Gap detection uses `app.modules.gex.jobs.calendar.is_trading_day`, not a weekday approximation.** This
router does I/O already and is not bound by `app.modules.gex.scan`'s purity contract, so unlike
`app.modules.gex.scan.breakouts` it is free to import the calendar. That turned out not to be optional: an
early version approximated "expected trading days" as plain Mon-Fri weekdays and, measured
against the live database (2026-09-09), excluded **every single symbol** in the universe -- a
126-bar (~6-month) lookback ordinarily spans 4-5 real NYSE holidays, which alone read as ~3.8%
"missing" under a weekday-only count, comfortably past the plan's 2% exclusion threshold before
a single real gap was involved. `is_trading_day` fixes that by knowing about actual holidays
for the years the default lookback falls in (2026-2027 today; see that module's own maintenance
note about keeping the table current). See `docs/validation-scan.md` for the measurement.

**Measured timing (2026-09-09, `GET /api/gex/scan/breakouts?n=20&k=5&lookback=126`, default query
params, against the live Docker Postgres with the full T42 backfill -- 47 symbols, most with
~1,250 bars of real history, i.e. *2.5x* the plan's "45 symbols x 500 bars" acceptance
scenario):** three consecutive requests via `curl -w '%{time_total}'` measured **0.90s, 0.93s,
1.03s** end to end (includes FastAPI request handling, 47 sequential `read_bars` round trips to
Postgres, `detect_events`/`summarize` for each, and JSON serialization) -- comfortably inside
the plan's 2s budget with roughly 1s of headroom, on a heavier data set than the acceptance
scenario specifies. `read_bars` is one indexed `SELECT ... WHERE symbol = ?` per symbol (see
`app.modules.gex.storage.bars_repository`), so this scales linearly in symbol count; no caching layer was
added, matching the single-user "not a scaling problem worth the complexity" reasoning
`app.modules.gex.api.gex`'s own module docstring already gives for the same trade-off.

**T45's `GET /api/gex/scan/trend` measured timing (2026-09-09, same live database, full universe,
no query params):** four consecutive requests via `curl -w '%{time_total}'` measured **3.51s,
3.61s, 3.72s, 3.79s** end to end. Unlike `/breakouts`, this is dominated by `_lookup_iv30`, not
by bars I/O: measured directly (a plain in-process timer, no HTTP) against the same database,
`read_bars` for all 47 symbols is only **0.57s**, while `_lookup_iv30` for the same 47 symbols
(most of them a fast `None` -- see below -- but 28 of them a real Parquet read) is **3.09s**.
That is not a caching gap this route failed to add; it is the honest cost of flattening 28
real option chains (`to_frame`, same flatten `app.modules.gex.api.report` names as "the expensive step") on
every single request, one of which (SPX) alone carries on the order of 25,000+ contract rows.
No caching layer was added for the same single-user reasoning `/breakouts` already gives, and
there is no plan-stated latency budget for `/trend` the way T43's plan names one for
`/breakouts` (2s) -- this paragraph exists so a reader has the real number and its breakdown
rather than an unstated implicit target. `GET /api/gex/scan/trend/{symbol}` (single symbol, `SPY`,
which does have a chain) measured **0.26-0.33s** across three requests -- one `read_bars` call
plus one `_lookup_iv30` call, not 47 of each. `GET /api/gex/scan/trend/NOPEXYZ` (zero stored bars)
measured **0.01s**, `200 OK`, empty `history`, all-`None` `current` -- confirming the same
"clean empty result, not a 404 or 500" contract `/breakouts/{symbol}` already established,
against the live server rather than only `tests/test_scan_api.py`'s offline equivalent.

* `GET /api/gex/scan/regime?filter=` -- one `app.modules.gex.scan.regime.RegimeRow` per `app.modules.gex.models.chain
  .Underlying` member (core + T47 extended, 28 symbols today), plus a `trend_pct` field (T48,
  plans/continuation/03-regime-board.md). **Never opens Parquet**: `_load_gex_inputs` below
  reads `gex_levels`/`gex_by_strike` rows directly and reconstructs the `KeyLevels`/`StrikeGex`
  objects `app.modules.gex.scan.regime.compute_regime_row` needs from them -- see that module's own
  docstring for why it takes those two engine types rather than a full `GexResult` (a real
  `GexResult` would need a fabricated `GexDiagnostics` nothing in storage backs). `iv30`/`rv20`
  are taken from the *same* `TrendComponents` this route computes for `trend_pct` (one
  `_lookup_iv30`/`realized_vol` pass, not two) whenever the symbol is in `settings
  .scan_universe` (true for every `Underlying` member under the default config); `atr14` and
  `return_5d` come from the same already-fetched `bars` frame. A symbol never captured at all
  gets a row with every GEX-derived field `None` and `reasons=("no snapshot captured yet for
  this symbol",)` -- the same "row per universe member, `None` where inputs are missing"
  contract `/trend` already established, extended to "missing" meaning "no option chain
  captured" rather than "no bars."

**T124: the universe pass is cached.** By 2026-09-23 the universe had grown to 125 symbols and
`/decisions` took 5.8s, almost all of it the per-symbol trend pass above (`read_bars` ~2.9s,
`_lookup_iv30` ~1.8s, indicators ~1.7s) -- which is a pure function of the stored bars and
snapshots. `_universe()` keeps the last result keyed on a fingerprint of both tables (row count,
max id, max date and a checksum of the bars; count, max id and IV count of the snapshots), so it
recomputes exactly when a capture, a bars run, a revision or a retention pass changes an input,
never on a timer. Anything that depends on the wall clock (chain age, `stale`) is computed per
request downstream of it, never cached.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.db import get_session_factory as get_gex_session_factory
from app.modules.gex.gex.engine import ExpiryFilter, KeyLevels, StrikeGex, to_frame
from app.modules.gex.gex.report import iv_regime
from app.modules.gex.jobs.calendar import MARKET_CLOSE, effective_data_time, is_trading_day
from app.modules.gex.models.chain import Underlying
from app.modules.gex.models.db import DailyBar, GexByStrike, GexLevel, Snapshot
from app.modules.gex.providers import get_provider
from app.modules.gex.providers.base import ProviderError
from app.modules.gex.scan.breakouts import (
    DEFAULT_K,
    DEFAULT_LOOKBACK,
    DEFAULT_N,
    BreakoutEvent,
    BreakoutSummary,
    Outcome,
    detect_events,
    summarize,
)
from app.modules.gex.scan.groups import BENCHMARKS, DEFAULT_BENCHMARK, GROUPS, SECTORS
from app.modules.gex.scan.indicators import (
    ADX_PERIOD,
    ATR_PERIOD,
    CHOP_PERIOD,
    ER_PERIOD,
    RV_PERIOD,
    adx,
    atr,
    choppiness,
    efficiency_ratio,
    realized_vol,
)
from app.modules.gex.scan.regime import STALE_THRESHOLD_MINUTES, RegimeRow, compute_regime_row
from app.modules.gex.scan.rotation import (
    RELATIVE_RETURN_WINDOWS,
    RRG_ZSCORE_WINDOW,
    SectorBreadth,
    relative_returns,
    rrg_approx,
    sector_breadth,
    weekly_closes,
)
from app.modules.gex.scan.trend import (
    TREND_LOOKBACK,
    TrendComponents,
    TrendRow,
    rank_universe,
    score_symbol,
)
from app.modules.gex.storage.bars_repository import (
    get_session_factory,
    read_bars,
    read_bars_many,
    read_universe_closes,
)
from app.modules.gex.storage.parquet import read_snapshot, resolve_snapshot_path

__all__ = ["RegimeBuild", "build_regime_rows", "router"]

router = APIRouter(prefix="/scan", tags=["scan"])

#: The plan's "Design decisions" menu -- see the module docstring for why this is enforced here
#: and not inside `app.modules.gex.scan.breakouts`.
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
    """Mirrors `app.modules.gex.scan.breakouts.BreakoutEvent` -- see that dataclass's docstring for what
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
    #: T92. Volume on the breakout bar against its own trailing baseline -- whether the market
    #: participated in the break. `None` where volume is unknown.
    rel_volume: float | None

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

    `symbol` is normalized the same way `app.modules.gex.api.bars.get_bars` normalizes it
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

    Mirrors `app.modules.gex.api.report._build`'s read path (one `Snapshot` lookup, `resolve_snapshot_path`
    + `read_snapshot` + `to_frame`, then `app.modules.gex.gex.report.iv_regime`) trimmed to the single
    number `app.modules.gex.scan.trend.score_symbol` needs. `data_dir` defaults to `settings.DATA_DIR` (via
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

    # T103: the fast path. `compute_and_store` now computes this once at capture, so the
    # common case is a column read and the Parquet reopen below is the fallback for rows
    # written before T103 (or whose IV was not computable, which stores null and therefore
    # *does* pay the fallback -- acceptable: those rows are rare and the recompute returns
    # None quickly). This is what made `_lookup_iv30` the dominant cost of the trend
    # endpoint, ~3s across the universe; see this module's docstring.
    if row.atm_iv is not None:
        return row.atm_iv

    try:
        path = resolve_snapshot_path(row, data_dir=data_dir)
        snapshot = read_snapshot(path)
    except FileNotFoundError:
        # Indexed but the Parquet file is missing. `app.modules.gex.api.report` 404s the whole request for
        # this (it is the one thing the caller explicitly asked for); a universe scan instead
        # degrades this one symbol's IV to `None` and keeps going, the same "one bad symbol
        # does not poison the table" posture `/breakouts`'s `excluded` list embodies for a
        # gappy bars history.
        return None

    frame = to_frame(snapshot)
    regime = iv_regime(frame, snapshot.spot)
    return regime.atm_iv


class TrendComponentsOut(BaseModel):
    """Mirrors `app.modules.gex.scan.trend.TrendComponents` -- see that dataclass's docstring for exactly
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
    #: T92. Volume on the latest bar against its own trailing 60-session baseline. `None`
    #: where the symbol reports no volume -- the five `^`-prefixed index quotes -- which the
    #: UI renders as the same `·` every other unknown gets, never as `0`.
    rel_volume: float | None

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
    deliberately absent here -- `app.modules.gex.scan.indicators.variance_ratio` is not a rolling `Series`
    (see that module's docstring) so there is no per-day VR reading to plot, and no IV history
    is ever persisted past the latest snapshot (`app.modules.gex.api.report.get_report`'s own docstring:
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
    bars: pd.DataFrame | None = None,
) -> tuple[TrendComponents, pd.DataFrame]:
    """Fetch `symbol`'s bars, look up its `iv30`, and score it -- the one sequence both trend
    routes need, factored out so the universe route and the single-symbol route can never
    silently diverge on how a row is built. Returns `(components, bars)` because the detail
    route also needs `bars` itself to build the sparkline history; the universe route discards
    the second element. `bars`, when given, is used instead of reading them (T124: the
    universe pass reads every symbol's bars in one query).
    """
    if bars is None:
        bars = read_bars(symbol, session_factory=bars_session_factory)
    iv30 = _lookup_iv30(symbol, gex_session_factory, data_dir=data_dir)
    return score_symbol(bars, iv30), bars


@dataclass(frozen=True)
class _Universe:
    """T124. The trend pass over `settings.scan_universe`: each symbol's components and the
    bars they were scored from. Shared between requests -- callers must not mutate the frames."""

    components: dict[str, TrendComponents]
    bars: dict[str, pd.DataFrame]


_universe_cache: tuple[tuple, _Universe] | None = None
_universe_lock = threading.Lock()


def _universe_key(bars_session_factory: sessionmaker, gex_session_factory: sessionmaker) -> tuple:
    """What `_universe()`'s result depends on, read in two aggregate queries (~tens of ms).

    Bars: count and max id catch an insert or a retention delete; max date a new session; the
    OHLC/volume sums an in-place revision (`upsert_bars` updates an existing date). Snapshots:
    count and max id catch a new capture, `count(atm_iv)` an IV backfill.
    """
    with bars_session_factory() as session:
        bars_state = session.execute(
            select(
                func.count(DailyBar.id),
                func.max(DailyBar.id),
                func.max(DailyBar.date),
                # `numeric`, not float: Postgres sums in parallel, and a float sum's last digits
                # depend on the order -- measured, it differed on every call. Numeric is exact.
                func.sum(
                    cast(DailyBar.open + DailyBar.high + DailyBar.low + DailyBar.close, Numeric)
                ),
                func.sum(DailyBar.volume),
            )
        ).one()
    with gex_session_factory() as session:
        snapshot_state = session.execute(
            select(func.count(Snapshot.id), func.max(Snapshot.id), func.count(Snapshot.atm_iv))
        ).one()
    return (
        tuple(settings.scan_universe),
        str(settings.DATA_DIR),
        tuple(bars_state),
        tuple(snapshot_state),
    )


def _universe(bars_session_factory: sessionmaker, gex_session_factory: sessionmaker) -> _Universe:
    """The trend pass over the whole universe, recomputed only when its inputs changed.

    The lock makes a second request that arrives mid-computation wait for the first one's
    result instead of starting its own copy of the pass.
    """
    global _universe_cache
    key = _universe_key(bars_session_factory, gex_session_factory)
    with _universe_lock:
        if _universe_cache is not None and _universe_cache[0] == key:
            return _universe_cache[1]
        bars_by_symbol = read_bars_many(settings.scan_universe, session_factory=bars_session_factory)
        components: dict[str, TrendComponents] = {}
        for symbol in settings.scan_universe:
            components[symbol], _ = _symbol_components(
                symbol, bars_session_factory, gex_session_factory, bars=bars_by_symbol[symbol]
            )
        universe = _Universe(components, bars_by_symbol)
        _universe_cache = (key, universe)
        return universe


def clear_universe_cache() -> None:
    """Drop the cached universe pass. For tests, which swap the database under the module."""
    global _universe_cache
    with _universe_lock:
        _universe_cache = None


@router.get("/trend", response_model=TrendResponse)
def get_trend() -> TrendResponse:
    """Trend/chop components, percentiles and composite for every symbol in `SCAN_UNIVERSE`.

    Unlike `/breakouts`, no symbol is ever excluded for a gappy history -- `app.modules.gex.scan.indicators`
    already reports `None` per-component for whatever it cannot compute (this route does not
    duplicate that decision with a second, coarser gap check), and a `None` component just
    narrows that one symbol's contribution to the cross-sectional percentiles rather than
    invalidating the whole row (see `app.modules.gex.scan.trend.rank_universe`'s docstring).

    Rows are sorted by `composite` descending (most "trending" first), symbols with no
    composite at all (nothing finite to average) sorted last, ties broken by symbol -- the
    same "quoted values first, `None` last, deterministic" ordering `/breakouts` already uses
    for `rate`.
    """
    bars_session_factory = get_session_factory()
    gex_session_factory = get_gex_session_factory()

    components_by_symbol = _universe(bars_session_factory, gex_session_factory).components

    rows = rank_universe(components_by_symbol)
    rows.sort(
        key=lambda r: (r.composite is None, -(r.composite or 0.0), r.symbol)
    )

    return TrendResponse(rows=[TrendRowOut.from_row(r) for r in rows])


@router.get("/trend/{symbol}", response_model=SymbolTrendResponse)
def get_symbol_trend(symbol: str) -> SymbolTrendResponse:
    """One symbol's current trend/chop components plus their rolling `TREND_LOOKBACK`-bar
    history, for the detail panel's sparklines.

    `symbol` is normalized the same way `app.modules.gex.api.bars.get_bars` and `get_symbol_breakouts`
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
#: `app.modules.gex.scan.rotation`'s own docstring.
_RRG_APPROXIMATION_NOTE = (
    "rs_ratio_approx / rs_momentum_approx are the common open approximation of JdK RS-Ratio / "
    "RS-Momentum (rolling z-scores of price/benchmark and its own week-over-week change). "
    "JdK RS-Ratio and RS-Momentum are a proprietary, patented construction; this is not that "
    "indicator and does not claim to match it."
)

#: How many calendar days of *daily* closes this route fetches before resampling to weekly and
#: running `app.modules.gex.scan.rotation.rrg_approx`. Sized for the largest `weeks` this route accepts
#: (`_MAX_ROTATION_WEEKS`) plus `2 * RRG_ZSCORE_WINDOW` weeks of z-score warm-up (the first
#: rolling z-score needs `RRG_ZSCORE_WINDOW` weeks to seed `rs_ratio_approx`, and the second
#: needs another `RRG_ZSCORE_WINDOW` on top of that to seed `rs_momentum_approx` -- see
#: `app.modules.gex.scan.rotation.rrg_approx`'s own docstring), converted to calendar days at roughly 7
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

#: Same rationale as `app.modules.gex.api.health`'s own `_TZ`: "today" for a daily-bars lookback window
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
    are `None` during the z-score's own warm-up (see `app.modules.gex.scan.rotation.rrg_approx`'s
    docstring for exactly how long that is) -- never a partially-windowed number.
    """

    date: dt.date
    rs_ratio_approx: float | None
    rs_momentum_approx: float | None


class RotationSymbolOut(BaseModel):
    """One symbol's full RRG trail (oldest to newest, already trimmed to the requested `weeks`)
    plus its 1/4/13-week relative return versus the chosen benchmark. `return_*` fields mirror
    `app.modules.gex.scan.rotation.relative_returns`'s own `f"return_{n}"` columns, named out here rather
    than left as a dict so the response schema is self-documenting.
    """

    symbol: str
    trail: list[RotationPointOut]
    return_5: float | None
    return_20: float | None
    return_65: float | None


class SectorBreadthOut(BaseModel):
    """Mirrors `app.modules.gex.scan.rotation.SectorBreadth` -- see that dataclass's docstring for exactly
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
    the union of the requested group's symbols, `app.modules.gex.scan.groups.SECTORS` (breadth needs these
    regardless of which group was requested -- the plan's breadth block sits next to every
    group tab, not only the "sectors" one) and both benchmark symbols (`SPY`, `RSP` -- breadth
    always reads both regardless of which one is this request's own `benchmark`). Fetching
    everything in **one** wide frame, rather than one `read_bars` call per symbol, is what
    makes every division below a same-`DataFrame`-column divide instead of a positional one --
    see `app.modules.gex.scan.rotation`'s module docstring for why that specific discipline is this route's
    single most important property.

    `app.modules.gex.scan.rotation.weekly_closes` resamples that one daily frame **once**, so the group's
    symbols and the chosen benchmark share the exact same weekly index before
    `app.modules.gex.scan.rotation.rrg_approx` ever divides one by the other -- never two separate resample
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
# T48: regime board (plans/continuation/03-regime-board.md)
# --------------------------------------------------------------------------------------------

#: `filter=` only ever accepts a persisted `ExpiryFilter` -- the same restriction
#: `app.modules.gex.api.gex._parse_history_filter` applies to `/gex/{underlying}/levels/history`, and for
#: the identical reason: `app.modules.gex.gex.store.DEFAULT_FILTERS` is the only set `compute_and_store`
#: ever writes rows for, so anything else would silently and permanently 404-by-omission rather
#: than surface the mistake.
_REGIME_FILTERS = (ExpiryFilter.ALL, ExpiryFilter.ZERO_DTE, ExpiryFilter.EX_ZERO_DTE)

#: Fallback used only if `get_provider(source)` itself raises for a `Snapshot.source` this
#: deployment can no longer construct a provider for (a credential that used to be configured,
#: or a provider name retired from `app.modules.gex.providers._PROVIDERS`). Cboe's own entitlement delay --
#: the only provider this app has ever captured real data under -- biased toward the *larger*
#: delay under ignorance, the same "assume the safer interpretation" reasoning
#: `app.modules.gex.jobs.calendar.is_market_holiday`'s own docstring gives for its unknown-year fallback:
#: understating staleness (a smaller fallback) risks a regime row looking fresher than it is,
#: which is the one failure mode T48 exists to prevent.
_FALLBACK_DELAYED_MINUTES = 15


def _validate_regime_filter(raw: str) -> ExpiryFilter:
    try:
        parsed = ExpiryFilter(raw)
    except ValueError:
        parsed = None
    if parsed is None or parsed not in _REGIME_FILTERS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"filter must be one of {[f.value for f in _REGIME_FILTERS]} (only persisted "
                f"filters can be read without recomputing from Parquet), got {raw!r}"
            ),
        )
    return parsed


def _delayed_minutes_for_source(source: str) -> int:
    """`Snapshot.source` -> that provider's entitlement delay, without opening Parquet.

    `ChainSnapshot.delayed_minutes` (T34's staleness input) is written once into the Parquet
    file per snapshot and is **not** a `snapshots` table column (see `app.modules.gex.models.db.Snapshot`'s
    field list) -- it is, in practice, a constant of the *provider*, not of any one capture
    (`CboeProvider.delayed_minutes` is a hardcoded `15`, `MarketDataProvider`'s is a hardcoded
    setting-derived constant too), so reconstructing it from `get_provider(source)` is exact,
    not an approximation, and (`app.modules.gex.providers.get_provider`'s own docstring) "cheap to
    construct" -- no I/O, so this does not reopen the Parquet-recompute door T48 is built to
    stay out of.
    """
    try:
        return get_provider(source).delayed_minutes
    except (ValueError, ProviderError):
        # ValueError: `source` is not a name `get_provider` recognizes (a retired provider).
        # ProviderError (e.g. `MissingCredential`): the provider is registered but this
        # deployment lacks a credential it needs (e.g. `MARKETDATA_TOKEN` unset) even though
        # some past deployment captured this snapshot with it configured.
        return _FALLBACK_DELAYED_MINUTES


@dataclass(frozen=True, slots=True)
class _RegimeGexInputs:
    """What `_load_gex_inputs` hands `app.modules.gex.scan.regime.compute_regime_row` -- everything that
    module needs about one symbol's latest snapshot, already read from `gex_levels` /
    `gex_by_strike`, never from Parquet.
    """

    snapshot_id: int
    levels: KeyLevels
    by_strike: tuple[StrikeGex, ...]
    zero_dte_by_strike: tuple[StrikeGex, ...]
    spot: float
    as_of: dt.datetime
    effective_at: dt.datetime
    chain_age_minutes: float
    stale: bool


def _load_gex_inputs(
    underlying: str,
    filter_: ExpiryFilter,
    gex_session_factory: sessionmaker,
) -> _RegimeGexInputs | None:
    """The latest snapshot's persisted `gex_levels`/`gex_by_strike` rows for `underlying`, for
    both `filter_` (the caller's requested display filter) and `ZERO_DTE` (always, regardless
    of `filter_` -- 0DTE share is a fixed diagnostic, not something the display filter should
    change; see `app.modules.gex.scan.regime.compute_regime_row`'s own docstring). Returns `None` when
    `underlying` has never been captured, or when this snapshot has no stored `GexLevel` row
    for `filter_` (should not happen once a snapshot exists -- `app.modules.gex.gex.store.compute_and_store`
    writes all of `DEFAULT_FILTERS` in one transaction -- but a partially-failed historical
    capture is not something this route should turn into a 500 over).

    **Never opens Parquet.** Every field on the returned `_RegimeGexInputs` is built from
    `snapshots` / `gex_levels` / `gex_by_strike` columns alone -- see `_strike_gex_from_values` and
    `KeyLevels`'s construction below for exactly which persisted column backs which field, and
    `app.modules.gex.scan.regime`'s module docstring for why a full `GexResult` (which would need Parquet
    for its `profile`/`diagnostics`) is not what gets built here.
    """
    with gex_session_factory() as session:
        snap = session.execute(
            select(Snapshot)
            .where(Snapshot.underlying == underlying)
            .order_by(Snapshot.captured_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if snap is None:
            return None

        def _fetch(filter_value: str) -> tuple[GexLevel | None, list[GexByStrike]]:
            level_row = session.execute(
                select(GexLevel).where(
                    GexLevel.snapshot_id == snap.id, GexLevel.filter == filter_value
                )
            ).scalar_one_or_none()
            by_strike_rows = list(
                session.execute(
                    select(GexByStrike)
                    .where(
                        GexByStrike.snapshot_id == snap.id, GexByStrike.filter == filter_value
                    )
                    .order_by(GexByStrike.strike)
                ).scalars()
            )
            return level_row, by_strike_rows

        level_row, by_strike_rows = _fetch(filter_.value)
        if filter_ is ExpiryFilter.ZERO_DTE:
            zero_dte_rows = by_strike_rows
        else:
            _, zero_dte_rows = _fetch(ExpiryFilter.ZERO_DTE.value)

        # Extract every plain value needed below while the session is still open -- ORM rows
        # are not touched again after this point, so nothing downstream depends on the
        # session staying open (avoids any risk of a detached-instance access).
        snap_id = snap.id
        snap_source = snap.source
        snap_spot = snap.spot
        snap_captured_at = snap.captured_at
        level_values = None if level_row is None else {
            "net_gex": level_row.net_gex,
            "call_wall": level_row.call_wall,
            "call_wall_gex": level_row.call_wall_gex,
            "put_wall": level_row.put_wall,
            "put_wall_gex": level_row.put_wall_gex,
            "max_abs_strike": level_row.max_abs_strike,
            "max_call_gex_strike": level_row.max_call_gex_strike,
            "max_put_gex_strike": level_row.max_put_gex_strike,
            "flip_point": level_row.flip_point,
            "spot": level_row.spot,
            "computed_at": level_row.computed_at,
        }
        by_strike_values = [
            (r.strike, r.call_gex, r.put_gex, r.net_gex) for r in by_strike_rows
        ]
        zero_dte_values = [(r.strike, r.call_gex, r.put_gex, r.net_gex) for r in zero_dte_rows]

    if level_values is None:
        return None

    def _strike_gex_from_values(values) -> tuple[StrikeGex, ...]:
        return tuple(
            StrikeGex(strike=s, call_gex=c, put_gex=p, net_gex=n, abs_gex=c - p)
            for s, c, p, n in values
        )

    by_strike = _strike_gex_from_values(by_strike_values)
    zero_dte_by_strike = _strike_gex_from_values(zero_dte_values)

    call_gex_total = sum(c for _, c, _, _ in by_strike_values)
    put_gex_total = sum(p for _, _, p, _ in by_strike_values)

    levels = KeyLevels(
        net_gex=level_values["net_gex"] if level_values["net_gex"] is not None else 0.0,
        call_gex=call_gex_total,
        put_gex=put_gex_total,
        abs_gex=call_gex_total - put_gex_total,
        call_wall=level_values["call_wall"],
        call_wall_gex=level_values["call_wall_gex"],
        put_wall=level_values["put_wall"],
        put_wall_gex=level_values["put_wall_gex"],
        max_abs_strike=level_values["max_abs_strike"],
        # Not persisted, and not read by `app.modules.gex.scan.regime` -- see `KeyLevels`'s own docstring:
        # `max_net_strike`/`min_net_strike` are documented aliases of `call_wall`/`put_wall`
        # under unambiguous names, so setting them here is not a fabrication, just the alias.
        max_abs_gex=None,
        max_net_strike=level_values["call_wall"],
        min_net_strike=level_values["put_wall"],
        flip_point=level_values["flip_point"],
        spot=level_values["spot"],
        computed_at=level_values["computed_at"],
        max_call_gex_strike=level_values["max_call_gex_strike"],
        max_call_gex=None,
        max_put_gex_strike=level_values["max_put_gex_strike"],
        max_put_gex=None,
    )

    delayed_minutes = _delayed_minutes_for_source(snap_source)
    effective_at = effective_data_time(snap_captured_at, delayed_minutes)
    local = effective_at.astimezone(_TZ)
    expected_close = dt.datetime.combine(local.date(), MARKET_CLOSE, tzinfo=_TZ) + dt.timedelta(
        minutes=delayed_minutes
    )
    chain_age_minutes = max(
        0.0, (expected_close.astimezone(dt.UTC) - effective_at).total_seconds() / 60.0
    )
    stale = chain_age_minutes > STALE_THRESHOLD_MINUTES

    return _RegimeGexInputs(
        snapshot_id=snap_id,
        levels=levels,
        by_strike=by_strike,
        zero_dte_by_strike=zero_dte_by_strike,
        spot=snap_spot,
        as_of=snap_captured_at,
        effective_at=effective_at,
        chain_age_minutes=chain_age_minutes,
        stale=stale,
    )


def _last_finite(series: pd.Series) -> float | None:
    """Most recent non-`NaN` value in `series`, or `None`. Same helper `app.modules.gex.scan.trend`'s own
    private `_last_finite` provides for that module's own rolling indicators -- duplicated
    (not imported across a module boundary for a private name) here for `atr`/`realized_vol`.
    """
    if series.empty:
        return None
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)


def _return_5d(bars: pd.DataFrame) -> float | None:
    """5-trading-bar close-to-close return, or `None` when fewer than 6 closes exist or the
    bar 5 sessions back closed at exactly zero (division would be undefined, not a real
    -100%-ish return).
    """
    if len(bars) <= 5:
        return None
    prior = float(bars["close"].iloc[-6])
    if prior == 0.0:
        return None
    return float(bars["close"].iloc[-1]) / prior - 1.0


class DealerPositioningOut(BaseModel):
    """Mirrors `app.modules.gex.gex.report.DealerPositioning` -- see that dataclass's docstring for exactly
    when `direction` is `None` (noise-dominated, or no contracts at all).
    """

    net_gex: float | None
    abs_gex: float | None
    ratio: float | None
    ratio_floor: float
    noise_dominated: bool
    direction: str | None
    label: str
    description: str


class WallInfoOut(BaseModel):
    """Mirrors `app.modules.gex.scan.regime.WallInfo` -- one wall (call or put), its distance from spot in
    three units, and its `room_beyond` (see that dataclass's own docstring).
    """

    strike: float | None
    net_gex: float | None
    abs_gex: float | None
    distance: float | None
    distance_pct: float | None
    distance_atr: float | None
    room_beyond: float | None
    room_beyond_strike: float | None


class RegimeRowOut(BaseModel):
    """One row of the `/regime` table: `app.modules.gex.scan.regime.RegimeRow` plus `trend_pct` (T45's
    cross-sectional composite, joined in here rather than threaded through the pure module --
    see this module's own docstring's T48 bullet for why).
    """

    underlying: str
    filter: str
    spot: float | None
    atr14: float | None
    iv30: float | None
    rv20: float | None
    iv_rv_ratio: float | None
    return_5d: float | None
    as_of: dt.datetime | None
    effective_at: dt.datetime | None
    chain_age_minutes: float | None
    stale: bool
    positioning: DealerPositioningOut | None
    flip_point: float | None
    flip_distance: float | None
    flip_distance_pct: float | None
    flip_distance_atr: float | None
    wall_below: WallInfoOut | None
    wall_above: WallInfoOut | None
    zero_dte_share: float | None
    verdict: str | None
    reasons: list[str]
    trend_pct: float | None

    @classmethod
    def missing(cls, underlying: str, filter_value: str, trend_pct: float | None) -> RegimeRowOut:
        """A symbol with no snapshot captured at all -- every GEX-derived field `None`, per
        this task's own acceptance item ("the API returns a row per core and extended symbol
        with `None` where inputs are missing").
        """
        return cls(
            underlying=underlying,
            filter=filter_value,
            spot=None,
            atr14=None,
            iv30=None,
            rv20=None,
            iv_rv_ratio=None,
            return_5d=None,
            as_of=None,
            effective_at=None,
            chain_age_minutes=None,
            stale=False,
            positioning=None,
            flip_point=None,
            flip_distance=None,
            flip_distance_pct=None,
            flip_distance_atr=None,
            wall_below=None,
            wall_above=None,
            zero_dte_share=None,
            verdict=None,
            reasons=["no snapshot captured yet for this symbol"],
            trend_pct=trend_pct,
        )

    @classmethod
    def from_row(cls, row: RegimeRow, trend_pct: float | None) -> RegimeRowOut:
        payload = row.to_dict()
        payload["trend_pct"] = trend_pct
        return cls(**payload)


class RegimeResponse(BaseModel):
    filter: str
    rows: list[RegimeRowOut]


@router.get("/regime", response_model=RegimeResponse)
def get_regime(
    filter_: Annotated[
        str,
        Query(
            alias="filter",
            description=f"One of {[f.value for f in _REGIME_FILTERS]} (persisted filters only).",
        ),
    ] = ExpiryFilter.ALL.value,
) -> RegimeResponse:
    """One `app.modules.gex.scan.regime.RegimeRow` per `Underlying` member (core + T47 extended), plus
    `trend_pct`. See this module's own docstring for the full design and why this route never
    opens Parquet.

    Trend components (and therefore `iv30`/`rv20`/`trend_pct`) are computed once, up front,
    across the *full* `settings.scan_universe` -- exactly `/trend`'s own pipeline
    (`_symbol_components` + `rank_universe`) -- both so `trend_pct` here matches what `/trend`
    shows for the same symbol elsewhere in the app, and so the already-fetched `bars` and
    already-looked-up `iv30`/`rv20` can be reused for `atr14`/`return_5d`/the regime metrics
    themselves rather than fetched a second time for every optioned symbol (T45's own
    `_lookup_iv30` is the dominant cost in that pipeline, ~3s across the universe -- see this
    module's docstring's T45 timing note -- and this route would otherwise pay it twice for
    every one of the ~28 `Underlying` members that also sit in `SCAN_UNIVERSE`, which is all of
    them under the default config).
    """
    parsed_filter = _validate_regime_filter(filter_)

    rows: list[RegimeRowOut] = []
    for build in build_regime_rows(parsed_filter):
        if build.row is None:
            rows.append(RegimeRowOut.missing(build.symbol, parsed_filter.value, build.trend_pct))
        else:
            rows.append(RegimeRowOut.from_row(build.row, build.trend_pct))

    return RegimeResponse(filter=parsed_filter.value, rows=rows)


@dataclass(frozen=True, slots=True)
class RegimeBuild:
    """One `Underlying` member's regime row plus the inputs it was built from, for a consumer
    that needs more than the row itself (T60's decision engine wants the by-strike ladder for
    targets and the bars for a breakout summary, and must not re-fetch either).

    `row` is `None` exactly when the symbol has never been captured (or has no persisted
    `gex_levels` row for the filter) -- the `RegimeRowOut.missing` case. `bars` is always a
    frame (possibly empty), never `None`. `by_strike` is empty when `row` is `None`.
    """

    symbol: str
    row: RegimeRow | None
    trend_pct: float | None
    by_strike: tuple[StrikeGex, ...]
    bars: pd.DataFrame
    #: The `snapshots.id` the row was computed from; `None` exactly when `row` is `None`.
    #: T61's decisions table keys on it so a re-run on the same capture never duplicates.
    snapshot_id: int | None = None


def build_regime_rows(
    parsed_filter: ExpiryFilter, *, symbols: Sequence[str] | None = None
) -> list[RegimeBuild]:
    """The `/regime` pipeline, factored out (T60) so `/decisions` builds on the same rows.

    Trend components (and therefore `iv30`/`rv20`/`trend_pct`) are computed once, up front,
    across the *full* `settings.scan_universe` -- exactly `/trend`'s own pipeline
    (`_symbol_components` + `rank_universe`) -- both so `trend_pct` matches what `/trend` shows
    for the same symbol elsewhere in the app, and so the already-fetched `bars` and
    already-looked-up `iv30`/`rv20` can be reused for `atr14`/`return_5d`/the regime metrics
    themselves rather than fetched a second time for every optioned symbol (T45's own
    `_lookup_iv30` is the dominant cost in that pipeline, ~3s across the universe -- see this
    module's docstring's T45 timing note -- and this would otherwise pay it twice for every one
    of the ~28 `Underlying` members that also sit in `SCAN_UNIVERSE`).

    `symbols` narrows the output to those `Underlying` values (the single-symbol decisions
    route); the trend ranking is still over the whole universe, since a percentile against a
    universe of one is meaningless. Defaults to every `Underlying` member, in enum order.
    """
    bars_session_factory = get_session_factory()
    gex_session_factory = get_gex_session_factory()

    universe = _universe(bars_session_factory, gex_session_factory)  # T124: cached
    components_by_symbol = universe.components
    bars_by_symbol = universe.bars
    trend_pct_by_symbol = {
        row.symbol: row.composite for row in rank_universe(components_by_symbol)
    }

    wanted = [u.value for u in Underlying] if symbols is None else list(symbols)
    builds: list[RegimeBuild] = []
    for symbol in wanted:
        trend_pct = trend_pct_by_symbol.get(symbol)

        bars = bars_by_symbol.get(symbol)
        if bars is None:
            bars = read_bars(symbol, session_factory=bars_session_factory)

        gex_inputs = _load_gex_inputs(symbol, parsed_filter, gex_session_factory)
        if gex_inputs is None:
            builds.append(RegimeBuild(symbol, None, trend_pct, (), bars))
            continue

        components = components_by_symbol.get(symbol)
        if components is not None:
            iv30 = components.iv30
            rv20 = components.rv20
        else:
            # `symbol` is an `Underlying` member outside a customized `SCAN_UNIVERSE` -- fall
            # back to a direct lookup rather than silently reporting `None` for a chain that
            # does exist.
            iv30 = _lookup_iv30(symbol, gex_session_factory)
            rv20 = _last_finite(realized_vol(bars, RV_PERIOD)) if not bars.empty else None

        atr14 = _last_finite(atr(bars, ATR_PERIOD)) if not bars.empty else None
        return_5d = _return_5d(bars)

        row = compute_regime_row(
            underlying=symbol,
            filter_=parsed_filter.value,
            levels=gex_inputs.levels,
            by_strike=gex_inputs.by_strike,
            zero_dte_by_strike=gex_inputs.zero_dte_by_strike,
            spot=gex_inputs.spot,
            atr14=atr14,
            iv30=iv30,
            rv20=rv20,
            return_5d=return_5d,
            as_of=gex_inputs.as_of,
            effective_at=gex_inputs.effective_at,
            chain_age_minutes=gex_inputs.chain_age_minutes,
            stale=gex_inputs.stale,
        )
        builds.append(
            RegimeBuild(symbol, row, trend_pct, gex_inputs.by_strike, bars, gex_inputs.snapshot_id)
        )

    return builds


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
    (see `app.modules.gex.scan.flows.compute_flows`'s own docstring); the plan's "never draw a symbol with
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
    newest stored row -- mirrors `app.modules.gex.api.health.FlowsFamilyHealth` exactly (same field
    meaning, same "stored row date, not last fetch time" freshness rule) so the flows page's
    own source banner and `/api/gex/health/capture`'s `flows` block can never disagree.
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
    input too -- `app.modules.gex.scan.flows.compute_flows` already returns plain `None` for an
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
    here comes from `app.modules.gex.scan.flows.compute_flows`'s `flow_t = (SO_t - SO_{t-1}) * NAV_t`
    definition over real issuer-published shares outstanding, never a volume/price proxy.
    `no_flow_data` names the four symbols (`app.modules.gex.providers.etf_flows.UNSUPPORTED_SYMBOLS`) this
    task's survey found no working source for at all -- they are never drawn as a zero bar,
    per the plan's explicit requirement.
    """
    from app.modules.gex.providers.etf_flows import (
        ALL_SUPPORTED_SYMBOLS,
        FAMILY_SYMBOLS,
        UNSUPPORTED_SYMBOLS,
    )
    from app.modules.gex.scan.flows import compute_flows
    from app.modules.gex.storage.flows_repository import (
        get_session_factory as get_flows_session_factory,
    )
    from app.modules.gex.storage.flows_repository import (
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


# --------------------------------------------------------------------------------------------
# T54: cross-asset regime strip (plans/continuation/06-cross-asset-regime.md). Kept as one
# self-contained, additive block -- including its own local imports below, rather than editing
# this file's shared import list at the top -- same rationale `get_flows`'s own block above
# gives for the identical pattern: a concurrent edit to this module has nothing here to
# conflict with beyond this block's own boundaries.
# --------------------------------------------------------------------------------------------

#: Calendar days of daily closes this route reads before computing anything. Sized to
#: comfortably clear `app.modules.gex.scan.cross_asset.PERCENTILE_WINDOW` (252 trading days) plus weekends
#: and holidays (252 * 7/5 ~= 353 calendar days) with real margin -- the same
#: over-provision-then-measure posture `_ROTATION_LOOKBACK_DAYS`'s own comment describes for an
#: identical calculation.
_CROSS_ASSET_LOOKBACK_DAYS = 400

#: The six Cboe index symbols (T54's `app.modules.gex.providers.cboe_index`) this route reads, plus the
#: everything-else-from-T42's-bars symbols the plan's "Data" section names by name (SPY for
#: realized vol; UUP/GLD/TLT for the three 20-day-move tiles). The 11 sector ETFs
#: (`app.modules.gex.scan.groups.SECTORS`) are unioned in below, the same "one shared frame, not one
#: `read_bars` call per symbol" reasoning `get_rotation` already gives for its own universe.
_CROSS_ASSET_VOL_SYMBOLS = ("^VIX", "^VIX3M", "^VIX9D", "^VVIX")
_CROSS_ASSET_OTHER_SYMBOLS = ("SPY", "UUP", "GLD", "TLT")


class CrossAssetOut(BaseModel):
    """Mirrors `app.modules.gex.scan.cross_asset.CrossAssetRow` field for field -- see that dataclass's own
    docstring for exactly when each field is `None` and why. Nine strip tiles' worth of data;
    no field here is a composite of any of the others (the plan's "no composite score").
    """

    as_of: dt.date | None

    vix: float | None
    vix3m: float | None
    vix9d: float | None
    vix_vix3m_ratio: float | None
    vix_vix3m_ratio_pct: float | None
    vix_vix3m_ratio_pct_n: int
    vix9d_vix_ratio: float | None
    vix9d_vix_ratio_pct: float | None
    vix9d_vix_ratio_pct_n: int
    term_structure: str | None
    term_structure_reason: str | None

    vvix: float | None
    vvix_pct: float | None
    vvix_pct_n: int

    vix_pct: float | None
    vix_pct_n: int

    spy_rv20: float | None
    vrp: float | None
    vrp_pct: float | None
    vrp_pct_n: int
    vrp_reason: str | None

    sector_correlation: float | None
    sector_correlation_n: int
    sector_correlation_universe_n: int

    uup_return_20d: float | None
    gld_return_20d: float | None
    tlt_return_20d: float | None

    @classmethod
    def from_row(cls, row) -> CrossAssetOut:
        payload = row.to_dict()
        # `CrossAssetRow.as_of` carries whatever the wide closes frame's own index dtype is
        # (an object-dtype `datetime.date`, in practice, but `read_universe_closes` makes no
        # hard promise beyond "whatever `DailyBar.date` was") -- normalized the same
        # `hasattr(..., "date")`-defensive way `app.modules.gex.api.scan.get_rotation` already normalizes
        # `rrg`'s own `row.date` before it reaches a response model.
        as_of = payload["as_of"]
        if as_of is not None and hasattr(as_of, "date") and not isinstance(as_of, dt.date):
            as_of = as_of.date()
        payload["as_of"] = as_of
        return cls(**payload)


@router.get("/cross-asset", response_model=CrossAssetOut)
def get_cross_asset() -> CrossAssetOut:
    """The cross-asset regime strip's nine tiles in one row (T54): volatility term structure
    (`^VIX`/`^VIX3M`/`^VIX9D`), `^VVIX` and its percentile, VIX's own 1-year percentile, the
    SPY volatility risk premium, 20-day sector correlation, and 20-day UUP/GLD/TLT moves.

    Reads one `_CROSS_ASSET_LOOKBACK_DAYS`-day wide closes frame in a single
    `read_universe_closes` call (same "one shared frame so every division/subtraction aligns by
    date" reasoning `get_rotation` already gives for its own universe -- see
    `app.modules.gex.scan.cross_asset`'s module docstring for why that matters here too), then hands column
    slices of that one frame to `app.modules.gex.scan.cross_asset.compute_cross_asset_row`, which does all
    the actual math. This route does no math itself beyond assembling the wire response.
    """
    from app.modules.gex.scan.cross_asset import compute_cross_asset_row

    universe = sorted(set(_CROSS_ASSET_VOL_SYMBOLS) | set(_CROSS_ASSET_OTHER_SYMBOLS) | set(SECTORS))

    end = _today()
    start = end - dt.timedelta(days=_CROSS_ASSET_LOOKBACK_DAYS)

    session_factory = get_session_factory()
    closes = read_universe_closes(universe, start, end, session_factory=session_factory)

    row = compute_cross_asset_row(
        vix=closes["^VIX"],
        vix3m=closes["^VIX3M"],
        vix9d=closes["^VIX9D"],
        vvix=closes["^VVIX"],
        spy_close=closes["SPY"],
        sector_closes=closes[list(SECTORS)],
        uup_close=closes["UUP"],
        gld_close=closes["GLD"],
        tlt_close=closes["TLT"],
    )
    return CrossAssetOut.from_row(row)
