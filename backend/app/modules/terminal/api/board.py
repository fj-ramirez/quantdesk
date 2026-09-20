"""`/api/terminal/board`, `/regime`, `/series`, `/series/{id}` (T80).

**Every endpoint takes `as_of`, and that is the module's entire reason to exist.** A cross-asset
board that can only show you now is a quote screen; one that can re-render the world as it looked
on 16 March 2020 -- using only what was knowable then, revisions excluded -- is the thing xactx
was specified to be. The parameter is therefore not an option on a detail view, it is the first
argument of every read.

**Nothing here is precomputed.** `build_board` and `classify` run per request against stored
observations, which is what lets `as_of` be any instant rather than one of the moments a nightly
job happened to fire. The cost is bounded -- 75 series over a 250-day window -- and the
alternative would defeat the point.

**A missing value and a zero must never look alike.** Every board row carries a `status`
(`ok`, `no_data`, `insufficient_history`, `zero_variance`, `not_daily`, `transform_error`) and
the numeric fields are `null`, never 0.0, when the status is not `ok`. Same discipline as GEX's
invariant 3 about open interest, and it matters more here: at a historical `as_of` most series
legitimately have no data yet, and rendering that as a flat zero would invent a calm market.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.modules.terminal.analytics.board import BoardParams, build_board
from app.modules.terminal.analytics.regime import classify
from app.modules.terminal.config import load_settings
from app.modules.terminal.store.db import connect
from app.modules.terminal.store.query import get as query_get
from app.modules.terminal.store.query import get_metadata, vintages

__all__ = ["router"]

router = APIRouter(tags=["terminal"])

_AS_OF_DESC = (
    "Re-render the world as it looked at this instant (ISO 8601, timezone-aware). "
    "Omitted means latest-known. Revisions published after this moment are excluded."
)


def _resolve_as_of(as_of: dt.datetime | None) -> dt.datetime:
    """Default to now, and insist on tz-awareness.

    A naive timestamp cannot be compared against vintages recorded in other zones, so the store
    refuses one. Rejecting it here turns that into a 422 naming the parameter rather than a 500
    from three layers down.
    """
    if as_of is None:
        return dt.datetime.now(dt.UTC)
    if as_of.tzinfo is None:
        raise HTTPException(
            status_code=422,
            detail="as_of must be timezone-aware, e.g. 2020-03-16T17:00:00-04:00 or ...Z",
        )
    return as_of


def _clean(value: Any) -> Any:
    """pandas/numpy value -> something JSON and Pydantic can both handle.

    Three conversions, each for a concrete failure:

    **NaN -> None.** pandas uses NaN for "no value"; JSON has none, and a `float('nan')` in a
    response is both invalid JSON and, once a client coerces it, indistinguishable from a real
    number. `None` is the honest wire form and is what the UI renders as an em dash.

    **numpy scalar -> Python scalar**, so Pydantic sees `float`/`int` rather than `np.float64`.

    **`pandas.Timestamp` -> `datetime`.** `Timestamp` subclasses `datetime`, so Pydantic
    validates it happily and then pydantic-core's serializer fails with "'float' object cannot
    be interpreted as an integer" -- at response time, not at construction, which is why the
    endpoint returned a 500 while the same object printed correctly in a shell. Its nanosecond
    precision is the reason; a plain `datetime` is what the wire format can carry anyway.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if value is pd.NaT:
        return None
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
        if isinstance(value, float) and math.isnan(value):
            return None
    return value


def _row(model: type[BaseModel], record: dict[str, Any]) -> BaseModel:
    """Build `model` from a pandas record, cleaning NaN and restoring integer columns.

    pandas has no nullable integer by default: a column of ints containing one NaN becomes
    `float64`, so `beta_window` arrives as `250.0` and `n_obs` as `1842.0`. Pydantic keeps them
    as floats and serialisation then fails with "'float' object cannot be interpreted as an
    integer" -- observed, on `/edges`.

    Casting per field from the model's own annotation rather than listing the integer columns by
    hand: a list would be one more thing to update when a column is added, and its going stale
    is a 500 rather than a warning.
    """
    out: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if name not in record:
            continue
        value = _clean(record[name])
        if value is not None and int in _annotation_types(field.annotation) and not isinstance(
            value, bool
        ):
            value = int(value)
        out[name] = value
    return model(**out)


def _annotation_types(annotation: Any) -> tuple[Any, ...]:
    """The concrete types in an annotation, flattening `int | None` to `(int, NoneType)`."""
    import typing

    args = typing.get_args(annotation)
    return args or (annotation,)


class BoardRow(BaseModel):
    series_id: str
    display_name: str | None = None
    asset_class: str | None = None
    value_date: dt.date | None = None
    change: float | None = None
    change_unit: str | None = None
    z: float | None = Field(
        default=None,
        description="Standard deviations against this series' own trailing window of past "
        "changes, which always excludes the change being scored.",
    )
    percentile: float | None = None
    window_n: int | None = Field(
        default=None, description="Past changes the z was measured against."
    )
    trailing_sd: float | None = None
    vol_percentile: float | None = None
    vol_flag: str | None = Field(
        default=None,
        description="'compressed' or 'elevated': where this series' trailing volatility sits "
        "in its own history. A z of 2 against a compressed window means something different "
        "from a z of 2 against a normal one.",
    )
    stale_days: float | None = Field(
        default=None, description="Calendar days between the newest observation and as_of."
    )
    status: str = Field(
        description="ok | no_data | insufficient_history | zero_variance | not_daily | "
        "transform_error. Anything but 'ok' means the numeric fields are null, never zero."
    )
    as_of_basis: str | None = Field(
        default=None,
        description="How this observation's as_of was established: source_vintage | "
        "derived_lag | archive_floor.",
    )


class BoardResponse(BaseModel):
    as_of: dt.datetime
    rows: list[BoardRow]
    #: The parameters the board was built with, echoed so any figure on screen is reproducible
    #: from stored inputs plus this set plus the as_of (spec 0.4).
    params: dict[str, Any]
    ok_count: int = Field(description="Rows with a computable z. The rest have a status saying why.")


class RegimeResponse(BaseModel):
    as_of: dt.datetime
    value_date: dt.date | None
    state: str = Field(description="risk_off | risk_on | rates_led | quiet")
    detail: str
    window_days: int
    #: The three legs, as z-scores. `quiet` means none of them moved enough to call a regime --
    #: an absence of one, not a fourth kind.
    evidence: dict[str, float | None]


class SeriesOut(BaseModel):
    series_id: str
    display_name: str
    source: str
    asset_class: str
    category: str
    unit: str
    frequency: str
    revisable: bool
    vintage_source: str
    notes: str | None = None


class ObservationOut(BaseModel):
    value_date: dt.date
    value: float
    as_of: dt.datetime
    as_of_basis: str


class VintageOut(BaseModel):
    as_of: dt.datetime
    value: float
    as_of_basis: str


class SeriesDetail(BaseModel):
    series: SeriesOut
    observations: list[ObservationOut]
    #: Every stored vintage of the most recent value_date in range. This is the traceability
    #: escape hatch made visible: given a number on screen, what did we think before?
    latest_vintages: list[VintageOut]


@router.get("/board", response_model=BoardResponse)
def get_board(
    as_of: Annotated[dt.datetime | None, Query(description=_AS_OF_DESC)] = None,
    asset_class: Annotated[str | None, Query(description="Filter to one asset class.")] = None,
) -> BoardResponse:
    """The normalized change board: every series, its move, and how unusual that move is."""
    resolved = _resolve_as_of(as_of)
    s = load_settings()
    params = BoardParams(
        as_of=resolved,
        zscore_window=s.zscore_window,
        min_observations=s.min_zscore_observations,
        max_gap_days=s.max_gap_days,
        vol_percentile_window=s.vol_percentile_window,
        vol_extreme_low_pct=s.vol_extreme_low_pct,
        vol_extreme_high_pct=s.vol_extreme_high_pct,
        stale_warn_days=s.stale_warn_days,
    )

    conn = connect(read_only=True)
    try:
        frame = build_board(conn, params)
    finally:
        conn.close()

    if asset_class:
        frame = frame[frame["asset_class"] == asset_class]

    rows = [_row(BoardRow, record) for record in frame.to_dict(orient="records")]
    return BoardResponse(
        as_of=resolved,
        rows=rows,
        params={
            "zscore_window": params.zscore_window,
            "min_observations": params.min_observations,
            "max_gap_days": params.max_gap_days,
            "vol_percentile_window": params.vol_percentile_window,
            "stale_warn_days": params.stale_warn_days,
        },
        ok_count=sum(1 for r in rows if r.status == "ok"),
    )


@router.get("/regime", response_model=RegimeResponse)
def get_regime(
    as_of: Annotated[dt.datetime | None, Query(description=_AS_OF_DESC)] = None,
) -> RegimeResponse:
    """The three-leg regime reading, with the evidence that produced it.

    Never just a label: a state without its real-yield, credit and dollar z-scores is an opinion
    you cannot check. `quiet` is a real answer meaning no leg moved enough to call a regime.
    """
    resolved = _resolve_as_of(as_of)
    s = load_settings()

    conn = connect(read_only=True)
    try:
        reading = classify(
            conn,
            resolved,
            window_days=s.regime_window_days,
            score_window=s.regime_score_window,
            min_z=s.regime_min_z,
            max_gap_days=s.max_gap_days,
        )
    finally:
        conn.close()

    value_date = reading.value_date if isinstance(reading.value_date, dt.date) else None
    return RegimeResponse(
        as_of=resolved,
        value_date=value_date,
        state=reading.state,
        detail=reading.detail,
        window_days=reading.window_days,
        evidence={k: _clean(v) for k, v in reading.evidence().items()},
    )


@router.get("/series", response_model=list[SeriesOut])
def list_series() -> list[SeriesOut]:
    """Every registered series. The universe the board is drawn from."""
    conn = connect(read_only=True)
    try:
        frame = get_metadata(conn)
    finally:
        conn.close()
    return [_row(SeriesOut, record) for record in frame.to_dict(orient="records")]


@router.get("/series/{series_id}", response_model=SeriesDetail)
def get_series(
    series_id: str,
    as_of: Annotated[dt.datetime | None, Query(description=_AS_OF_DESC)] = None,
    start: Annotated[dt.date | None, Query()] = None,
    end: Annotated[dt.date | None, Query()] = None,
) -> SeriesDetail:
    """One series as of a moment, plus every stored vintage of its latest value.

    The vintage list is the traceability escape hatch (spec 0.4) surfaced to the screen: given a
    number on the board, this is what we believed about it before, and when we changed our mind.
    """
    resolved = _resolve_as_of(as_of)
    conn = connect(read_only=True)
    try:
        meta = get_metadata(conn, series_id)
        if meta.empty:
            raise HTTPException(status_code=404, detail=f"no series {series_id!r}")

        default_start = start or (resolved.date() - dt.timedelta(days=365))
        frame = query_get(
            conn, series_id, default_start, end or resolved.date(),
            as_of=resolved, warn_empty=False,
        )
        vintage_rows: list[VintageOut] = []
        if not frame.empty:
            latest_date = frame["value_date"].iloc[-1]
            if not isinstance(latest_date, dt.date):
                latest_date = latest_date.date()
            vframe = vintages(conn, series_id, latest_date)
            vintage_rows = [
                VintageOut(
                    as_of=r["as_of"], value=_clean(r["value"]), as_of_basis=r["as_of_basis"]
                )
                for r in vframe.to_dict(orient="records")
            ]
    finally:
        conn.close()

    record = meta.to_dict(orient="records")[0]
    return SeriesDetail(
        series=_row(SeriesOut, record),
        observations=[
            ObservationOut(
                value_date=r["value_date"] if isinstance(r["value_date"], dt.date)
                else r["value_date"].date(),
                value=_clean(r["value"]),
                as_of=r["as_of"],
                as_of_basis=r["as_of_basis"],
            )
            for r in frame.to_dict(orient="records")
        ],
        latest_vintages=vintage_rows,
    )
