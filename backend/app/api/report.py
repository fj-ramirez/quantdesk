"""Read API for the options-intelligence report (T39) -- `app.gex.report` behind HTTP.

Shape and posture are deliberately identical to `app.api.gex`: same underlying validation
(422 on an unknown symbol), same filter parsing (including the `EXPIRIES:<date>[,...]` form),
same clean 404 with a specific `detail` when a symbol has never been captured -- that 404 is
the contract T37's empty state depends on, and T40's report page depends on it too.

**Why this opens Parquet rather than reading `gex_levels`.** T39 says to reuse the stored
snapshot the way `api/gex.py` does and not to recompute what is already persisted. Reusing the
stored *snapshot* is exactly what happens here. Reading the report's inputs back off Postgres
is not possible, and this is a fact about the schema rather than a shortcut: `gex_levels`
holds walls, net/abs GEX and the flip point, and `gex_by_strike` holds per-strike aggregates.
The report additionally needs *per-contract* `open_interest` split by right (max pain, the
put/call ratios), `volume` (the volume ratio), `iv` with `dte` and `strike` (the ATM 30-day
vol) and `bid`/`ask` (the premium screen). None of those survive aggregation, and none is in
either table. They live only in the Parquet file, so the file has to be opened.

What *is* avoided is doing the work twice: one `read_snapshot`, one `to_frame`, and that one
frame is handed to both `compute_all` and `build_report` (the engine's own guidance -- reuse a
single `to_frame` across consumers, since flattening rather than aggregation is the expensive
step). No new table is added, per T39.

`?format=text` returns `app.gex.report.render_text` output as `text/plain` so the report exists
outside the browser -- `curl`-able, pipeable, diffable between two captures.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy.orm import Session

# Imported from `app.api.gex` rather than reimplemented: these four are the *contract* for
# how this API answers an unknown symbol (422), an uncaptured one (404 with a specific
# `detail`, which T37's empty state matches on) and a filter string, and the report endpoint
# must answer identically or the frontend needs a second code path for the same conditions.
# They are underscore-private to signal "not part of the public route surface", not to bar
# reuse inside the API package.
from app.api.gex import _canonical_underlying, _get_latest_row, _parse_filter, _snapshot_meta
from app.api.schemas import ReportOut
from app.gex.engine import ExpiryFilter, compute_all, to_frame
from app.gex.report import build_report, render_text
from app.jobs.capture import get_session_factory
from app.models.db import Snapshot
from app.storage.parquet import read_snapshot, resolve_snapshot_path

__all__ = ["router"]

router = APIRouter(prefix="/report", tags=["report"])

_FILTER_DESC = "ExpiryFilter name, or 'EXPIRIES:<date>[,<date>...]' for an explicit list."
_FORMAT_DESC = "'json' (default) for the structured report, or 'text' for the rendered report."
_CFD_SPOT_DESC = (
    "The CFD instrument's spot, read off the user's own trading platform (T41), e.g. "
    "XAUUSD for GLD. Omit to get today's report exactly, with no converted block. Must be a "
    "positive, finite number -- FastAPI rejects a non-numeric value with 422 on its own, and "
    "`gt=0` rejects zero or negative here."
)


def _build(
    row: Snapshot, filters: ExpiryFilter | list[dt.date], cfd_spot: float | None
) -> tuple[Any, ReportOut]:
    """Load `row`'s Parquet chain once and return `(ReportResult, ReportOut)`.

    Both are returned because `?format=text` renders from the dataclass while the JSON
    response validates the dict -- computing the chain twice to serve two representations of
    the same numbers would be the one way these could ever disagree.

    `resolve_snapshot_path` is the required join point for `parquet_path`; never join
    `DATA_DIR` by hand here (see that function's docstring and TASKS.md T30).

    `cfd_spot` (T41) is threaded straight through to `build_report`, which leaves
    `ReportResult.cfd` `None` when it is `None` -- the only state this parameter can be in
    given `?cfd_spot=` is optional, so an unconverted report is unaffected either way.
    """
    path = resolve_snapshot_path(row)
    try:
        snapshot = read_snapshot(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"snapshot {row.id} is indexed but its Parquet file is missing ({path})",
        ) from None

    # One flatten, two consumers. `compute_all` applies `filters` internally; `build_report`
    # is handed the *unfiltered* frame plus the same `filters` and applies the identical mask
    # itself, so both halves of the report describe one contract population.
    frame = to_frame(snapshot)
    result = compute_all(snapshot, filters, frame=frame)
    try:
        report = build_report(result, frame, filters, cfd_spot=cfd_spot)
    except ValueError as exc:
        # `translate_to_cfd` raises on a non-positive/non-finite spot or an unmapped
        # underlying. `gt=0` on the query parameter already rejects zero/negative before this
        # is ever reached, but the pure module re-validates rather than trusting the caller --
        # this is the belt to that braces, not dead code.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    payload = report.to_dict()
    payload["snapshot"] = _snapshot_meta(payload["snapshot"], row)
    return report, ReportOut.model_validate(payload)


def _resolve(session: Session, underlying: str, snapshot_id: int | None) -> Snapshot:
    if snapshot_id is None:
        return _get_latest_row(session, underlying)
    row = session.get(Snapshot, snapshot_id)
    if row is None or row.underlying != underlying:
        raise HTTPException(
            status_code=404, detail=f"no snapshot {snapshot_id} for underlying {underlying}"
        )
    return row


@router.get(
    "/{underlying}",
    response_model=ReportOut,
    responses={200: {"content": {"text/plain": {}}}},
)
def get_report(
    underlying: str,
    filter_: Annotated[str, Query(alias="filter", description=_FILTER_DESC)] = ExpiryFilter.ALL.value,
    format_: Annotated[str, Query(alias="format", description=_FORMAT_DESC)] = "json",
    snapshot_id: Annotated[
        int | None, Query(alias="snapshot", description="Pin a specific snapshot id.")
    ] = None,
    cfd_spot: Annotated[float | None, Query(alias="cfd_spot", gt=0, description=_CFD_SPOT_DESC)] = None,
) -> Any:
    """The options-intelligence report for `underlying`'s most recent snapshot (or `snapshot`).

    Returns `ReportOut` as JSON, or the plain-text rendering when `format=text`.

    Note what this endpoint deliberately does **not** supply: `build_report`'s `iv_history`.
    Nothing in the schema persists past ATM implied vols -- `gex_levels` has no such column,
    and adding one is a migration T39 was scoped away from -- so the IV regime label comes back
    `None` and the UI renders "insufficient history". That is the honest answer on this
    database, not a gap to paper over with a hardcoded band.

    `cfd_spot` (T41) is optional and off by default: absent, `ReportOut.cfd` is `None` and the
    rest of the response is unaffected. `Query(..., gt=0)` rejects a zero, negative or
    non-numeric value with a 422 before it ever reaches `app.gex.report` -- there is no path by
    which a bad spot here produces an infinity or a NaN scattered through the converted block.
    """
    canonical = _canonical_underlying(underlying)
    parsed_filter = _parse_filter(filter_)

    normalized = format_.strip().lower()
    if normalized not in ("json", "text"):
        raise HTTPException(
            status_code=422, detail=f"unknown format {format_!r}; expected 'json' or 'text'"
        )

    session_factory = get_session_factory()
    with session_factory() as session:
        row = _resolve(session, canonical, snapshot_id)
        report, out = _build(row, parsed_filter, cfd_spot)

    if normalized == "text":
        # `charset=utf-8` stated explicitly: the rendered report is ASCII today but the
        # descriptions it carries are free text, and a client should never have to guess.
        return Response(content=render_text(report), media_type="text/plain; charset=utf-8")
    return out
