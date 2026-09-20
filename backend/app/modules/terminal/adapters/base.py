"""The adapter contract.

    fetch(series_codes, start, end) -> list[Observation]
    list_available() -> list[str]

Adapters are constructed with the source_code -> series_id mapping they are
responsible for, so they can emit canonical ids without reaching into the store.
They never write; a single Loader persists what they yield.

Every adapter must raise UnknownSeriesError on a code it does not recognise or
that the source has retired, rather than returning an empty list (spec 7,
"Series discontinuation"). This is the single most important behaviour here:
a retired code that returns [] produces a silently truncated series, and the
gap only shows up later as an inexplicable z-score.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from ..errors import UnknownSeriesError
from ..logging import get_logger
from ..models import Observation


class Adapter(Protocol):
    name: str

    def fetch(
        self, series_codes: Sequence[str], start: date, end: date, source_batch: str
    ) -> list[Observation]: ...

    def list_available(self) -> list[str]: ...


class BaseAdapter:
    """Shared HTTP client, code resolution and as_of construction."""

    name: str = "base"

    def __init__(
        self,
        series_map: dict[str, str],
        *,
        timeout: float = 30.0,
        snapshot_tz: str = "America/New_York",
    ) -> None:
        """series_map maps this source's native codes to canonical series ids."""
        self.series_map = dict(series_map)
        self.snapshot_tz = ZoneInfo(snapshot_tz)
        self.log = get_logger(f"adapters.{self.name}")
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "xactx/0.1 (personal research)"},
        )

    def close(self) -> None:
        self.client.close()

    def list_available(self) -> list[str]:
        return sorted(self.series_map)

    def resolve(self, source_code: str) -> str:
        """Canonical series_id for a native code, or raise."""
        try:
            return self.series_map[source_code]
        except KeyError:
            raise UnknownSeriesError(
                f"{self.name}: {source_code!r} is not a code this adapter was given. "
                f"Known: {sorted(self.series_map)}"
            ) from None

    def at_local(self, d: date, local_time: time) -> datetime:
        """A tz-aware timestamp on date d at a local wall-clock time.

        Used to build as_of from a publication convention. Going through
        ZoneInfo rather than a fixed UTC offset keeps DST correct, which matters
        because a whole year of observations would otherwise be an hour wrong on
        one side of the transition.
        """
        return datetime.combine(d, local_time, tzinfo=self.snapshot_tz)
