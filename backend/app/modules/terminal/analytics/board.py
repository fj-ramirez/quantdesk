"""The normalized change board (spec 3.1).

For every series: the latest change, expressed as a z-score against its own
trailing distribution of changes, sorted by |z|.

Four things this does that a naive version does not:

  * The trailing window EXCLUDES the change being scored. Including it lets a
    large move partly define its own normality and shrinks every extreme.
  * Changes spanning a wide gap are kept out of the trailing distribution. A
    ten-day move is not a draw from the one-day distribution.
  * The trailing volatility is itself scored against its own history, because a
    z of 2 against a compressed window is a different event from a z of 2
    against a normal one (spec 3.1).
  * Every row is built from a single explicit as_of, and a series whose newest
    observation is stale at that moment says so rather than being compared with
    fresher ones (spec 7).

A series with too little history is reported with a status, never dropped
silently and never given a z computed from a handful of points.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ..errors import DataIntegrityError
from ..logging import get_logger
from ..models import SeriesMeta

# T79: was `import duckdb`, with a DuckDB connection type in every signature below.
# `store.db` is still the only module that knows the engine (its docstring always said
# so); `Connection` is the alias it exports, so the next swap is one line there.
from ..store.db import Connection
from ..store.query import assert_no_lookahead, get, get_metadata
from .transforms import apply_transform

log = get_logger("analytics.board")

# Calendar days of history to pull per change, by series frequency. Weekends,
# holidays and publication gaps all cost calendar days, so these run ahead of
# the nominal period: under-fetching silently shortens the window, which would
# be invisible in the output.
CALENDAR_DAYS_PER_CHANGE = {"d": 1.75, "w": 8.0, "m": 32.0, "q": 95.0}

# The board is a DAILY change board (spec 3.1: today's change against a trailing
# 250-DAY distribution). Lower-frequency series have no daily change, and
# scoring a monthly print against a daily distribution would be meaningless, so
# they are reported with a status rather than silently mixed in or dropped.
BOARD_FREQUENCY = "d"

# Status values a board row can carry.
STATUS_OK = "ok"
STATUS_INSUFFICIENT_HISTORY = "insufficient_history"
STATUS_NO_DATA = "no_data"
STATUS_ZERO_VARIANCE = "zero_variance"
STATUS_NOT_DAILY = "not_daily"
STATUS_TRANSFORM_ERROR = "transform_error"


@dataclass(frozen=True)
class BoardParams:
    """Every tunable the board uses, in one place, recorded on the result.

    Carried alongside the output so any figure on a board can be reproduced:
    stored raw inputs plus this parameter set plus the as_of reproduce it
    exactly (spec 0.4).
    """

    as_of: datetime
    zscore_window: int = 250
    min_observations: int = 60
    max_gap_days: int = 5
    vol_percentile_window: int = 750
    vol_extreme_low_pct: float = 10.0
    vol_extreme_high_pct: float = 90.0
    stale_warn_days: int = 3

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise DataIntegrityError("BoardParams.as_of must be timezone-aware")
        if self.min_observations > self.zscore_window:
            raise DataIntegrityError(
                f"min_observations ({self.min_observations}) exceeds zscore_window "
                f"({self.zscore_window}); no series could ever qualify"
            )


def _percentile_rank(sample: np.ndarray, value: float) -> float:
    """Percentage of `sample` at or below `value`. Plain and explicit rather than
    via scipy, so the definition is visible at the point of use."""
    if sample.size == 0:
        return float("nan")
    return 100.0 * float((sample <= value).sum()) / sample.size


def build_board(
    conn: Connection,
    params: BoardParams,
    series_ids: list[str] | None = None,
) -> pd.DataFrame:
    """One row per series, sorted by descending |z|.

    Columns: series_id, display_name, asset_class, value_date, change,
    change_unit, z, percentile, window_n, trailing_sd, vol_percentile,
    vol_flag, stale_days, status, as_of_basis, source_batch.
    """
    meta = get_metadata(conn)
    if series_ids is not None:
        meta = meta[meta["series_id"].isin(series_ids)]

    as_of_date = params.as_of.date()
    rows = [
        _board_row(conn, SeriesMeta(**_meta_kwargs(r)), params, as_of_date)
        for r in meta.to_dict("records")
    ]
    board = pd.DataFrame([r for r in rows if r is not None])
    if board.empty:
        log.warning("board is empty: no series produced a row")
        return board

    board["abs_z"] = board["z"].abs()
    board = board.sort_values(
        ["status", "abs_z"], ascending=[True, False], na_position="last"
    ).drop(columns="abs_z")
    return board.reset_index(drop=True)


def _meta_kwargs(row: dict) -> dict:
    """series_metadata row -> SeriesMeta kwargs, ignoring columns SeriesMeta
    does not model."""
    fields = {
        "series_id", "display_name", "source", "source_code", "asset_class",
        "category", "unit", "frequency", "default_transform", "revisable",
        "vintage_source", "snapshot_tz", "snapshot_local_time", "notes",
    }
    out = {k: v for k, v in row.items() if k in fields}
    out["revisable"] = bool(out["revisable"])
    for k in ("snapshot_tz", "snapshot_local_time", "notes"):
        if pd.isna(out.get(k)):
            out[k] = None
    return out


def _blank_row(m: SeriesMeta, status: str, **extra) -> dict:
    row = {
        "series_id": m.series_id,
        "display_name": m.display_name,
        "asset_class": m.asset_class,
        "value_date": None,
        "change": float("nan"),
        "change_unit": None,
        "z": float("nan"),
        "percentile": float("nan"),
        "window_n": 0,
        "trailing_sd": float("nan"),
        "vol_percentile": float("nan"),
        "vol_flag": "",
        "stale_days": None,
        "status": status,
        "as_of_basis": None,
        "source_batch": None,
    }
    row.update(extra)
    return row


def _lookback_start(m: SeriesMeta, params: BoardParams, as_of_date: date) -> date:
    per_change = CALENDAR_DAYS_PER_CHANGE.get(m.frequency, 1.75)
    span = params.zscore_window + params.vol_percentile_window
    return as_of_date - timedelta(days=int(span * per_change))


def _board_row(
    conn: Connection,
    m: SeriesMeta,
    params: BoardParams,
    as_of_date: date,
) -> dict | None:
    if m.frequency != BOARD_FREQUENCY:
        return _blank_row(m, STATUS_NOT_DAILY)

    start = _lookback_start(m, params, as_of_date)
    # warn_empty=False: this sweeps the whole universe, including series no
    # adapter fills yet. Their emptiness is reported in the board's own summary,
    # so a store-level warning here would fire 20-odd times a run and train the
    # reader to ignore warnings that do matter.
    obs = get(conn, m.series_id, start, as_of_date, as_of=params.as_of, warn_empty=False)
    if obs.empty:
        return _blank_row(m, STATUS_NO_DATA)

    # Belt and braces: get() already enforces the cutoff, but a frame assembled
    # any other way must not slip past (spec 8, no-lookahead assertion).
    assert_no_lookahead(obs, params.as_of)

    try:
        changes = apply_transform(
            obs, m.series_id, m.default_transform, m.unit, params.max_gap_days
        )
    except DataIntegrityError as e:
        # One series' bad data must not take the whole board down, but it must
        # not be swallowed either: the row says transform_error and the reason
        # is logged at ERROR.
        log.error("%s: transform failed: %s", m.series_id, e)
        return _blank_row(m, STATUS_TRANSFORM_ERROR)
    frame = changes.frame
    if frame.empty:
        return _blank_row(m, STATUS_NO_DATA, change_unit=changes.change_unit)

    latest = frame.iloc[-1]
    latest_date = pd.Timestamp(latest["value_date"]).date()
    stale_days = (as_of_date - latest_date).days

    # The trailing distribution: everything BEFORE the latest change, gap-clean,
    # most recent `zscore_window` of them.
    history = changes.usable()
    history = history[history["value_date"] < latest["value_date"]]
    window = history.tail(params.zscore_window)
    sample = window["change"].to_numpy(dtype=float)

    common = {
        "value_date": latest_date,
        "change": float(latest["change"]),
        "change_unit": changes.change_unit,
        "window_n": int(sample.size),
        "stale_days": stale_days,
        "as_of_basis": latest.get("as_of_basis"),
        "source_batch": latest.get("source_batch"),
    }

    if sample.size < params.min_observations:
        log.warning(
            "%s: %d prior changes available, %d required; z not reported",
            m.series_id, sample.size, params.min_observations,
        )
        return _blank_row(m, STATUS_INSUFFICIENT_HISTORY, **common)

    sd = float(sample.std(ddof=1))
    if sd == 0.0 or not np.isfinite(sd):
        # A constant series (a pegged rate, a stale feed) has no scale to
        # normalise by. Reporting z = inf or 0 would both be lies.
        return _blank_row(m, STATUS_ZERO_VARIANCE, trailing_sd=sd, **common)

    z = (float(latest["change"]) - float(sample.mean())) / sd
    percentile = _percentile_rank(sample, float(latest["change"]))

    vol_percentile, vol_flag = _vol_context(changes, latest["value_date"], params)

    if stale_days > params.stale_warn_days:
        log.warning(
            "%s: newest observation is %s, %d days before the board as_of",
            m.series_id, latest_date, stale_days,
        )

    return {
        **_blank_row(m, STATUS_OK, **common),
        "z": z,
        "percentile": percentile,
        "trailing_sd": sd,
        "vol_percentile": vol_percentile,
        "vol_flag": vol_flag,
    }


def _vol_context(
    changes, latest_date, params: BoardParams
) -> tuple[float, str]:
    """Where the current trailing volatility sits in its own history.

    Spec 3.1: flag when the trailing vol is itself at an extreme. Without this a
    2-sigma move in a becalmed market reads identically to a 2-sigma move in a
    violent one.
    """
    usable = changes.usable()
    usable = usable[usable["value_date"] <= latest_date]
    rolling = (
        usable["change"]
        .rolling(params.zscore_window, min_periods=params.min_observations)
        .std(ddof=1)
        .dropna()
    )
    if len(rolling) < 2:
        return float("nan"), ""

    history = rolling.tail(params.vol_percentile_window).to_numpy(dtype=float)
    current = float(rolling.iloc[-1])
    pct = _percentile_rank(history, current)

    if pct <= params.vol_extreme_low_pct:
        return pct, "compressed"
    if pct >= params.vol_extreme_high_pct:
        return pct, "elevated"
    return pct, ""
