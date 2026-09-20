"""Cboe index history adapter.

Daily history CSVs, one per index, free and complete (VIX back to 1990). Source
codes are the index names: VIX, VIX9D, VIX3M, VIX6M, SKEW.

Two CSV shapes are in use and both are handled explicitly rather than by
guessing column positions:

    DATE,OPEN,HIGH,LOW,CLOSE   -- the VIX family
    DATE,SKEW                  -- SKEW

Only the close is taken: the daily board compares closes under one snapshot
convention (spec 7), and mixing a close with an intraday print would corrupt
that silently.

Like Treasury, Cboe publishes no revision history, so as_of is derived from the
value date plus a documented lag, and the metadata records vintage_source
"derived_lag".
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime, time

from ..errors import EmptyFetchError, UnknownSeriesError
from ..models import Observation
from .base import BaseAdapter

BASE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices"

# Cboe settles and posts these indices shortly after the 16:15 ET close of index
# options. 17:00 is a conservative placement: after the value genuinely exists,
# same trading day.
PUBLICATION_LOCAL_TIME = time(17, 0)

# Cboe writes dates as MM/DD/YYYY in these files.
DATE_FORMAT = "%m/%d/%Y"

KNOWN_INDICES = frozenset({"VIX", "VIX9D", "VIX3M", "VIX6M", "SKEW"})

# Which column carries the value we store, per index.
VALUE_COLUMN = {
    "VIX": "CLOSE",
    "VIX9D": "CLOSE",
    "VIX3M": "CLOSE",
    "VIX6M": "CLOSE",
    "SKEW": "SKEW",
}


class CboeAdapter(BaseAdapter):
    name = "cboe"

    def resolve(self, source_code: str) -> str:
        if source_code not in KNOWN_INDICES:
            raise UnknownSeriesError(
                f"cboe: {source_code!r} is not an index this adapter publishes. "
                f"Known: {sorted(KNOWN_INDICES)}"
            )
        return super().resolve(source_code)

    def fetch(
        self,
        series_codes: Sequence[str],
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        out: list[Observation] = []
        for code in series_codes:
            series_id = self.resolve(code)
            text = self._download(code)
            out.extend(self.parse(text, code, series_id, start, end, source_batch))
        return out

    def _download(self, code: str) -> str:
        r = self.client.get(f"{BASE_URL}/{code}_History.csv")
        if r.status_code == 404:
            raise UnknownSeriesError(
                f"cboe: no history file for {code!r} (404). The index may have "
                "been renamed or retired."
            )
        r.raise_for_status()
        return r.text

    def parse(
        self,
        text: str,
        code: str,
        series_id: str,
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        """Parse one history CSV. Separate from the download so contract tests
        can run against a saved fixture without touching the network."""
        reader = csv.DictReader(io.StringIO(text))
        column = VALUE_COLUMN[code]
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise EmptyFetchError(
                f"cboe: {code} CSV has no {column!r} column; got "
                f"{reader.fieldnames}. The file layout changed."
            )

        obs: list[Observation] = []
        skipped = 0
        for row in reader:
            raw_date = (row.get("DATE") or "").strip()
            raw_value = (row.get(column) or "").strip()
            if not raw_date:
                continue
            try:
                value_date = datetime.strptime(raw_date, DATE_FORMAT).date()  # noqa: DTZ007
            except ValueError:
                # Some Cboe files carry a header note or trailing row.
                skipped += 1
                continue
            if not (start <= value_date <= end):
                continue
            if not raw_value:
                skipped += 1
                continue
            obs.append(
                Observation(
                    series_id=series_id,
                    value_date=value_date,
                    as_of=self.at_local(value_date, PUBLICATION_LOCAL_TIME),
                    value=float(raw_value),
                    source_batch=source_batch,
                )
            )

        if not obs:
            raise EmptyFetchError(
                f"cboe: {code} ({series_id}) yielded no observations in "
                f"{start}..{end}; the file parsed but the range is empty."
            )
        if skipped:
            self.log.warning("%s: %d unparseable or blank rows skipped", code, skipped)
        self.log.info("%s (%s): %d observations", code, series_id, len(obs))
        return obs
