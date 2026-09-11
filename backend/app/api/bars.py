"""Daily-bars read API (T42, plans/continuation/00-foundation-daily-bars.md).

`GET /api/bars/{symbol}?start=&end=` and `GET /api/universe` exist for the six continuation-plan
scan tools that follow T42 (breakout ledger, trend scorer, rotation, cross-asset strip, ...);
this app itself shows nothing with them yet. Kept in their own router (not folded into
`app.api.chains` or `app.api.snapshots`) because bars have no relationship to option chains or
GEX -- a different table, a different provider hierarchy, a different caller base.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings
from app.jobs.intraday_bars import session_daily_bar
from app.storage.bars_repository import get_session_factory, read_bars, read_intraday_bars

__all__ = ["router"]

class IntradayBarOut(BaseModel):
    """One stored intraday bucket (T74). `ts` is the bucket's opening instant, UTC."""

    symbol: str
    interval: str
    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


class SessionBarOut(BaseModel):
    """The in-progress daily bar, aggregated from today's buckets (T74).

    Deliberately a distinct schema from `BarOut` rather than a `BarOut` with a flag: this is
    **not** a settled daily bar and nothing that consumes settled bars should be able to accept
    it by accident. `complete` is always `false` here; it exists so a client renders the
    "in progress" affordance from the payload rather than from knowing which endpoint it called.
    """

    symbol: str
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None
    complete: bool = False
    bucket_count: int
    last_bucket_ts: dt.datetime


class IntradayBarsResponse(BaseModel):
    """`GET /api/bars/{symbol}/intraday`: the stored series plus the derived session bar."""

    symbol: str
    interval: str
    bars: list[IntradayBarOut]
    session_bar: SessionBarOut | None = None


router = APIRouter(tags=["bars"])


class BarOut(BaseModel):
    """Mirrors `app.models.bars.DailyBar` -- see that model's docstring for field meanings,
    in particular why `volume` is nullable and `0` is a distinct, genuine value from `None`.
    """

    date: dt.date
    open: float
    high: float
    low: float
    close: float
    volume: int | None
    source: str


class UniverseResponse(BaseModel):
    symbols: list[str]


@router.get("/universe", response_model=UniverseResponse)
def get_universe() -> UniverseResponse:
    """The configured `SCAN_UNIVERSE`, in order -- what every later continuation-plan scan
    page reads bars for. A thin read of `app.config.settings`, not the database: this route
    answers "what symbols does this app track," not "which of them have data yet" (that's what
    an empty list from `/api/bars/{symbol}` communicates per-symbol).
    """
    return UniverseResponse(symbols=settings.scan_universe)


@router.get("/bars/{symbol}", response_model=list[BarOut])
def get_bars(
    symbol: str,
    start: Annotated[dt.date | None, Query(description="Inclusive lower bound")] = None,
    end: Annotated[dt.date | None, Query(description="Inclusive upper bound")] = None,
) -> list[BarOut]:
    """Every stored bar for `symbol` in `[start, end]` (either bound optional), ascending by
    date.

    `symbol` accepts `^`-prefixed vendor-style tickers (`^VIX`) unchanged -- FastAPI/Starlette
    percent-decode a path segment before routing reaches this function, so `GET
    /api/bars/%5EVIX` and a client that sends the literal `^` both arrive here as `"^VIX"`; see
    `tests/test_bars_api.py` for both forms exercised through `TestClient`. Normalized the same
    way every provider normalizes a requested symbol (`.strip().upper()`) before the lookup, so
    `spy` and `SPY` hit the same rows.

    This route does zero validation against `SCAN_UNIVERSE` or any provider: a symbol with no
    bars yet (never backfilled, a typo) returns an empty list, not a 404 or 422, so a frontend
    page can render "no data for this symbol" without a special-cased error path -- the same
    "clean empty state, not a synthetic error" contract `app.api.chains` uses for an unlisted
    expiry.
    """
    session_factory = get_session_factory()
    df = read_bars(symbol.strip().upper(), start, end, session_factory=session_factory)
    return [
        BarOut(
            date=row.date,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=None if pd.isna(row.volume) else int(row.volume),
            source=row.source,
        )
        for row in df.itertuples(index=False)
    ]


@router.get("/bars/{symbol}/intraday", response_model=IntradayBarsResponse)
def get_intraday_bars(
    symbol: str,
    interval: Annotated[str | None, Query(description="Bar interval; defaults to the configured one")] = None,
    day: Annotated[dt.date | None, Query(description="UTC day to read; defaults to today")] = None,
) -> IntradayBarsResponse:
    """Stored intraday buckets for `symbol`, plus the in-progress daily bar derived from them (T74).

    Same "empty is not an error" contract as `GET /api/bars/{symbol}` above: a symbol outside
    `INTRADAY_BARS_SYMBOLS`, a day before polling was switched on, or a holiday all return an
    empty `bars` list and a `null` session_bar rather than a 404. A client renders "no intraday
    data" from that, and falls back to the last settled daily bar.

    `session_bar` is explicitly **not** a settled daily bar and carries `complete: false` to say
    so in the payload. It is aggregated from the buckets in this same response, never written to
    `daily_bars` -- see `app.jobs.intraday_bars.session_daily_bar` for why that separation is
    load-bearing rather than tidiness.
    """
    resolved_interval = interval or settings.INTRADAY_BARS_INTERVAL
    normalized = symbol.strip().upper()
    session_factory = get_session_factory()
    target_day = day or dt.datetime.now(dt.UTC).date()

    start = dt.datetime.combine(target_day, dt.time.min, tzinfo=dt.UTC)
    end = dt.datetime.combine(target_day, dt.time.max, tzinfo=dt.UTC)
    rows = read_intraday_bars(
        normalized,
        interval=resolved_interval,
        start=start,
        end=end,
        session_factory=session_factory,
    )

    session_bar = None
    if rows:
        aggregate = session_daily_bar(
            normalized,
            day=target_day,
            interval=resolved_interval,
            session_factory=session_factory,
        )
        if aggregate is not None:
            session_bar = SessionBarOut(
                symbol=normalized,
                date=target_day,
                open=aggregate.open,
                high=aggregate.high,
                low=aggregate.low,
                close=aggregate.close,
                volume=aggregate.volume,
                complete=False,
                bucket_count=len(rows),
                last_bucket_ts=rows[-1].ts,
            )

    return IntradayBarsResponse(
        symbol=normalized,
        interval=resolved_interval,
        bars=[
            IntradayBarOut(
                symbol=r.symbol,
                interval=r.interval,
                ts=r.ts,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
            )
            for r in rows
        ],
        session_bar=session_bar,
    )
