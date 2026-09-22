"""Manual capture trigger and snapshot listing (T05).

`POST /api/gex/snapshots/capture` runs the exact same `app.modules.gex.jobs.capture.capture_snapshot` the
16:20 scheduler job uses, so a manual capture and a scheduled one differ only in who called
them and the `is_eod` flag -- there is no second, divergent code path to keep in sync.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.db import get_session_factory
from app.modules.gex.jobs.capture import capture_snapshot
from app.modules.gex.models.chain import Underlying
from app.modules.gex.models.db import Snapshot

__all__ = ["router"]

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


class CaptureResponse(BaseModel):
    """Mirrors `app.modules.gex.jobs.capture.CaptureResult` -- see that dataclass for field meanings."""

    underlying: str
    ok: bool
    contract_count: int | None
    spot: float | None
    duration_seconds: float
    snapshot_id: int | None
    parquet_path: str | None
    skipped_duplicate: bool
    error: str | None


class SnapshotOut(BaseModel):
    """Deliberately omits `parquet_path`: it's a server-side filesystem detail (see T30 in
    TASKS.md) -- a raw path relative to `DATA_DIR`, meaningless without knowing that base, and
    the frontend has no use for it -- so it never leaves the backend via this response. Nothing
    yet reads `Snapshot.parquet_path` back out through this route; the one place that needs the
    real path is `app.modules.gex.storage.parquet.resolve_snapshot_path`, used server-side only.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    underlying: str
    captured_at: dt.datetime
    source: str
    spot: float
    contract_count: int
    is_eod: bool
    #: T102. The trading session this chain's *contents* belong to -- group by this, not by
    #: `captured_at`'s date. A weekend or pre-open capture holds the previous session's book,
    #: so the two differ routinely. Null only on rows written before T102 and not yet
    #: backfilled.
    session_date: dt.date | None = None


@router.post("/capture", response_model=CaptureResponse, status_code=201)
async def trigger_capture(
    underlying: Annotated[str, Query(description="SPX, SPY, or QQQ")],
    eod: Annotated[bool, Query(description="Mark the row is_eod=True")] = False,
) -> CaptureResponse:
    """Fetch and persist one underlying's chain right now.

    422 for an underlying this app does not know about (a config/typo error, not worth
    hitting the network for); 502 when the capture itself failed (provider or storage) --
    `CaptureResult.error` from `app.modules.gex.jobs.capture` becomes the response detail either way, so
    the same message that lands in the structured log line is visible to the caller here.
    """
    try:
        Underlying(underlying.strip().upper())
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"unsupported underlying {underlying!r}"
        ) from None

    result = await capture_snapshot(underlying, is_eod=eod)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error or "capture failed")
    return CaptureResponse(**dataclasses.asdict(result))


@router.get("", response_model=list[SnapshotOut])
def list_snapshots(
    underlying: Annotated[str, Query(description="SPX, SPY, or QQQ")],
    limit: Annotated[int, Query(ge=1, le=500)] = 30,
) -> list[SnapshotOut]:
    """Most recent `limit` snapshot rows for `underlying`, newest first.

    Queries `Snapshot` directly (not `SnapshotRepository.list`, which is unbounded and
    ascending -- built for T09's date-range use case, not "give me the last N") rather than
    changing that method's contract for this route's sake.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        stmt = (
            select(Snapshot)
            .where(Snapshot.underlying == underlying.strip().upper())
            .order_by(Snapshot.captured_at.desc())
            .limit(limit)
        )
        rows = session.execute(stmt).scalars().all()
    return [SnapshotOut.model_validate(row) for row in rows]
