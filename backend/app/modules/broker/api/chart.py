"""`GET /api/broker/symbols` and `GET /api/broker/chart` (T159).

Read-only, like every route in this process: invariant 11 keeps the order path in the executor,
and nothing here imports it. The arithmetic (buckets, the forming bar, levels as of each bar)
is in `app.modules.broker.chart`; this file reads, calls it, and shapes the answer.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import get_session_factory
from app.modules.broker import chart as ch
from app.modules.broker import repository as repo
from app.modules.broker.basis import PAIRS, Pair

__all__ = ["router"]

router = APIRouter(tags=["broker"])

Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class PairOut(BaseModel):
    desk: str = Field(description="The desk symbol whose GEX levels this CFD is drawn with.")
    method: str = Field(description="`offset` or `ratio`: how a desk level is translated.")
    proxy: bool = Field(description="The CFD tracks a different underlying (NAS100 is NDX, "
                        "not QQQ), so its levels are an approximation.")


class SymbolOut(BaseModel):
    symbol: str
    last_bar_at: dt.datetime | None = Field(description="Open of the newest stored M1 bar. "
                                            "Null means nothing has been ingested.")
    pair: PairOut | None


class BarOut(BaseModel):
    time: dt.datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int = Field(description="Lowest spread in the bar, in points.")
    forming: bool = Field(description="The bar had not closed at the horizon. No detector "
                          "saw it.")


class EventOut(BaseModel):
    kind: str
    direction: str | None
    time: dt.datetime = Field(description="The bar the event is about.")
    confirmed_time: dt.datetime = Field(description="The bar at whose close it was knowable.")
    price: float | None
    ref_time: dt.datetime | None


class ZoneOut(BaseModel):
    price: float
    direction: str | None
    touches: int
    last_time: dt.datetime


class LevelOut(BaseModel):
    time: dt.datetime = Field(description="The bar this level is drawn on.")
    captured_at: dt.datetime = Field(description="The snapshot it comes from: the newest at "
                                     "or before the bar's close.")
    call_wall: float | None
    put_wall: float | None
    flip_point: float | None
    spot: float | None


class LevelBasisOut(BaseModel):
    captured_at: dt.datetime
    basis_at: dt.datetime
    basis_kind: str
    roll_after_basis: bool = Field(description="A roll happened between the basis and the "
                                   "snapshot, so the translated levels are off by the roll.")


class ChartOut(BaseModel):
    symbol: str
    timeframe: Timeframe
    horizon: dt.datetime = Field(description="The instant the chart is drawn as of.")
    last_bar_at: dt.datetime | None
    pair: PairOut | None
    bars: list[BarOut]
    events: list[EventOut]
    zones: list[ZoneOut]
    levels: list[LevelOut]
    level_basis: LevelBasisOut | None = Field(description="The basis behind the newest level "
                                              "on the chart.")


def _pair(symbol: str) -> Pair | None:
    """The pair a CFD's levels are drawn with: the first in `PAIRS`, so `S&P.fs` uses SPX
    (offset, the primary) rather than SPY."""
    return next((p for p in PAIRS if p.cfd == symbol), None)


def _pair_out(pair: Pair | None) -> PairOut | None:
    return None if pair is None else PairOut(desk=pair.desk, method=pair.method, proxy=pair.proxy)


def _num(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return None if math.isnan(number) else number


@router.get("/symbols", response_model=list[SymbolOut])
def symbols() -> list[SymbolOut]:
    factory = get_session_factory()
    return [
        SymbolOut(
            symbol=s,
            last_bar_at=repo.last_bar_at(s, session_factory=factory),
            pair=_pair_out(_pair(s)),
        )
        for s in repo.configured_symbols()
    ]


@router.get("/chart", response_model=ChartOut)
def chart(
    symbol: str,
    tf: Timeframe = "M15",
    bars: Annotated[int, Query(ge=10, description="Buckets wanted. Clamped to what "
                               f"{ch.MAX_M1_ROWS} M1 rows can fill at this timeframe.")] = 300,
    end: Annotated[dt.datetime | None, Query(description="Draw the chart as of this instant. "
                                             "Omitted: now.")] = None,
) -> ChartOut:
    if symbol not in repo.configured_symbols():
        raise HTTPException(status_code=404, detail=f"Unknown broker symbol: {symbol}")
    if end is not None and end.tzinfo is None:
        raise HTTPException(status_code=422, detail="`end` needs a timezone offset.")

    minutes = ch.TIMEFRAMES[tf]
    wanted = min(bars, ch.bars_cap(minutes))
    horizon = end or dt.datetime.now(dt.UTC)
    factory = get_session_factory()

    # One bucket's worth more than needed, so the oldest (possibly cut) bucket can be dropped.
    m1 = repo.m1_bars(symbol, limit=(wanted + 1) * minutes, end=end, session_factory=factory)
    frame = ch.resample(m1, minutes, horizon)
    if len(frame) > wanted:
        frame = frame.iloc[-wanted:].reset_index(drop=True)

    events, zone_events = ch.detect(frame)
    zones = ch.recent_zones(frame, zone_events)

    pair = _pair(symbol)
    level_rows: list[LevelOut] = []
    level_basis: LevelBasisOut | None = None
    if pair is not None and not frame.empty:
        start = frame["time"].iloc[0].to_pydatetime() - ch.LEVEL_MAX_AGE
        raw = repo.levels(symbol, pair.desk, start=start, end=horizon, session_factory=factory)
        per_bar = ch.levels_per_bar(frame, raw, horizon)
        level_rows = [
            LevelOut(
                time=r.time.to_pydatetime(),
                captured_at=r.captured_at.to_pydatetime(),
                call_wall=_num(r.call_wall),
                put_wall=_num(r.put_wall),
                flip_point=_num(r.flip_point),
                spot=_num(r.spot),
            )
            for r in per_bar.itertuples(index=False)
        ]
        if level_rows:
            newest = level_rows[-1].captured_at
            match = raw.loc[raw["captured_at"] == newest]
            if not match.empty:
                row = match.iloc[-1]
                level_basis = LevelBasisOut(
                    captured_at=newest,
                    basis_at=row["basis_at"],
                    basis_kind=row["basis_kind"],
                    roll_after_basis=bool(row["roll_after_basis"]),
                )

    return ChartOut(
        symbol=symbol,
        timeframe=tf,
        horizon=horizon,
        last_bar_at=None if m1.empty else m1["ts"].iloc[-1],
        pair=_pair_out(pair),
        bars=[
            BarOut(
                time=r.time.to_pydatetime(),
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                tick_volume=int(r.tick_volume),
                spread=int(r.spread),
                forming=bool(r.forming),
            )
            for r in frame.itertuples(index=False)
        ],
        events=[EventOut(**asdict(e)) for e in events],
        zones=[ZoneOut(**asdict(z)) for z in zones],
        levels=level_rows,
        level_basis=level_basis,
    )
