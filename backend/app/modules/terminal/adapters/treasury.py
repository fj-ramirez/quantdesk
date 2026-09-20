"""US Treasury daily par yield curve adapter.

One XML document per calendar year. Source codes here are Treasury's own field
names (BC_10YEAR, BC_2YEAR, ...), so the series map reads the same way as it
does for FRED.

Treasury publishes no revision history, so as_of is derived: value_date at the
documented publication time (see PUBLICATION_LOCAL_TIME). The metadata for these
series records vintage_source "derived_lag" so nothing downstream mistakes that
for a real vintage.

Spec 1.2 lists this source as a cross-check on FRED, which occasionally
disagrees on holidays. Keeping both under distinct series ids is deliberate:
they are compared, never merged.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import date, time

from ..errors import EmptyFetchError, UnknownSeriesError
from ..models import Observation
from .base import BaseAdapter

BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml"
)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
}

# Treasury posts the curve at approximately 18:00 ET on the value date. as_of is
# placed here rather than at the 16:00 snapshot because the figures genuinely
# are not available at the equity close, and claiming otherwise would let a
# same-day backtest read a number it could not have had.
PUBLICATION_LOCAL_TIME = time(18, 0)

# Every tenor the feed publishes. Used to validate source codes offline.
KNOWN_FIELDS = frozenset({
    "BC_1MONTH", "BC_2MONTH", "BC_3MONTH", "BC_4MONTH", "BC_6MONTH",
    "BC_1YEAR", "BC_2YEAR", "BC_3YEAR", "BC_5YEAR", "BC_7YEAR",
    "BC_10YEAR", "BC_20YEAR", "BC_30YEAR",
})


class TreasuryAdapter(BaseAdapter):
    name = "treasury"

    def resolve(self, source_code: str) -> str:
        if source_code not in KNOWN_FIELDS:
            raise UnknownSeriesError(
                f"treasury: {source_code!r} is not a field this feed publishes. "
                f"Known: {sorted(KNOWN_FIELDS)}"
            )
        return super().resolve(source_code)

    def fetch(
        self,
        series_codes: Sequence[str],
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        mapping = {code: self.resolve(code) for code in series_codes}

        obs: list[Observation] = []
        for year in range(start.year, end.year + 1):
            obs.extend(self._fetch_year(year, mapping, start, end, source_batch))

        if not obs:
            raise EmptyFetchError(
                f"treasury: no observations for {sorted(series_codes)} in {start}..{end}"
            )
        return obs

    def _fetch_year(
        self,
        year: int,
        mapping: dict[str, str],
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        r = self.client.get(
            BASE_URL,
            params={
                "data": "daily_treasury_yield_curve",
                "field_tdr_date_value": str(year),
            },
        )
        r.raise_for_status()
        return self.parse_year(r.content, mapping, start, end, source_batch, year)

    def parse_year(
        self,
        xml_bytes: bytes,
        mapping: dict[str, str],
        start: date,
        end: date,
        source_batch: str,
        year: int | None = None,
    ) -> list[Observation]:
        """Parse one year document. Separate from the fetch so contract tests can
        run against a saved fixture without touching the network."""
        root = ET.fromstring(xml_bytes)

        obs: list[Observation] = []
        missing: dict[str, int] = {}
        rows = 0
        for props in root.iterfind(".//m:properties", NS):
            node = props.find("d:NEW_DATE", NS)
            if node is None or not node.text:
                continue
            value_date = date.fromisoformat(node.text[:10])
            if not (start <= value_date <= end):
                continue
            rows += 1
            as_of = self.at_local(value_date, PUBLICATION_LOCAL_TIME)
            for code, series_id in mapping.items():
                field = props.find(f"d:{code}", NS)
                # A tenor can be absent for a whole era (the 20y and 30y have
                # both had gaps). Absent means absent; it is never carried over.
                if field is None or field.text is None or not field.text.strip():
                    missing[code] = missing.get(code, 0) + 1
                    continue
                obs.append(
                    Observation(
                        series_id=series_id,
                        value_date=value_date,
                        as_of=as_of,
                        value=float(field.text),
                        source_batch=source_batch,
                    )
                )
        for code, n in sorted(missing.items()):
            self.log.warning(
                "%s: %d of %d rows in %s had no value and were skipped, not filled",
                code, n, rows, year,
            )
        self.log.info("%s: %d curve dates, %d observations", year, rows, len(obs))
        return obs
