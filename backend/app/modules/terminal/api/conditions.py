"""`/api/terminal/sentiment` and `/api/terminal/conditions` (T171, plans/sentiment).

Both take `as_of` and compute per request from stored observations, like `/board` and
`/regime`. The method lives in `analytics/sentiment.py`; this file only shapes it. A missing
component is `null` with a `status` saying why, never a 0 -- a neutral 50 invented for an
input the desk does not have would pull the score toward calm.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.modules.terminal.analytics import sentiment as sent
from app.modules.terminal.api.board import _AS_OF_DESC, RegimeResponse, _resolve_as_of
from app.modules.terminal.config import load_settings
from app.modules.terminal.store.db import connect

__all__ = ["router"]

router = APIRouter(tags=["terminal"])


def _num(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(v) else v


class ComponentOut(BaseModel):
    key: str
    label: str
    series: list[str]
    transform: str
    orientation: int = Field(description="+1: a high value is greed. -1: a high value is fear.")
    status: str = Field(description="ok | no_data | stale | short_history")
    value: float | None = Field(description="The transformed input, in its own units.")
    percentile: float | None = Field(description="Within its own trailing history, 0-100.")
    score: float | None = Field(description="The percentile oriented so 100 = greed.")
    value_date: dt.date | None
    detail: str


class SentimentResponse(BaseModel):
    as_of: dt.datetime
    status: str = Field(description="ok | insufficient (fewer than min_components present)")
    score: float | None = Field(description="0 = extreme fear, 100 = extreme greed.")
    band: str | None = Field(description="extreme_fear | fear | neutral | greed | extreme_greed")
    used: int
    min_components: int
    components: list[ComponentOut]
    #: Shown, not scored. See analytics/sentiment.DISPLAY_ONLY.
    display: dict[str, float | None]


class GammaOut(BaseModel):
    underlying: str
    status: str = Field(description="ok | no_data | stale")
    sign: str | None = Field(description="positive | negative dealer gamma")
    net_gex: float | None
    flip_dist_pct: float | None = Field(description="(spot - flip) / spot, %; >0 above flip")
    value_date: dt.date | None
    detail: str


class ConditionsResponse(BaseModel):
    as_of: dt.datetime
    macro: RegimeResponse | None
    macro_detail: str
    gamma: list[GammaOut]
    sentiment: SentimentResponse
    synthesis: str | None = Field(
        description="stressed | unstable | fragile | cushioned | orderly | complacent; "
        "null when SPX gamma or sentiment is unavailable.")
    synthesis_detail: str


def _sentiment_out(r: sent.SentimentReading) -> SentimentResponse:
    return SentimentResponse(
        as_of=r.as_of,
        status=r.status,
        score=_num(r.score),
        band=r.band,
        used=r.used,
        min_components=sent.MIN_COMPONENTS,
        components=[
            ComponentOut(
                key=c.key, label=c.label, series=list(c.series), transform=c.transform,
                orientation=c.orientation, status=c.status, value=_num(c.value),
                percentile=_num(c.percentile), score=_num(c.score), value_date=c.value_date,
                detail=c.detail,
            )
            for c in r.components
        ],
        display={k: _num(v) for k, v in r.display.items()},
    )


@router.get("/sentiment", response_model=SentimentResponse)
def get_sentiment(
    as_of: Annotated[dt.datetime | None, Query(description=_AS_OF_DESC)] = None,
) -> SentimentResponse:
    """The sentiment composite, with every component's value, percentile and status."""
    resolved = _resolve_as_of(as_of)
    conn = connect(read_only=True)
    try:
        reading = sent.read_sentiment(conn, resolved)
    finally:
        conn.close()
    return _sentiment_out(reading)


@router.get("/conditions", response_model=ConditionsResponse)
def get_conditions(
    as_of: Annotated[dt.datetime | None, Query(description=_AS_OF_DESC)] = None,
) -> ConditionsResponse:
    """Macro regime, dealer gamma and sentiment side by side, and the fixed synthesis of
    gamma sign x sentiment side. A description of conditions, not a signal."""
    resolved = _resolve_as_of(as_of)
    s = load_settings()
    conn = connect(read_only=True)
    try:
        c = sent.read_conditions(
            conn, resolved,
            window_days=s.regime_window_days, score_window=s.regime_score_window,
            min_z=s.regime_min_z, max_gap_days=s.max_gap_days,
        )
    finally:
        conn.close()

    macro = None
    if c.macro is not None:
        vd = c.macro.value_date
        macro = RegimeResponse(
            as_of=resolved,
            value_date=vd if isinstance(vd, dt.date) else None,
            state=c.macro.state,
            detail=c.macro.detail,
            window_days=c.macro.window_days,
            evidence={k: _num(v) for k, v in c.macro.evidence().items()},
        )
    return ConditionsResponse(
        as_of=resolved,
        macro=macro,
        macro_detail=c.macro_detail,
        gamma=[
            GammaOut(underlying=g.underlying, status=g.status, sign=g.sign,
                     net_gex=_num(g.net_gex), flip_dist_pct=_num(g.flip_dist_pct),
                     value_date=g.value_date, detail=g.detail)
            for g in c.gamma
        ],
        sentiment=_sentiment_out(c.sentiment),
        synthesis=c.synthesis,
        synthesis_detail=c.synthesis_detail,
    )
