"""Turning stored levels into comparable changes (spec 3.1).

Each series carries a default_transform in its metadata; this module applies it.
Two rules matter more than the arithmetic:

  * Changes are computed between consecutive STORED observations, never across a
    forward-filled gap. Where the gap between two observations exceeds
    max_gap_days, the change is still produced but marked `wide_gap`, because a
    ten-day move is not a draw from the one-day distribution and letting it into
    a trailing window quietly fattens the tails (spec 7, holiday calendars).
  * The output carries its unit. A yield diff is basis points and an equity
    return is percent; a board that sorts them in one column without saying
    which is which invites exactly the wrong comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..errors import DataIntegrityError

# Yields, spreads and breakevens are stored in percent; a one-day move is
# conventionally quoted in basis points. 100bp = 1pp.
BP_PER_PERCENTAGE_POINT = 100.0

# Log returns and pct_change are reported in percent.
PCT_PER_UNIT = 100.0

TRANSFORMS = frozenset({"diff", "log_return", "pct_change", "level"})


def change_unit(transform: str, series_unit: str) -> str:
    """Unit label for a transformed change.

    Depends on BOTH the transform and the series' own unit: differencing a
    percent-quoted yield gives basis points, but differencing an index level
    (SKEW, VIX) gives index points. Labelling both "bp" would put two unlike
    numbers in one sorted column under the same heading, which is precisely the
    comparison the board exists to make safe.
    """
    if transform in ("log_return", "pct_change"):
        return "%"
    if transform == "level":
        return series_unit
    if series_unit == "pct":
        return "bp"
    if series_unit == "usd":
        return "usd"
    if series_unit == "ratio":
        return "ratio"
    return "pts"


@dataclass(frozen=True)
class ChangeSeries:
    """Changes for one series, with the provenance needed to reproduce them."""

    series_id: str
    transform: str
    change_unit: str
    #  columns: value_date, change, prev_date, gap_days, wide_gap, as_of
    frame: pd.DataFrame

    def usable(self) -> pd.DataFrame:
        """Changes eligible for a trailing distribution: gap-clean only."""
        return self.frame[~self.frame["wide_gap"]]


def apply_transform(
    observations: pd.DataFrame,
    series_id: str,
    transform: str,
    unit: str,
    max_gap_days: int,
) -> ChangeSeries:
    """Convert a frame from store.get() into period-over-period changes.

    `observations` must be the output of get(): value_date, value, as_of, ... It
    is assumed to be one row per value_date (get() already collapses vintages)
    and is sorted here rather than trusted to arrive sorted.
    """
    if transform not in TRANSFORMS:
        raise DataIntegrityError(
            f"{series_id}: unknown transform {transform!r}; "
            f"expected one of {sorted(TRANSFORMS)}"
        )

    df = observations.sort_values("value_date").reset_index(drop=True)
    if "value_date" not in df.columns or "value" not in df.columns:
        raise DataIntegrityError(
            f"{series_id}: expected value_date and value columns, got {list(df.columns)}"
        )

    value_dates = pd.to_datetime(df["value_date"])
    values = df["value"].astype(float)

    if transform == "level":
        change = values
        prev_date = value_dates
    else:
        prev = values.shift(1)
        prev_date = value_dates.shift(1)
        if transform == "diff":
            change = values - prev
            if unit == "pct":
                # Percent-quoted series: report the move in basis points.
                change = change * BP_PER_PERCENTAGE_POINT
        elif transform == "log_return":
            if (values <= 0).any():
                bad = df.loc[values <= 0, "value_date"].iloc[0]
                raise DataIntegrityError(
                    f"{series_id}: log_return needs positive values; "
                    f"{bad} has {values[values <= 0].iloc[0]}"
                )
            change = np.log(values / prev) * PCT_PER_UNIT
        else:  # pct_change
            change = (values / prev - 1.0) * PCT_PER_UNIT

    gap_days = (value_dates - prev_date).dt.days
    out = pd.DataFrame(
        {
            "value_date": df["value_date"],
            "change": change,
            "prev_date": prev_date.dt.date,
            "gap_days": gap_days,
            # NaN gap is the first row, which has no predecessor and no change.
            "wide_gap": (gap_days > max_gap_days).fillna(True),
        }
    )
    # Carry the provenance of the observation each change ENDS on, so a figure
    # on the board can still be traced to the vintage and ingest run behind it
    # (spec 0.4). Without this the chain breaks at the transform.
    for column in ("as_of", "as_of_basis", "source_batch"):
        out[column] = df[column] if column in df.columns else None
    # The first row has no prior observation, so it has no change. Dropped
    # rather than zero-filled.
    out = out[out["change"].notna()].reset_index(drop=True)

    return ChangeSeries(
        series_id=series_id,
        transform=transform,
        change_unit=change_unit(transform, unit),
        frame=out,
    )
