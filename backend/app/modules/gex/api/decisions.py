"""Read API for the decision engine (T60) -- `app.modules.gex.scan.decisions` behind HTTP.

Routes, both computing on request (no new table, the same posture every `app.modules.gex.api.scan` route
takes for a single-user app):

* `GET /api/gex/decisions?filter=&min_score=` -- one `DecisionOut` per `Underlying` member that has
  a captured chain, opportunities ranked across the whole universe (`ranked`: `active` before
  `watch` before `rejected`, then by score), plus `no_chain` naming the members with nothing
  captured yet. `min_score` drops opportunities below it from `ranked` only; every symbol's own
  `DecisionOut` still lists all of its opportunities so the per-symbol view stays complete.
* `GET /api/gex/decisions/{underlying}?filter=` -- one symbol. 422 on an unknown symbol, and the
  same clean 404 with a specific `detail` `app.modules.gex.api.gex` uses when a symbol has never been
  captured (the contract the frontend's empty state depends on, T37).

Every input is built by `app.modules.gex.api.scan.build_regime_rows` -- the exact pipeline `/regime` runs,
factored out for this router -- so the regime a suggestion cites here is byte-identical to the
row `/regime` shows for the same symbol. The breakout summary is computed from the bars that
pipeline already fetched, with `app.modules.gex.scan.breakouts`' plan defaults (`N=20`, `k=5`, 126-bar
lookback), never a second `read_bars`.

`filter` is restricted to the persisted filters (`ALL`, `ZERO_DTE`, `EX_ZERO_DTE`) for the same
reason `/regime` is: the levels are read from `gex_levels`/`gex_by_strike`, never recomputed
from Parquet.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.modules.gex.api.gex import _canonical_underlying
from app.modules.gex.api.scan import _validate_regime_filter, build_regime_rows
from app.modules.gex.gex.engine import ExpiryFilter
from app.modules.gex.jobs.decisions import DecisionsJobResult, decide_build, record_decisions_job
from app.modules.gex.scan.decisions import DecisionResult
from app.modules.gex.scan.outcomes import summarize_outcomes
from app.modules.gex.storage import decisions_repository as repo

__all__ = ["router"]

router = APIRouter(prefix="/decisions", tags=["decisions"])

_FILTER_DESC = "One of ALL, ZERO_DTE, EX_ZERO_DTE (persisted filters only)."
_MIN_SCORE_DESC = (
    "Drop opportunities scoring below this from the cross-universe `ranked` list. Per-symbol "
    "rows are never filtered."
)


class ScoreComponentOut(BaseModel):
    name: str
    points: int
    max_points: int
    note: str


class OpportunityOut(BaseModel):
    """Mirrors `app.modules.gex.scan.decisions.Opportunity`; see that dataclass and the module docstring
    for the vocabulary of `setup`/`side`/`status` and where each level comes from."""

    key: str
    setup: str
    side: str
    status: str
    score: int
    grade: str
    entry: float
    entry_label: str
    stop: float
    stop_label: str
    target: float
    target_label: str
    target_2: float | None
    target_2_label: str | None
    risk: float
    reward: float
    rr: float | None
    risk_atr: float | None
    reward_atr: float | None
    thesis: list[str]
    invalidation: list[str]
    structure: str
    warnings: list[str]
    score_breakdown: list[ScoreComponentOut]
    rejection_reason: str | None


class DecisionOut(BaseModel):
    underlying: str
    filter: str
    spot: float
    atr14: float | None
    as_of: dt.datetime
    effective_at: dt.datetime
    stale: bool
    verdict: str | None
    positioning_direction: str | None
    positioning_ratio: float | None
    opportunities: list[OpportunityOut]
    no_trade_reasons: list[str]

    @classmethod
    def from_result(cls, result: DecisionResult) -> DecisionOut:
        return cls(**result.to_dict())


class RankedOpportunityOut(OpportunityOut):
    """An opportunity with its symbol attached, for the cross-universe list."""

    underlying: str
    spot: float


class DecisionsResponse(BaseModel):
    filter: str
    generated_from: str
    ranked: list[RankedOpportunityOut]
    symbols: list[DecisionOut]
    no_chain: list[str]


_DISCLAIMER = (
    "Suggestions computed from the latest captured chain and daily bars. Analysis only: "
    "nothing here is routed, and every level is one the engine measured."
)


_STATUS_RANK = {"active": 0, "watch": 1, "rejected": 2}


@router.get("", response_model=DecisionsResponse)
def get_decisions(
    filter_: Annotated[str, Query(alias="filter", description=_FILTER_DESC)] = ExpiryFilter.ALL.value,
    min_score: Annotated[int, Query(ge=0, le=100, description=_MIN_SCORE_DESC)] = 0,
) -> DecisionsResponse:
    """Every opportunity across the optioned universe, ranked, plus each symbol's own row."""
    parsed_filter = _validate_regime_filter(filter_)

    symbols: list[DecisionOut] = []
    no_chain: list[str] = []
    ranked: list[RankedOpportunityOut] = []
    for build in build_regime_rows(parsed_filter):
        if build.row is None:
            no_chain.append(build.symbol)
            continue
        result = decide_build(build)
        out = DecisionOut.from_result(result)
        symbols.append(out)
        for opp in out.opportunities:
            if opp.score < min_score:
                continue
            ranked.append(
                RankedOpportunityOut(**opp.model_dump(), underlying=out.underlying, spot=out.spot)
            )

    ranked.sort(key=lambda o: (_STATUS_RANK[o.status], -o.score, o.underlying, o.key))
    return DecisionsResponse(
        filter=parsed_filter.value,
        generated_from=_DISCLAIMER,
        ranked=ranked,
        symbols=symbols,
        no_chain=no_chain,
    )


# --------------------------------------------------------------------------------------------
# T61: the track record. `/history` and `/record` are declared *before* `/{underlying}` so the
# literal segments are matched first -- FastAPI resolves routes in declaration order.
# --------------------------------------------------------------------------------------------


class GroupStatsOut(BaseModel):
    n: int
    pending: int
    untriggered: int
    resolved: int
    targets: int
    stops: int
    expired: int
    hit_rate: float | None
    win_rate: float | None
    avg_r: float | None
    total_r: float | None
    best_r: float | None
    worst_r: float | None


class TrackRecordOut(BaseModel):
    overall: GroupStatsOut
    by_setup: dict[str, GroupStatsOut]
    by_grade: dict[str, GroupStatsOut]


class DecisionRecordOut(BaseModel):
    """One stored opportunity and its outcome so far; mirrors
    `app.modules.gex.storage.decisions_repository.DecisionRecord`."""

    id: int
    underlying: str
    filter: str
    snapshot_id: int
    key: str
    decided_on: dt.date
    as_of: dt.datetime
    setup: str
    side: str
    status: str
    score: int
    grade: str
    entry: float
    stop: float
    target: float
    target_2: float | None
    spot: float
    atr14: float | None
    outcome: str
    fill: float | None
    triggered_on: dt.date | None
    resolved_on: dt.date | None
    bars_held: int | None
    mfe_r: float | None
    mae_r: float | None
    result_r: float | None
    mark_r: float | None
    evaluated_through: dt.date | None
    outcome_note: str | None
    opportunity: OpportunityOut


class DecisionsHistoryResponse(BaseModel):
    records: list[DecisionRecordOut]
    summary: TrackRecordOut
    note: str


class RecordRunOut(BaseModel):
    recorded: int
    evaluated: int
    resolved: int
    errors: list[str]

    @classmethod
    def from_result(cls, result: DecisionsJobResult) -> RecordRunOut:
        return cls(**result.to_dict())


_OUTCOMES = ("pending", "untriggered", "target", "stop", "expired", "invalid")
_HISTORY_NOTE = (
    "Outcomes are scored on daily bars after the decision date: a fade fills when its wall is "
    "touched within 5 bars, a continuation at the next open; the stop is checked before the "
    "target on every bar; trades expire at the close after 10 bars. Results are in R, multiples "
    "of the planned entry-to-stop risk. Rates are withheld below 5 resolved trades."
)


@router.get("/history", response_model=DecisionsHistoryResponse)
def get_history(
    underlying: Annotated[str | None, Query(description="Narrow to one symbol.")] = None,
    outcome: Annotated[str | None, Query(description=f"One of {list(_OUTCOMES)}.")] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 200,
) -> DecisionsHistoryResponse:
    """Stored opportunities newest first, plus the track record summarized over **every**
    stored row that matches `underlying`/`outcome` (not just the `limit` returned), by setup
    and by grade."""
    canonical = None if underlying is None else _canonical_underlying(underlying)
    if outcome is not None and outcome not in _OUTCOMES:
        raise HTTPException(status_code=422, detail=f"outcome must be one of {list(_OUTCOMES)}")
    records = repo.read_history(underlying=canonical, outcome=outcome, limit=limit)
    everything = repo.read_history(underlying=canonical, outcome=outcome, limit=2000)
    summary = summarize_outcomes((r.setup, r.grade, r.outcome, r.result_r) for r in everything)
    return DecisionsHistoryResponse(
        records=[DecisionRecordOut(**r.to_dict()) for r in records],
        summary=TrackRecordOut(**summary.to_dict()),
        note=_HISTORY_NOTE,
    )


@router.post("/record", response_model=RecordRunOut, status_code=201)
async def record_now(
    filter_: Annotated[str, Query(alias="filter", description=_FILTER_DESC)] = ExpiryFilter.ALL.value,
) -> RecordRunOut:
    """Run the 17:45 ET job now: record today's opportunities (no-op for a snapshot already
    recorded) and score every pending row against the bars stored so far."""
    parsed_filter = _validate_regime_filter(filter_)
    return RecordRunOut.from_result(await record_decisions_job(filter_=parsed_filter))


@router.get("/{underlying}", response_model=DecisionOut)
def get_symbol_decisions(
    underlying: str,
    filter_: Annotated[str, Query(alias="filter", description=_FILTER_DESC)] = ExpiryFilter.ALL.value,
) -> DecisionOut:
    """One symbol's opportunities. 422 for an unknown symbol, 404 for one never captured."""
    canonical = _canonical_underlying(underlying)
    parsed_filter = _validate_regime_filter(filter_)

    builds = build_regime_rows(parsed_filter, symbols=[canonical])
    build = builds[0]
    if build.row is None:
        # Same `detail` string `app.modules.gex.api.gex._get_latest_row` raises: the frontend's empty
        # state (T37) matches on it, and this route must answer identically.
        raise HTTPException(status_code=404, detail=f"no snapshot captured yet for {canonical}")
    return DecisionOut.from_result(decide_build(build))
