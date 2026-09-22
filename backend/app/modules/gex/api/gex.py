"""Read API for GEX results (T11) -- the join between the capture pipeline (T05/T09) and the
engine (T08) on one side, and the React dashboard on the other. Neither half has ever spoken
to the other before this module.

Two of these three routes recompute rather than read `gex_levels`/`gex_by_strike`:

* `GET /gex/{underlying}/latest` and `GET /gex/{underlying}/snapshots/{id}` need
  `GexResult.profile`, which T09 never persists (`app.modules.gex.gex.store`'s own docstring: only the
  *levels* and *by-strike* aggregates are stored). Getting the profile means opening the
  snapshot's Parquet file and running `compute_all` regardless. Once that cost is already
  paid, reading `by_strike`/`levels` back off the *same* `compute_all` call -- instead of
  also querying `gex_levels`/`gex_by_strike` -- is strictly cheaper (one Parquet read instead
  of one Parquet read plus two extra round trips) and removes a whole class of bug where the
  persisted levels and the freshly computed profile could ever disagree (a different IV
  policy, a config change between capture and read, a not-yet-applied backfill).
  `compute_all` on a full ~28,650-contract chain measures ~0.43 s (engine docstring); for a
  single-user app that is an acceptable page-load cost, and it buys internal consistency for
  free. No caching layer is added on top -- there is one user, and a second request for the
  same snapshot is just another 0.43 s, not a scaling problem worth the complexity.
* `GET /gex/{underlying}/levels/history` is the one route that must scale past a single
  snapshot -- it can span months of 15-minute intraday captures once T18 ships -- so it is a
  pure `gex_levels` JOIN `snapshots` read and never opens Parquet at all.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session_factory
from app.modules.gex.api.schemas import (
    ExpiryHistoryRowOut,
    GexResultOut,
    LevelHistoryRowOut,
)
from app.modules.gex.gex.engine import ExpiryFilter, compute_all
from app.modules.gex.jobs.calendar import effective_data_time
from app.modules.gex.models.chain import Underlying
from app.modules.gex.models.db import GexByExpiry, GexLevel, Snapshot
from app.modules.gex.storage.parquet import read_snapshot, resolve_snapshot_path

__all__ = ["router"]

router = APIRouter(prefix="/gex", tags=["gex"])

_FILTER_DESC = "ExpiryFilter name, or 'EXPIRIES:<date>[,<date>...]' for an explicit list."


def _canonical_underlying(raw: str) -> str:
    """Validate and canonicalize a path underlying, or 422 -- same posture as
    `app.modules.gex.api.snapshots.trigger_capture`: an unknown symbol is a client error, not worth a
    round trip to storage first.
    """
    try:
        return Underlying(raw.strip().upper()).value
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unsupported underlying {raw!r}") from None


def _parse_filter(raw: str) -> ExpiryFilter | list[dt.date]:
    """Parse a `filter` query value into what `app.modules.gex.gex.engine.compute_all` accepts: an
    `ExpiryFilter` member, or an explicit list of dates for the `EXPIRIES:<date>[,<date>...]`
    form `app.modules.gex.gex.engine._filter_label` produces (and this parses back) -- outside the enum
    on purpose. Anything else is a 422, never a 500: `expiry_mask` itself would raise
    `ValueError` on a bad string, and that is user input, not a bug.
    """
    if raw.startswith("EXPIRIES:"):
        tail = raw[len("EXPIRIES:") :]
        if not tail:
            raise HTTPException(status_code=422, detail="EXPIRIES: filter needs at least one date")
        try:
            return [
                dt.date.fromisoformat(part.strip()) for part in tail.split(",") if part.strip()
            ]
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid date in EXPIRIES filter {raw!r}: {exc}"
            ) from None
    try:
        return ExpiryFilter(raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown filter {raw!r}; expected one of "
                f"{[f.value for f in ExpiryFilter]} or 'EXPIRIES:<date>[,<date>...]'"
            ),
        ) from None


def _parse_history_filter(raw: str) -> ExpiryFilter:
    """`levels/history` reads `gex_levels` verbatim -- it can only return rows for a filter
    `app.modules.gex.gex.store.compute_and_store` actually persisted (by default: `ALL`, `ZERO_DTE`,
    `EX_ZERO_DTE` -- `app.modules.gex.gex.store.DEFAULT_FILTERS`). An explicit `EXPIRIES:...` filter is
    never persisted under that literal string, so accepting it here would silently and
    permanently return an empty list rather than surface the mistake -- reject it instead.
    """
    try:
        return ExpiryFilter(raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"levels/history only supports persisted filters: "
                f"{[f.value for f in ExpiryFilter]} (explicit EXPIRIES: lists are never "
                f"persisted by app.modules.gex.gex.store, so they cannot be queried here)"
            ),
        ) from None


def _snapshot_meta(result_snapshot_dict: dict[str, Any], row: Snapshot) -> dict[str, Any]:
    """Merge `Snapshot.id`/`Snapshot.is_eod` into `SnapshotMeta.to_dict()` -- the exact merge
    `app.modules.gex.gex.engine.SnapshotMeta`'s own docstring names as T11's job, since that module does
    no I/O and therefore never sees the index row at all. Also merges in `effective_at`
    (T34): `row.captured_at` is the same tz-aware value the engine read off the Parquet
    snapshot (both come from the one `ChainSnapshot` this row indexes), so deriving from the
    ORM row rather than re-parsing `result_snapshot_dict["captured_at"]`'s ISO string avoids a
    redundant round trip through text.
    """
    return {
        **result_snapshot_dict,
        "id": row.id,
        "is_eod": row.is_eod,
        "effective_at": effective_data_time(
            row.captured_at, result_snapshot_dict["delayed_minutes"]
        ),
    }


def _compute_result_out(row: Snapshot, filters: ExpiryFilter | list[dt.date]) -> GexResultOut:
    """Resolve `row` to its Parquet file, load it, and run the full `compute_all` pipeline.

    `resolve_snapshot_path` is the one required join point -- never `DATA_DIR` joined by hand
    here (see that function's docstring and TASKS.md T30 for the bug that guards against).
    """
    path = resolve_snapshot_path(row)
    try:
        snapshot = read_snapshot(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"snapshot {row.id} is indexed but its Parquet file is missing ({path})",
        ) from None
    result = compute_all(snapshot, filters)
    payload = result.to_dict()
    payload["snapshot"] = _snapshot_meta(payload["snapshot"], row)
    return GexResultOut.model_validate(payload)


def _get_latest_row(session: Session, underlying: str) -> Snapshot:
    stmt = (
        select(Snapshot)
        .where(Snapshot.underlying == underlying)
        .order_by(Snapshot.captured_at.desc())
        .limit(1)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no snapshot captured yet for {underlying}")
    return row


def _get_snapshot_row(session: Session, underlying: str, snapshot_id: int) -> Snapshot:
    row = session.get(Snapshot, snapshot_id)
    if row is None or row.underlying != underlying:
        raise HTTPException(
            status_code=404, detail=f"no snapshot {snapshot_id} for underlying {underlying}"
        )
    return row


@router.get("/{underlying}/latest", response_model=GexResultOut)
def get_latest(
    underlying: str,
    filter_: Annotated[str, Query(alias="filter", description=_FILTER_DESC)] = ExpiryFilter.ALL.value,
) -> GexResultOut:
    """The most recently captured snapshot for `underlying`, fully computed (levels,
    by-strike, by-expiry and the gamma profile) under `filter`.
    """
    canonical = _canonical_underlying(underlying)
    parsed_filter = _parse_filter(filter_)
    session_factory = get_session_factory()
    with session_factory() as session:
        row = _get_latest_row(session, canonical)
        return _compute_result_out(row, parsed_filter)


@router.get("/{underlying}/snapshots/{snapshot_id}", response_model=GexResultOut)
def get_snapshot(
    underlying: str,
    snapshot_id: int,
    filter_: Annotated[str, Query(alias="filter", description=_FILTER_DESC)] = ExpiryFilter.ALL.value,
) -> GexResultOut:
    """Same shape as `latest`, for one past snapshot by its `Snapshot.id`."""
    canonical = _canonical_underlying(underlying)
    parsed_filter = _parse_filter(filter_)
    session_factory = get_session_factory()
    with session_factory() as session:
        row = _get_snapshot_row(session, canonical, snapshot_id)
        return _compute_result_out(row, parsed_filter)


@router.get("/{underlying}/levels/history", response_model=list[LevelHistoryRowOut])
def get_levels_history(
    underlying: str,
    filter_: Annotated[
        str, Query(alias="filter", description="One persisted ExpiryFilter name.")
    ] = ExpiryFilter.ALL.value,
    start: Annotated[
        dt.datetime | None, Query(description="Inclusive lower bound on captured_at.")
    ] = None,
    end: Annotated[
        dt.datetime | None, Query(description="Inclusive upper bound on captured_at.")
    ] = None,
    eod_only: Annotated[bool, Query(description="Only end-of-day captures.")] = False,
) -> list[LevelHistoryRowOut]:
    """`gex_levels` rows for `underlying`/`filter` across time, ascending by `captured_at`.

    Reads `gex_levels` JOIN `snapshots` only -- **never** opens Parquet -- so this stays cheap
    no matter how many months of captures (T18's 15-minute intraday polling especially) have
    accumulated. A naive `start`/`end` (no UTC offset) is treated as UTC rather than rejected,
    since `UTCDateTime` only ever hands back UTC-normalized instants to compare against.
    """
    canonical = _canonical_underlying(underlying)
    parsed_filter = _parse_history_filter(filter_)

    def _aware(value: dt.datetime | None) -> dt.datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)

    start_aware, end_aware = _aware(start), _aware(end)

    stmt = (
        select(GexLevel, Snapshot)
        .join(Snapshot, GexLevel.snapshot_id == Snapshot.id)
        .where(Snapshot.underlying == canonical, GexLevel.filter == parsed_filter.value)
    )
    if start_aware is not None:
        stmt = stmt.where(Snapshot.captured_at >= start_aware)
    if end_aware is not None:
        stmt = stmt.where(Snapshot.captured_at <= end_aware)
    if eod_only:
        stmt = stmt.where(Snapshot.is_eod.is_(True))
    stmt = stmt.order_by(Snapshot.captured_at.asc())

    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.execute(stmt).all()

    return [
        LevelHistoryRowOut(
            snapshot_id=level.snapshot_id,
            captured_at=snap.captured_at,
            is_eod=snap.is_eod,
            filter=level.filter,
            net_gex=level.net_gex,
            call_wall=level.call_wall,
            call_wall_gex=level.call_wall_gex,
            put_wall=level.put_wall,
            put_wall_gex=level.put_wall_gex,
            max_abs_strike=level.max_abs_strike,
            max_call_gex_strike=level.max_call_gex_strike,
            max_put_gex_strike=level.max_put_gex_strike,
            flip_point=level.flip_point,
            spot=level.spot,
            computed_at=level.computed_at,
        )
        for level, snap in rows
    ]


@router.get("/{underlying}/expiry/history", response_model=list[ExpiryHistoryRowOut])
def get_expiry_history(
    underlying: str,
    filter_: Annotated[
        str, Query(alias="filter", description="One persisted ExpiryFilter name.")
    ] = ExpiryFilter.ALL.value,
    snapshot_id: Annotated[
        int | None, Query(description="One snapshot; omit for the latest capture.")
    ] = None,
) -> list[ExpiryHistoryRowOut]:
    """The term structure of dealer gamma: `gex_by_expiry` for one snapshot (T101).

    Reads `gex_by_expiry` JOIN `snapshots` only -- **never** opens Parquet -- so it costs the
    same whether the snapshot is today's or from months ago. That is the whole point of rolling
    this up at capture time: `engine.by_expiry` has computed it since T08, but until T101 it
    was discarded at persist, so no question about a *past* term structure could be answered at
    any price short of reopening the chain.

    Ascending by expiry, which is the order a term structure is read in.
    """
    canonical = _canonical_underlying(underlying)
    parsed_filter = _parse_history_filter(filter_)

    stmt = (
        select(GexByExpiry, Snapshot)
        .join(Snapshot, GexByExpiry.snapshot_id == Snapshot.id)
        .where(Snapshot.underlying == canonical, GexByExpiry.filter == parsed_filter.value)
    )
    if snapshot_id is not None:
        stmt = stmt.where(GexByExpiry.snapshot_id == snapshot_id)
    else:
        latest = (
            select(Snapshot.id)
            .where(Snapshot.underlying == canonical)
            .order_by(Snapshot.captured_at.desc())
            .limit(1)
        )
        stmt = stmt.where(GexByExpiry.snapshot_id == latest.scalar_subquery())
    stmt = stmt.order_by(GexByExpiry.expiry)

    with get_session_factory()() as session:
        rows = session.execute(stmt).all()

    return [
        ExpiryHistoryRowOut(
            snapshot_id=row.snapshot_id,
            captured_at=snap.captured_at,
            is_eod=snap.is_eod,
            filter=row.filter,
            expiry=row.expiry,
            dte=row.dte,
            call_gex=row.call_gex,
            put_gex=row.put_gex,
            net_gex=row.net_gex,
            abs_gex=row.abs_gex,
            contracts=row.contracts,
            open_interest=row.open_interest,
        )
        for row, snap in rows
    ]
