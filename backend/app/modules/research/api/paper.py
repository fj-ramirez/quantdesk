"""`GET /api/research/paper` and `GET /api/research/status` (T78).

The paper watchlist is the honest half of this module. A leaderboard row is a number the search
found by looking; a paper candidate is a commitment made at a point in time, and everything that
has happened to it since `promoted_at` was never fitted. That is the only evidence here that
cannot have been mined.

So the list is ordered by promotion date, oldest first, and deliberately **not** by performance.
Sorting a forward record by how well each entry has done since is how an honest table quietly
becomes another leaderboard.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.modules.research.storage.repository import paper_candidates, registry_status

__all__ = ["router"]

router = APIRouter(tags=["research"])


class PaperCandidateOut(BaseModel):
    hash: str
    promoted_at: dt.datetime
    market: str
    strategy: str
    symbol: str
    timeframe: str
    params: dict[str, Any]
    promoted_oos_sharpe: float | None = Field(
        description="OOS Sharpe at the moment of promotion. Everything after `promoted_at` is "
        "forward performance and was never fitted."
    )
    sharpe_2x: float | None = Field(
        description="OOS Sharpe recomputed at doubled costs. An edge that dies here was never "
        "an edge."
    )
    neighbor_med: float | None = Field(
        description="Median OOS Sharpe of adjacent parameter values. A lone spike is "
        "overfitting."
    )
    wf_pos: int | None = Field(description="Walk-forward windows that were profitable.")
    wf_active: int | None = Field(description="Walk-forward windows the strategy traded in.")
    wf_med: float | None = Field(description="Median walk-forward Sharpe.")
    corr_max: float | None = Field(
        description="Highest correlation with an existing watchlist member at promotion."
    )


class StatusOut(BaseModel):
    total_trials: int
    paper_candidates: int
    cycles: int = Field(description="Distinct run dates -- days on which a cycle recorded work.")
    last_run_date: dt.date | None
    trials_last_cycle: int
    # The filter bar's options come from the data rather than a hard-coded list, so a strategy
    # family added to `strategies.py` appears in the UI the first cycle after it runs, with no
    # frontend change.
    markets: list[str]
    strategies: list[str]
    timeframes: list[str]


@router.get("/paper", response_model=list[PaperCandidateOut])
def get_paper_candidates() -> list[PaperCandidateOut]:
    """The forward-tracking watchlist, oldest promotion first."""
    return [
        PaperCandidateOut(
            hash=c.hash,
            promoted_at=c.promoted_at,
            market=c.market,
            strategy=c.strategy,
            symbol=c.symbol,
            timeframe=c.timeframe,
            params=c.params,
            promoted_oos_sharpe=c.promoted_oos_sharpe,
            sharpe_2x=c.sharpe_2x,
            neighbor_med=c.neighbor_med,
            wf_pos=c.wf_pos,
            wf_active=c.wf_active,
            wf_med=c.wf_med,
            corr_max=c.corr_max,
        )
        for c in paper_candidates()
    ]


@router.get("/status", response_model=StatusOut)
def get_status() -> StatusOut:
    """How much has been tried, when the search last ran, and what can be filtered by.

    Deliberately cheap -- counts and distincts -- because the status strip renders on every page
    of the module and must never be the reason one is slow.
    """
    s = registry_status()
    return StatusOut(
        total_trials=s.total_trials,
        paper_candidates=s.paper_candidates,
        cycles=s.cycles,
        last_run_date=s.last_run_date,
        trials_last_cycle=s.trials_last_cycle,
        markets=s.markets,
        strategies=s.strategies,
        timeframes=s.timeframes,
    )
