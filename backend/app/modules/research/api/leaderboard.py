"""`GET /api/research/leaderboard` and `GET /api/research/trials/{hash}` (T78).

**The noise ceiling ships with the rows, in the same response.** Not a separate endpoint, not a
client-side calculation, and not optional. A leaderboard without it is worse than no leaderboard
because it looks authoritative: with 134,377 trials behind it, pure luck produces a best OOS
Sharpe around 5.6, so a row at 3.0 is *below the noise floor* and means nothing at all. Anything
that can render rows can render the ceiling, because it cannot get one without the other.

Every row also carries its **own** ceiling and an `above_ceiling` flag, because a three-year OOS
span and a fifteen-year one are held to very different standards (the ceiling scales as
1/sqrt(years)). The static `report.py` has always highlighted per row for that reason; the flag
is computed server-side so the page cannot drift from the HTML.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.modules.research.storage.repository import (
    LeaderboardFilters,
    leaderboard_page,
    trial_by_hash,
)

__all__ = ["router"]

router = APIRouter(tags=["research"])

#: The report's `top_n`. A page, not the whole 134k -- and the pager reports the true match
#: count so "40 of 1,912" is honest about what is being hidden.
DEFAULT_LIMIT = 40
MAX_LIMIT = 200


class LeaderboardRowOut(BaseModel):
    hash: str
    market: str
    strategy: str
    symbol: str
    timeframe: str
    params: dict[str, Any]
    is_sharpe: float | None
    oos_sharpe: float | None
    oos_cagr: float | None
    oos_max_dd: float | None
    oos_fills: int | None
    oos_exposure: float | None
    oos_years: float | None
    run_date: dt.date
    noise_ceiling: float = Field(
        description="This row's ceiling, at its own OOS span. Not the headline figure."
    )
    above_ceiling: bool = Field(
        description="`oos_sharpe` clears this row's own ceiling. False means indistinguishable "
        "from luck."
    )


class LeaderboardOut(BaseModel):
    rows: list[LeaderboardRowOut]
    total: int = Field(description="Rows matching the filters, before limit/offset.")
    total_trials: int = Field(
        description="Every trial in the registry, losers included -- the ceiling's denominator."
    )
    noise_ceiling: float = Field(
        description="Best OOS Sharpe pure noise would produce across `total_trials`, at the "
        "median OOS span of the returned rows."
    )
    median_oos_years: float
    limit: int
    offset: int


class TrialOut(BaseModel):
    hash: str
    market: str
    strategy: str
    symbol: str
    timeframe: str
    params: dict[str, Any]
    run_date: dt.date
    is_sharpe: float | None
    is_cagr: float | None
    is_max_dd: float | None
    is_fills: int | None
    oos_sharpe: float | None
    oos_cagr: float | None
    oos_max_dd: float | None
    oos_fills: int | None
    oos_exposure: float | None
    oos_bars: int | None
    oos_years: float | None


@router.get("/leaderboard", response_model=LeaderboardOut)
def get_leaderboard(
    market: Annotated[str | None, Query(description="Exact match, e.g. crypto.")] = None,
    strategy: Annotated[str | None, Query(description="Strategy family name.")] = None,
    timeframe: Annotated[str | None, Query(description="1h, 4h or 1d.")] = None,
    min_trades_oos: Annotated[
        int, Query(ge=0, description="Minimum OOS fills. Default matches research.yaml.")
    ] = 20,
    min_exposure: Annotated[
        float, Query(ge=0.0, le=1.0, description="Minimum OOS exposure. Default matches "
        "research.yaml.")
    ] = 0.02,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LeaderboardOut:
    """Ranked by OOS Sharpe, filtered, paginated, with the noise ceiling attached.

    The filter defaults are `config/research.yaml`'s, so an unparameterised call reproduces the
    static report's row set -- which is what makes "the page and the HTML agree" a checkable
    claim rather than a hope.
    """
    page = leaderboard_page(
        LeaderboardFilters(
            market=market,
            strategy=strategy,
            timeframe=timeframe,
            min_trades_oos=min_trades_oos,
            min_exposure=min_exposure,
        ),
        limit=limit,
        offset=offset,
    )
    return LeaderboardOut(
        rows=[LeaderboardRowOut(**vars(r)) for r in page.rows],
        total=page.total,
        total_trials=page.total_trials,
        noise_ceiling=page.noise_ceiling,
        median_oos_years=page.median_oos_years,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/trials/{trial_hash}", response_model=TrialOut)
def get_trial(trial_hash: str) -> TrialOut:
    """One trial by hash, with both sides of the split.

    The in-sample numbers are here and deliberately not on the leaderboard: IS performance is
    what the search fitted, so putting it next to the ranking invites reading it as evidence. On
    a detail page, with OOS beside it, the gap between the two is the interesting part.
    """
    trial = trial_by_hash(trial_hash)
    if trial is None:
        raise HTTPException(status_code=404, detail=f"no trial with hash {trial_hash!r}")
    return TrialOut(
        hash=trial.hash,
        market=trial.market,
        strategy=trial.strategy,
        symbol=trial.symbol,
        timeframe=trial.timeframe,
        params=trial.params,
        run_date=trial.run_date,
        is_sharpe=trial.is_sharpe,
        is_cagr=trial.is_cagr,
        is_max_dd=trial.is_max_dd,
        is_fills=trial.is_fills,
        oos_sharpe=trial.oos_sharpe,
        oos_cagr=trial.oos_cagr,
        oos_max_dd=trial.oos_max_dd,
        oos_fills=trial.oos_fills,
        oos_exposure=trial.oos_exposure,
        oos_bars=trial.oos_bars,
        oos_years=trial.oos_years,
    )
