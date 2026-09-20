"""Point-in-time retrieval.

The primitive in spec 1.1:

    get(series_id, value_date_range, as_of=<timestamp>) -> series

With as_of omitted it returns latest-known. With as_of set it returns the world
as it looked at that moment: for each value_date, the newest vintage whose as_of
does not exceed the requested timestamp. Rows the world had not yet produced are
absent, not approximated.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from ..errors import DataIntegrityError, LookaheadError, UnknownSeriesError
from ..logging import get_logger

# T79: was `import duckdb`, with a DuckDB connection type in every signature below.
# `store.db` is still the only module that knows the engine (its docstring always said
# so); `Connection` is the alias it exports, so the next swap is one line there.
from .db import Connection

log = get_logger("store.query")

# Selects one row per value_date: the latest as_of that satisfies the cutoff.
# Ties on as_of would be ambiguous, but (series_id, value_date, as_of) is the
# primary key, so at most one row can hold any given as_of.
_GET_SQL = """
SELECT value_date, value, as_of, as_of_basis, source_batch
FROM (
    SELECT value_date, value, as_of, as_of_basis, source_batch,
           ROW_NUMBER() OVER (
               PARTITION BY value_date ORDER BY as_of DESC
           ) AS vintage_rank
    FROM observations
    WHERE series_id = ?
      AND value_date >= ?
      AND value_date <= ?
      -- T79: the casts are a genuine Postgres requirement, not a style choice. DuckDB infers
      -- a NULL parameter's type from context; Postgres refuses ("could not determine data type
      -- of parameter $4") because `? IS NULL` gives it nothing to work from. The semantics are
      -- unchanged: as_of omitted means latest-known, as_of set means the cutoff applies.
      AND (CAST(? AS timestamptz) IS NULL OR as_of <= CAST(? AS timestamptz))
) ranked
WHERE vintage_rank = 1
ORDER BY value_date
"""

_MIN_DATE = date(1900, 1, 1)
_MAX_DATE = date(2999, 12, 31)


def _require_registered(conn: Connection, series_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM series_metadata WHERE series_id = ?", [series_id]
    ).fetchone()
    if row is None:
        raise UnknownSeriesError(
            f"{series_id!r} is not registered in series_metadata. "
            "An unregistered id is a typo or a retired series, not an empty result."
        )


def _check_as_of(as_of: datetime | None) -> None:
    if as_of is not None and as_of.tzinfo is None:
        raise DataIntegrityError(
            "as_of must be timezone-aware; a naive timestamp cannot be compared "
            "against vintages recorded in other timezones"
        )


def get(
    conn: Connection,
    series_id: str,
    start: date | None = None,
    end: date | None = None,
    as_of: datetime | None = None,
    warn_empty: bool = True,
) -> pd.DataFrame:
    """Return columns [value_date, value, as_of, as_of_basis, source_batch].

    Raises UnknownSeriesError for an unregistered series_id. A registered series
    with no rows in range returns empty and logs a warning: absence of data is a
    legitimate state, absence of a series is not.
    """
    _require_registered(conn, series_id)
    _check_as_of(as_of)

    lo = start or _MIN_DATE
    hi = end or _MAX_DATE
    df = conn.execute(_GET_SQL, [series_id, lo, hi, as_of, as_of]).fetch_df()

    if df.empty and warn_empty:
        log.warning(
            "%s: no observations in [%s, %s]%s",
            series_id, lo, hi,
            f" as of {as_of.isoformat()}" if as_of else "",
        )
    return df


def vintages(
    conn: Connection, series_id: str, value_date: date
) -> pd.DataFrame:
    """Every stored vintage of one observation, oldest first.

    The traceability escape hatch (spec 0.4): given a derived number, this shows
    exactly which values were visible when.
    """
    _require_registered(conn, series_id)
    return conn.execute(
        """
        SELECT as_of, value, as_of_basis, source_batch
        FROM observations
        WHERE series_id = ? AND value_date = ?
        ORDER BY as_of
        """,
        [series_id, value_date],
    ).fetch_df()


def get_metadata(
    conn: Connection, series_id: str | None = None
) -> pd.DataFrame:
    if series_id is None:
        return conn.execute(
            "SELECT * FROM series_metadata ORDER BY series_id"
        ).fetch_df()
    _require_registered(conn, series_id)
    return conn.execute(
        "SELECT * FROM series_metadata WHERE series_id = ?", [series_id]
    ).fetch_df()


def assert_no_lookahead(df: pd.DataFrame, evaluation_time: datetime) -> None:
    """Spec 8: fail loudly if any input became known after the evaluation moment.

    Call this on the frame feeding any backtest or event study, in addition to
    passing as_of to get(). The two checks are independent: get() enforces the
    cutoff at read time, this catches a frame assembled some other way.
    """
    if evaluation_time.tzinfo is None:
        raise DataIntegrityError("evaluation_time must be timezone-aware")
    if df.empty or "as_of" not in df.columns:
        return
    as_of = pd.to_datetime(df["as_of"], utc=True)
    offenders = df[as_of > pd.Timestamp(evaluation_time).tz_convert("UTC")]
    if not offenders.empty:
        raise LookaheadError(
            f"{len(offenders)} observation(s) carry an as_of later than "
            f"{evaluation_time.isoformat()}; earliest offender: "
            f"{offenders.iloc[0].to_dict()}"
        )


def find_weekday_gaps(
    conn: Connection,
    series_id: str,
    start: date,
    end: date,
    as_of: datetime | None = None,
) -> list[date]:
    """Weekdays in range with no observation.

    This does NOT know market holiday calendars (spec 7: they differ by market),
    so it reports candidates for inspection, not errors. It exists so gaps are
    visible; nothing in this package fills them.
    """
    df = get(conn, series_id, start, end, as_of=as_of)
    present = set(pd.to_datetime(df["value_date"]).dt.date) if not df.empty else set()
    weekdays = pd.bdate_range(start, end).date
    return [d for d in weekdays if d not in present]
