"""Raw-chain read API (T11): `GET /chains/{underlying}/latest?expiry=`.

Returns the contracts of one expiry from the most recently captured snapshot, straight off
`OptionContract` -- no GEX computed here at all. This exists for a raw-chain / order-book-ish
view, and as the thing a UI cross-checks a `GexResult` against (e.g. "does this strike's
`open_interest` match what the by-strike chart implies").
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.schemas import ChainResponse, ContractOut
from app.jobs.capture import get_session_factory
from app.models.chain import Underlying
from app.models.db import Snapshot
from app.storage.parquet import read_snapshot, resolve_snapshot_path

__all__ = ["router"]

router = APIRouter(prefix="/chains", tags=["chains"])


def _canonical_underlying(raw: str) -> str:
    """Same validation as `app.api.gex._canonical_underlying` -- kept local rather than
    imported so this router has no dependency on the GEX router module.
    """
    try:
        return Underlying(raw.strip().upper()).value
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unsupported underlying {raw!r}") from None


@router.get("/{underlying}/latest", response_model=ChainResponse)
def get_latest_chain(
    underlying: str,
    expiry: Annotated[dt.date, Query(description="Expiry date, e.g. 2026-09-18.")],
) -> ChainResponse:
    """Every contract of `expiry` (both SPX/SPXW roots, if applicable) from the most recent
    snapshot for `underlying`. An `expiry` with no listed contracts returns an empty
    `contracts` list, not a 404 -- the snapshot and underlying are valid, the date just has
    nothing on it (e.g. a weekend, or an expiry outside what was captured).
    """
    canonical = _canonical_underlying(underlying)
    session_factory = get_session_factory()
    with session_factory() as session:
        stmt = (
            select(Snapshot)
            .where(Snapshot.underlying == canonical)
            .order_by(Snapshot.captured_at.desc())
            .limit(1)
        )
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"no snapshot captured yet for {canonical}"
            )

        path = resolve_snapshot_path(row)
        try:
            snapshot = read_snapshot(path)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404,
                detail=f"snapshot {row.id} is indexed but its Parquet file is missing ({path})",
            ) from None

        contracts = tuple(
            ContractOut.model_validate(c)
            for c in sorted(
                (c for c in snapshot.contracts if c.expiry == expiry),
                key=lambda c: (c.strike, c.right.value),
            )
        )

        return ChainResponse(
            underlying=canonical,
            expiry=expiry,
            snapshot={
                "id": row.id,
                "is_eod": row.is_eod,
                "underlying": snapshot.underlying.value,
                "spot": snapshot.spot,
                "captured_at": snapshot.captured_at,
                "source": snapshot.source,
                "delayed_minutes": snapshot.delayed_minutes,
                "contract_count": len(snapshot.contracts),
            },
            contracts=contracts,
        )
