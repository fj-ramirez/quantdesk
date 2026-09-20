"""Building the aligned change matrix that factor analysis runs on.

Spec 7 is explicit about the rule here: "Prefer explicit missing values and drop
incomplete rows from the PCA window." Different markets keep different holiday
calendars, and filling one series across a day it did not trade invents a zero
change that drags every correlation toward the others.

So the panel is an INNER join across series on value_date. A date where any
included series is missing is dropped whole, and the count of dropped dates is
reported rather than absorbed. That makes the panel narrower than a filled one
would be, which is the point: the rows that survive are rows where every series
genuinely traded.

Wide-gap changes are excluded for the same reason they are kept out of the
board's trailing distribution: a ten-day move is not a draw from the one-day
distribution.

THE FRESHNESS COST OF ALIGNMENT
-------------------------------
An inner join ends where its slowest member ends. FRED publishes the FX series
on the weekly H.10 schedule, so including them caps the whole panel roughly a
week behind everything else: with data through 11 September, the newest complete
row is the 4th. The alternative is dropping FX and losing the dollar factor that
spec 3.2 expects to find.

Neither choice is free, so neither is made silently. The panel reports which
series capped it and by how many days, and drop_stale_days lets a caller trade
the laggards away deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from ..errors import EmptyFetchError
from ..logging import get_logger
from ..models import SeriesMeta

# T79: was `import duckdb`, with a DuckDB connection type in every signature below.
# `store.db` is still the only module that knows the engine (its docstring always said
# so); `Connection` is the alias it exports, so the next swap is one line there.
from ..store.db import Connection
from ..store.query import assert_no_lookahead, get, get_metadata
from .transforms import apply_transform

log = get_logger("analytics.panel")

# Calendar days pulled per observation wanted. Weekends, holidays and
# publication gaps all cost calendar days.
CALENDAR_DAYS_PER_CHANGE = 1.75

# Sources whose series are candidates for the factor panel. The panel is a
# daily cross-asset matrix; positioning is weekly and the policy path has no
# history, so neither belongs in it.
PANEL_FREQUENCY = "d"

# Series excluded from the panel by id prefix, with the reason.
EXCLUDED_PREFIXES = {
    # The Treasury curve duplicates the FRED curve almost exactly (5927 shared
    # dates, zero disagreements beyond a rounding tick). Including both would
    # hand the rates factor a second copy of itself and inflate its variance
    # share for no added information.
    "ust_cc.": "duplicates the FRED curve; kept for cross-checking, not for PCA",
    # Contract-dated futures settlements: each exists for a few months and the
    # set changes monthly, which is not a stable panel.
    "ff.zq.": "contract-dated, short-lived, and rolls monthly",
    # No history yet and none obtainable retrospectively.
    "policy.ff.": "accumulates forward only; cannot fill a trailing window",
}


@dataclass
class ChangePanel:
    """An aligned matrix of daily changes, plus what it took to build it."""

    frame: pd.DataFrame                      # index value_date, columns series_id
    series: list[str] = field(default_factory=list)
    dropped_dates: int = 0
    excluded: dict[str, str] = field(default_factory=dict)
    as_of: datetime | None = None
    # Days between the panel's last complete row and the freshest observation
    # any included series had. Zero means nothing held the panel back.
    stale_days: int = 0
    # The series whose last observation capped the panel, if any did.
    limiting_series: list[str] = field(default_factory=list)

    def standardized(self) -> pd.DataFrame:
        """Each column centred and scaled by its own standard deviation.

        PCA on raw changes would be dominated by whichever series has the
        largest units -- an index in points would swamp a yield in basis
        points. Standardising makes the decomposition about co-movement rather
        than about scale.
        """
        sd = self.frame.std(ddof=1)
        zero = sd[sd == 0].index.tolist()
        if zero:
            # A constant column has no co-movement to explain and would divide
            # by zero. Reported rather than silently dropped.
            log.warning(
                "panel: %d series had zero variance in the window and were "
                "dropped: %s", len(zero), zero,
            )
        usable = self.frame.drop(columns=zero)
        return (usable - usable.mean()) / usable.std(ddof=1)


def eligible_series(meta: pd.DataFrame) -> tuple[list[SeriesMeta], dict[str, str]]:
    """Daily series that belong in a cross-asset panel, and why the rest do not."""
    from .board import _meta_kwargs

    keep: list[SeriesMeta] = []
    excluded: dict[str, str] = {}
    for row in meta.to_dict("records"):
        m = SeriesMeta(**_meta_kwargs(row))
        if m.frequency != PANEL_FREQUENCY:
            excluded[m.series_id] = f"frequency {m.frequency}, not daily"
            continue
        reason = next(
            (why for pre, why in EXCLUDED_PREFIXES.items()
             if m.series_id.startswith(pre)),
            None,
        )
        if reason:
            excluded[m.series_id] = reason
            continue
        keep.append(m)
    return keep, excluded


def build_panel(
    conn: Connection,
    as_of: datetime,
    window: int,
    max_gap_days: int = 5,
    min_series: int = 10,
    series_ids: list[str] | None = None,
    drop_stale_days: int | None = None,
) -> ChangePanel:
    """The trailing `window` rows of aligned daily changes, as of `as_of`.

    Every series is read through get() with the explicit as_of, so the panel is
    the panel that could have been built on that day.

    drop_stale_days, when set, excludes any series whose newest observation is
    more than that many days behind the freshest series in the panel. Each
    exclusion is reported. Use it to trade a laggard away for freshness on
    purpose; leaving it unset keeps every series and accepts the cap.
    """
    meta = get_metadata(conn)
    if series_ids is not None:
        meta = meta[meta["series_id"].isin(series_ids)]
    candidates, excluded = eligible_series(meta)

    as_of_date = as_of.date()
    start = as_of_date - timedelta(days=int(window * 2 * CALENDAR_DAYS_PER_CHANGE))

    columns: dict[str, pd.Series] = {}
    for m in candidates:
        obs = get(conn, m.series_id, start, as_of_date, as_of=as_of, warn_empty=False)
        if obs.empty:
            excluded[m.series_id] = "no observations in the window"
            continue
        assert_no_lookahead(obs, as_of)
        try:
            changes = apply_transform(
                obs, m.series_id, m.default_transform, m.unit, max_gap_days
            )
        except Exception as e:  # noqa: BLE001 - reported per series, never fatal
            excluded[m.series_id] = f"transform failed: {e}"
            continue
        usable = changes.usable()
        if usable.empty:
            excluded[m.series_id] = "no gap-clean changes in the window"
            continue
        s = pd.Series(
            usable["change"].to_numpy(),
            index=pd.to_datetime(usable["value_date"]).dt.date,
            name=m.series_id,
        )
        columns[m.series_id] = s[~s.index.duplicated(keep="last")]

    if len(columns) < min_series:
        raise EmptyFetchError(
            f"panel: only {len(columns)} series produced changes, need "
            f"{min_series}. A factor decomposition over a handful of series "
            "describes those series, not the cross-asset system."
        )

    # Freshness triage before alignment, since alignment is what turns one
    # laggard into a week off the whole panel.
    last_seen = {sid: max(col.index) for sid, col in columns.items()}
    freshest = max(last_seen.values())
    behind = {
        sid: (freshest - last).days
        for sid, last in last_seen.items()
        if (freshest - last).days > 0
    }
    if drop_stale_days is not None:
        for sid, lag in sorted(behind.items(), key=lambda kv: -kv[1]):
            if lag > drop_stale_days:
                excluded[sid] = (
                    f"last observation {lag}d behind the freshest series; "
                    f"dropped at drop_stale_days={drop_stale_days}"
                )
                columns.pop(sid, None)
        if len(columns) < min_series:
            raise EmptyFetchError(
                f"panel: dropping series staler than {drop_stale_days}d left "
                f"only {len(columns)}, below the {min_series} minimum."
            )

    wide = pd.DataFrame(columns)
    before = len(wide)
    # Inner join: a date where ANY series is missing leaves the panel whole.
    aligned = wide.dropna(how="any")
    dropped = before - len(aligned)

    aligned = aligned.tail(window)
    if len(aligned) < window:
        log.warning(
            "panel: %d complete rows available, %d requested. Every date where "
            "any one series was missing was dropped; nothing was filled.",
            len(aligned), window,
        )
    if aligned.empty:
        raise EmptyFetchError(
            "panel: no date has an observation for every series. Alignment is "
            "an inner join by design (spec 7); check which series are stale."
        )

    panel_end = max(aligned.index)
    stale_days = (freshest - panel_end).days
    limiting = sorted(
        sid for sid, last in last_seen.items()
        if sid in aligned.columns and last <= panel_end and stale_days > 0
    )
    if stale_days > 0:
        log.warning(
            "panel ends %s, %d day(s) behind the freshest data (%s). Alignment "
            "ends where its slowest member ends; the laggards are %s. Pass "
            "drop_stale_days to trade them for freshness.",
            panel_end, stale_days, freshest, limiting[:5],
        )

    log.info(
        "panel: %d series x %d complete rows (%d dates dropped for missing "
        "data, %d series excluded)",
        len(aligned.columns), len(aligned), dropped, len(excluded),
    )
    return ChangePanel(
        frame=aligned,
        series=list(aligned.columns),
        dropped_dates=dropped,
        excluded=excluded,
        as_of=as_of,
        stale_days=stale_days,
        limiting_series=limiting,
    )


def panel_dates(frame: pd.DataFrame) -> list[date]:
    return list(frame.index)
