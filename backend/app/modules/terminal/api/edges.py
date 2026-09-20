"""`/api/terminal/edges`, `/policy`, `/brief` (T80).

The transmission graph, the implied policy path, and the daily brief -- each `as_of`-aware.

**The graph's two highest-value outputs are `corr_percentile` and `sign_conflict`**, and spec 4
says so explicitly. Neither is a number you can read off a correlation matrix:

* `corr_percentile` says where today's rolling correlation sits *in its own history*. A
  correlation of 0.4 means nothing until you know whether that is the 5th or the 95th percentile
  for this pair. A fixed-window correlation is an average over regimes, which is why the window
  is reported alongside every estimate rather than assumed.
* `sign_conflict` fires when the empirical relationship has the opposite sign to the one theory
  predicts. That is the interesting state -- it is the market telling you the usual channel is
  not operating -- and it is exactly what a plain heatmap buries.

`expected_sign == 0` is a **real value**, not a missing one: it means the sign is genuinely
regime-dependent, and asserting one would mislead precisely when it matters most. The API passes
it through as 0 and the UI must not render it as "unknown".
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.modules.terminal.api.board import _clean, _resolve_as_of, _row
from app.modules.terminal.config import load_settings
from app.modules.terminal.store.db import connect

__all__ = ["router"]

router = APIRouter(tags=["terminal"])

_AS_OF_DESC = (
    "Re-render as it looked at this instant (ISO 8601, timezone-aware). Omitted means "
    "latest-known."
)


class EdgeRow(BaseModel):
    from_series: str
    to_series: str
    expected_sign: int = Field(
        description="+1 / -1 / 0 from theory. **0 is a real value**: the sign is genuinely "
        "regime-dependent, not unknown."
    )
    typical_lag_days: int
    chain: str | None = None
    note: str | None = None
    as_of: dt.datetime | None = None
    value_date: dt.date | None = None
    beta: float | None = None
    beta_window: int | None = Field(
        default=None,
        description="Observations the estimate covers. Reported alongside it because a "
        "fixed-window beta is an average over regimes.",
    )
    beta_t_stat: float | None = None
    r_squared: float | None = None
    corr: float | None = None
    corr_percentile: float | None = Field(
        default=None,
        description="Where this correlation sits in its own trailing history. One of the two "
        "highest-value outputs of the graph.",
    )
    corr_history_n: int | None = None
    sign_conflict: bool | None = Field(
        default=None,
        description="The empirical sign disagrees with theory -- the usual channel is not "
        "operating. The other highest-value output.",
    )
    significant: bool | None = None
    n_obs: int | None = None


class EdgesResponse(BaseModel):
    as_of: dt.datetime
    edges: list[EdgeRow]
    conflicts: int = Field(description="Edges whose empirical sign disagrees with theory.")
    #: Definitions with no estimate at or before `as_of`. Named rather than silently dropped:
    #: an edge missing from a graph and an edge with no data look identical on screen otherwise.
    unestimated: list[str]


class PolicyStep(BaseModel):
    meeting_date: dt.date
    implied_rate: float | None = None
    change_bp: float | None = None


class PolicyResponse(BaseModel):
    as_of: dt.datetime
    steps: list[PolicyStep]
    #: Empty when the futures curve needed to solve the path is not available at this `as_of`.
    #: Said explicitly rather than returning a flat line.
    note: str | None = None


class BriefResponse(BaseModel):
    as_of: dt.datetime
    markdown: str
    unanswered: int = Field(
        description="How many of the brief's five standing questions could not be answered "
        "from stored data. Stated by the document itself, and echoed here so a caller cannot "
        "arrive at a different count."
    )


@router.get("/edges", response_model=EdgesResponse)
def get_edges(
    as_of: Annotated[dt.datetime | None, Query(description=_AS_OF_DESC)] = None,
) -> EdgesResponse:
    """The transmission graph: definitions joined to their most recent estimate at `as_of`.

    The join is a correlated subquery picking the latest `edge_stats` row not after `as_of`,
    which is the same point-in-time discipline `observations` gets. An edge estimated last night
    must not appear on a board rendered as of last March.
    """
    resolved = _resolve_as_of(as_of)

    conn = connect(read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT d.from_series, d.to_series, d.expected_sign, d.typical_lag_days,
                   d.chain, d.note,
                   s.as_of, s.value_date, s.beta, s.beta_window, s.beta_t_stat,
                   s.r_squared, s.corr, s.corr_percentile, s.corr_history_n,
                   s.sign_conflict, s.significant, s.n_obs
            FROM edge_definitions d
            LEFT JOIN LATERAL (
                SELECT * FROM edge_stats e
                WHERE e.from_series = d.from_series
                  AND e.to_series = d.to_series
                  AND e.as_of <= CAST(? AS timestamptz)
                ORDER BY e.as_of DESC
                LIMIT 1
            ) s ON TRUE
            ORDER BY d.from_series, d.to_series
            """,
            [resolved],
        ).fetch_df()
    finally:
        conn.close()

    edges = [_row(EdgeRow, record) for record in rows.to_dict(orient="records")]
    return EdgesResponse(
        as_of=resolved,
        edges=edges,
        conflicts=sum(1 for e in edges if e.sign_conflict),
        unestimated=[f"{e.from_series}->{e.to_series}" for e in edges if e.beta is None],
    )


@router.get("/policy", response_model=PolicyResponse)
def get_policy(
    as_of: Annotated[dt.datetime | None, Query(description=_AS_OF_DESC)] = None,
) -> PolicyResponse:
    """The implied policy path, read from the stored `policy.ff.meeting_*` series.

    Read from storage rather than re-solved per request: the path is derived from fed funds
    futures settlements by the nightly `policy` step, and those settlements are themselves
    point-in-time observations. Re-solving here would mean the screen and the stored series
    could disagree, and the stored one is what the brief and the board's rates rows already use.
    """
    resolved = _resolve_as_of(as_of)

    conn = connect(read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT m.series_id, m.value_date, m.value, c.value AS change_value
            FROM (
                SELECT DISTINCT ON (series_id) series_id, value_date, value
                FROM observations
                WHERE series_id LIKE 'policy.ff.meeting_%'
                  AND series_id NOT LIKE '%.chg'
                  AND as_of <= CAST(? AS timestamptz)
                ORDER BY series_id, value_date DESC, as_of DESC
            ) m
            LEFT JOIN (
                SELECT DISTINCT ON (series_id) series_id, value
                FROM observations
                WHERE series_id LIKE 'policy.ff.meeting_%.chg'
                  AND as_of <= CAST(? AS timestamptz)
                ORDER BY series_id, value_date DESC, as_of DESC
            ) c ON c.series_id = m.series_id || '.chg'
            ORDER BY m.series_id
            """,
            [resolved, resolved],
        ).fetch_df()
    finally:
        conn.close()

    steps = [
        PolicyStep(
            meeting_date=r["value_date"] if isinstance(r["value_date"], dt.date)
            else r["value_date"].date(),
            implied_rate=_clean(r["value"]),
            change_bp=_clean(r["change_value"]),
        )
        for r in rows.to_dict(orient="records")
    ]
    note = None
    if not steps:
        note = (
            "No implied path is stored at this as_of. The fed funds futures curve it is solved "
            "from had not been ingested yet, or the nightly policy step has not run for this "
            "date. This is an absence of data, not a flat path."
        )
    return PolicyResponse(as_of=resolved, steps=steps, note=note)


@router.get("/brief", response_model=BriefResponse)
def get_brief(
    as_of: Annotated[dt.datetime | None, Query(description=_AS_OF_DESC)] = None,
) -> BriefResponse:
    """The daily brief, as markdown, rendered as a page rather than printed to stdout.

    Returned as markdown rather than pre-rendered HTML: the brief is prose with tables, the
    frontend already has type styles, and shipping HTML from an API would mean the server owning
    presentation it cannot see.
    """
    resolved = _resolve_as_of(as_of)
    settings = load_settings()

    from app.modules.terminal.brief import generate_with_count

    # **Writable, and that is a wart, not a decision.** The ported `section_affects` calls
    # `graph.register()` and `graph.estimate_all()`, so generating the brief both writes edge
    # definitions and recomputes the transmission graph. That made sense when `brief` was a CLI
    # command you ran after ingesting; it is wrong for an HTTP GET, which should be safe to
    # cache, safe to serve from a replica, and readable by the `quantdesk_ro` role.
    #
    # Not fixed here because the fix belongs in `brief.py` -- `section_affects` should read
    # stored `edge_stats` the way `/edges` already does, which is also far cheaper than
    # re-estimating the whole panel on every page load -- and T79's port deliberately kept that
    # file diffable against the standalone repo. Logged as T85.
    conn = connect(read_only=False)
    try:
        text, unanswered = generate_with_count(conn, resolved, settings)
    finally:
        conn.close()

    return BriefResponse(as_of=resolved, markdown=text, unanswered=unanswered)
