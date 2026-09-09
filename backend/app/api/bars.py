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
from app.storage.bars_repository import get_session_factory, read_bars

__all__ = ["router"]

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
