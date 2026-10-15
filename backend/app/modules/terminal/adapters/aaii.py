"""AAII Sentiment Survey (T169, plans/sentiment).

One legacy `.xls` carries the whole weekly history, from 1987-06-26 (verified 2026-10-15):
a letterhead, a `Reported Date` / `Date, Bullish, Neutral, Bearish, Total, ...` header pair,
then one row per week with the three shares as fractions (0.4025 = 40.25 %), and trailing
yearly count rows (`Count '25`) that are dropped by requiring a real date.

Source codes are the three answers: BULLISH, NEUTRAL, BEARISH. Values are stored in percent.

as_of: the row's date is the Thursday the survey is reported; the file's Last-Modified on
2026-10-08 was 10:40 ET that morning. The convention is the reported date at 12:00 ET
(`derived_lag`). AAII publishes no revision history; a past week changing in the file would
make the loader raise rather than silently keep one of the two values (invariant 10).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import date, time

import pandas as pd

from ..errors import EmptyFetchError, UnknownSeriesError
from ..models import Observation
from .base import BaseAdapter

URL = "https://www.aaii.com/files/surveys/sentiment.xls"

#: Source code -> column index in the sheet (0 is the date).
COLUMNS = {"BULLISH": 1, "NEUTRAL": 2, "BEARISH": 3}

PUBLICATION_LOCAL_TIME = time(12, 0)


class AaiiAdapter(BaseAdapter):
    name = "aaii"

    def __init__(self, series_map: dict[str, str], **kw) -> None:
        super().__init__(series_map, **kw)
        # AAII serves the file to a browser user agent; the default one is refused.
        self.client.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
        )
        self._frame: pd.DataFrame | None = None

    def resolve(self, source_code: str) -> str:
        if source_code not in COLUMNS:
            raise UnknownSeriesError(
                f"aaii: {source_code!r} is not a survey answer. Known: {sorted(COLUMNS)}"
            )
        return super().resolve(source_code)

    def fetch(
        self,
        series_codes: Sequence[str],
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        if self._frame is None:
            r = self.client.get(URL)
            r.raise_for_status()
            self._frame = self.read_sheet(r.content)
        out: list[Observation] = []
        for code in series_codes:
            out += self.parse(self._frame, code, self.resolve(code), start, end, source_batch)
        return out

    @staticmethod
    def read_sheet(content: bytes) -> pd.DataFrame:
        """The weekly rows: a `date` column plus the three answers as fractions."""
        raw = pd.read_excel(io.BytesIO(content), header=None, engine="xlrd")
        dates = pd.to_datetime(raw[0], errors="coerce", format="mixed")
        rows = raw[dates.notna()].copy()
        if rows.empty:
            raise EmptyFetchError("aaii: the sheet has no dated rows; the layout changed")
        frame = pd.DataFrame({"date": dates[dates.notna()].dt.date})
        for code, col in COLUMNS.items():
            frame[code] = pd.to_numeric(rows[col], errors="coerce")
        return frame.reset_index(drop=True)

    def parse(
        self,
        frame: pd.DataFrame,
        code: str,
        series_id: str,
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        obs: list[Observation] = []
        for value_date, value in zip(frame["date"], frame[code], strict=True):
            if not (start <= value_date <= end) or pd.isna(value):
                continue
            obs.append(
                Observation(
                    series_id=series_id,
                    value_date=value_date,
                    as_of=self.at_local(value_date, PUBLICATION_LOCAL_TIME),
                    # Rounded so float noise in the sheet cannot read as a revision.
                    value=round(float(value) * 100.0, 4),
                    source_batch=source_batch,
                )
            )
        if not obs:
            raise EmptyFetchError(
                f"aaii: {code} ({series_id}) yielded no observations in {start}..{end}"
            )
        self.log.info("%s (%s): %d weekly observations", code, series_id, len(obs))
        return obs
