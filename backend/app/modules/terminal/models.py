"""Normalized records exchanged between adapters, the loader and the store.

Adapters yield Observation instances and never touch the database (spec 1.2).
Validation lives in __post_init__ so a malformed value fails at the boundary
where it was produced, not three layers later inside a z-score.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime

from .errors import DataIntegrityError

ASSET_CLASSES = frozenset(
    {"rates", "credit", "fx", "equity", "commodity", "vol", "macro"}
)
CATEGORIES = frozenset(
    {"level", "spread", "implied", "positioning", "actual", "consensus"}
)
UNITS = frozenset({"pct", "bp", "index", "usd", "contracts", "ratio"})
FREQUENCIES = frozenset({"d", "w", "m", "q", "irregular"})
TRANSFORMS = frozenset({"diff", "log_return", "level", "pct_change"})

# How to read series_metadata.vintage_source: the series' DOMINANT as_of basis,
# for documentation. The authoritative, per-row answer is observations.as_of_basis
# (see AS_OF_BASES below), because one series routinely mixes bases.
#
# Deviation from spec 1.1, agreed before implementation: as_of means different
# things depending on whether the source publishes vintages, and conflating them
# would quietly weaken the point-in-time guarantee.
#   source_vintage -- the source told us when this value became known
#                     (ALFRED realtime_start). A true vintage.
#   derived_lag    -- the source publishes no revision history, so as_of is
#                     value_date plus that source's documented publication lag
#                     (see each adapter's PUBLICATION_* constants). Used for
#                     backfilled history, where ingest time would be useless:
#                     every historical row would read as "known today" and no
#                     as-of query before today could return anything.
#   ingest_time    -- as_of is when our ingester first saw the value. An upper
#                     bound on the true knowledge date, never earlier than it.
#                     Only honest for rows captured live, not backfilled.
VINTAGE_SOURCES = frozenset({"source_vintage", "derived_lag", "ingest_time"})

# How as_of was established for ONE observation. series_metadata.vintage_source
# documents a series' dominant basis; this records the truth row by row, because
# a single series routinely mixes them: FRED keeps real vintages only back to a
# series-specific archive start (DGS10: 2005-06-28, WTI: 2011-04-06, EURUSD:
# 2014-03-18) and nothing before it.
#   source_vintage -- as_of is the publisher's own vintage date. Authoritative.
#   derived_lag    -- as_of is value_date plus a documented publication
#                     convention for that source. Accurate to that convention.
#   archive_floor  -- the value was demonstrably known BY this time, but the
#                     true publication date is earlier and unrecoverable. Used
#                     for observations predating the vintage archive. Safe for
#                     no-lookahead (never too early) but it makes the row
#                     invisible to as-of queries before the floor, so it is
#                     marked rather than passed off as a vintage.
AS_OF_BASES = frozenset({"source_vintage", "derived_lag", "archive_floor"})


@dataclass(frozen=True, slots=True)
class Observation:
    """One value of one series, as known at one moment."""

    series_id: str
    value_date: date
    as_of: datetime
    value: float
    source_batch: str
    as_of_basis: str = "derived_lag"

    def __post_init__(self) -> None:
        # datetime and pandas.Timestamp are both subclasses of date, so an
        # isinstance check would pass one straight through and then compare
        # unequal to every real date downstream. Demand the exact type.
        if type(self.value_date) is not date:
            raise DataIntegrityError(
                f"{self.series_id}: value_date must be a datetime.date, got "
                f"{type(self.value_date).__name__}. A timestamp here means a "
                "frame boundary was crossed without converting."
            )
        if self.as_of_basis not in AS_OF_BASES:
            raise DataIntegrityError(
                f"{self.series_id} {self.value_date}: as_of_basis="
                f"{self.as_of_basis!r} not in {sorted(AS_OF_BASES)}"
            )
        if not self.series_id:
            raise DataIntegrityError("series_id must not be empty")
        if self.as_of.tzinfo is None:
            # A naive as_of cannot be compared across sources in different
            # timezones, which is exactly what point-in-time retrieval does.
            raise DataIntegrityError(
                f"{self.series_id} {self.value_date}: as_of must be timezone-aware"
            )
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise DataIntegrityError(
                f"{self.series_id} {self.value_date}: value must be numeric, "
                f"got {type(self.value).__name__}"
            )
        if math.isnan(self.value) or math.isinf(self.value):
            # Spec 0.5 / 7: a missing value is an absent row, never a stored NaN.
            # Storing it would let it survive into a correlation window unnoticed.
            raise DataIntegrityError(
                f"{self.series_id} {self.value_date}: value is {self.value}; "
                "missing observations must be omitted, not stored"
            )


@dataclass(frozen=True, slots=True)
class SeriesMeta:
    """A row of series_metadata (spec 1.1)."""

    series_id: str
    display_name: str
    source: str
    source_code: str
    asset_class: str
    category: str
    unit: str
    frequency: str
    default_transform: str
    revisable: bool
    vintage_source: str
    snapshot_tz: str | None = None
    snapshot_local_time: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        for field, allowed in (
            ("asset_class", ASSET_CLASSES),
            ("category", CATEGORIES),
            ("unit", UNITS),
            ("frequency", FREQUENCIES),
            ("default_transform", TRANSFORMS),
            ("vintage_source", VINTAGE_SOURCES),
        ):
            got = getattr(self, field)
            if got not in allowed:
                raise DataIntegrityError(
                    f"{self.series_id}: {field}={got!r} not in {sorted(allowed)}"
                )
