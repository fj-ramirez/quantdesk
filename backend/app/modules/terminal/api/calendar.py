"""`/api/terminal/calendar` -- what is scheduled, as it was known at `as_of` (T139).

Reads `terminal.releases`, which holds the Fed's FOMC meetings and faireconomy.media's weekly
feed. Point-in-time like every other terminal read: each release appears as its newest vintage
not later than `as_of`, so a past calendar shows the consensus as it stood then, not as it was
later revised.

Three things the response carries so a reader cannot get them wrong:

* `consensus` is the publisher's forecast **as first seen before the event**
  (`consensus_as_of`). Null means none was published, or we first saw the event after it
  happened. It never means zero.
* `impact` is the publisher's generic rating, not the desk's judgment of what matters.
* `last_fetch` per source says when the calendar was last refreshed. The feed covers one week
  at a time, so a calendar that has not refreshed in days is missing events rather than quiet.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.modules.terminal import releases
from app.modules.terminal.api.board import _resolve_as_of
from app.modules.terminal.store.db import connect

__all__ = ["router"]

router = APIRouter(tags=["terminal"])


class ReleaseRow(BaseModel):
    release_id: str
    source: str
    title: str
    country: str
    impact: str | None = Field(
        default=None, description="The publisher's generic rating, kept as given."
    )
    scheduled_at: dt.datetime
    starts_on: dt.date | None = Field(
        default=None, description="First day of a multi-day event (an FOMC meeting)."
    )
    consensus: float | None = Field(
        default=None,
        description="The forecast as one number, as first seen before the event. Null is "
        "unpublished, first seen too late, or not a single number. Never zero.",
    )
    consensus_raw: str | None = Field(
        default=None, description="The forecast exactly as published, e.g. '5.31|2.6'."
    )
    consensus_as_of: dt.datetime | None = None
    prior: float | None = None
    prior_raw: str | None = None
    as_of: dt.datetime = Field(description="When our fetch first saw this vintage.")


class CalendarResponse(BaseModel):
    as_of: dt.datetime
    window_start: dt.datetime
    window_end: dt.datetime
    last_fetch: dict[str, dt.datetime | None] = Field(
        description="Last clean fetch per source. A stale one means missing events, not a "
        "quiet week."
    )
    rows: list[ReleaseRow]


@router.get("/calendar", response_model=CalendarResponse)
def get_calendar(
    as_of: Annotated[
        dt.datetime | None,
        Query(description="Re-render the calendar as it was known at this instant. Omitted "
              "means latest-known."),
    ] = None,
    days: Annotated[int, Query(ge=1, le=60)] = 7,
    impact: Annotated[
        str | None,
        Query(description="Comma-separated, e.g. 'High,Medium'. Omitted means every rating."),
    ] = None,
) -> CalendarResponse:
    """Releases from midnight ET on `as_of`'s date through `days` days ahead."""
    resolved = _resolve_as_of(as_of)
    start = dt.datetime.combine(resolved.astimezone(releases.ET).date(), dt.time(0),
                                tzinfo=releases.ET)
    impacts = [i.strip() for i in impact.split(",") if i.strip()] if impact else None

    conn = connect(read_only=True)
    try:
        rows = releases.upcoming(conn, resolved, days=days, start=start, impacts=impacts)
        last_fetch = {
            source: releases.last_successful_fetch(conn, source)
            for source in (releases.SOURCE_FED, releases.SOURCE_FAIRECONOMY)
        }
    finally:
        conn.close()

    return CalendarResponse(
        as_of=resolved,
        window_start=start,
        window_end=start + dt.timedelta(days=days),
        last_fetch=last_fetch,
        rows=[ReleaseRow(**{k: v for k, v in r.items() if k != "status"}) for r in rows],
    )
