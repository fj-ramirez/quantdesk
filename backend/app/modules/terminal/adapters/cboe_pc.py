"""Cboe exchange put/call ratios (T168, plans/sentiment).

Two sources that join without a gap, verified 2026-10-15:

    history CSV  2006-11-01 .. 2019-10-04
        https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/{file}.csv
        A disclaimer line, a product line, then DATE,CALL,PUT,TOTAL,P/C Ratio.
    daily JSON   2019-10-07 ..
        https://cdn.cboe.com/data/us/options/market_statistics/daily/{YYYY-MM-DD}_daily_options
        `ratios[]` of {name, value}, values as strings.

Source codes are the ratio kinds: TOTAL, EQUITY, INDEX.

The daily JSON is one request per day, so a nightly ingest from the backfill start would make
~1,800 requests a night for data that does not change. Each fetched day is therefore cached on
disk under `DATA_DIR/terminal/cboe_pc/`; only days not yet cached are requested. A day with no
file -- a weekend, a holiday, today before publication -- answers **403 AccessDenied** (S3), not
404. A 403 on a day older than `MISSING_SETTLE_DAYS` is cached as missing so a holiday is asked
once; a recent one is retried on the next run.

as_of: S3's Last-Modified on recent files falls 21:20-22:50 ET the same evening, but
2026-06-15's file was rewritten at 13:58 ET the next day. The convention is therefore the next
calendar day at 06:00 ET (`derived_lag`): late enough for the ordinary case, and a rewrite
like June's is the documented exception, not the rule.
"""

from __future__ import annotations

import csv
import io
import json
import time as _time
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

from ..errors import EmptyFetchError, UnknownSeriesError
from ..models import Observation
from .base import BaseAdapter

CSV_URL = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/{file}.csv"
JSON_URL = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{day}_daily_options"

#: Last day the history CSVs carry, and first day of the daily JSON.
CSV_LAST_DAY = date(2019, 10, 4)
JSON_FIRST_DAY = date(2019, 10, 7)

#: Source code -> (history CSV file, name of the ratio in the daily JSON).
KINDS = {
    "TOTAL": ("totalpc", "TOTAL PUT/CALL RATIO"),
    "EQUITY": ("equitypc", "EQUITY PUT/CALL RATIO"),
    "INDEX": ("indexpc", "INDEX PUT/CALL RATIO"),
}

#: A day published by the next morning; the conservative placement documented above.
PUBLICATION_LAG = timedelta(days=1)
PUBLICATION_LOCAL_TIME = time(6, 0)

#: A 403 older than this is a day with no file (a holiday), not one still to come.
MISSING_SETTLE_DAYS = 7

#: Pause between uncached daily requests: the backfill is ~1,800 of them, once.
REQUEST_PAUSE_SECONDS = 0.05

CSV_DATE_FORMAT = "%m/%d/%Y"
_MISSING = {"missing": True}


class CboePutCallAdapter(BaseAdapter):
    name = "cboe_pc"

    def __init__(self, series_map: dict[str, str], *, cache_dir: Path | None = None,
                 **kw) -> None:
        super().__init__(series_map, **kw)
        self.cache_dir = cache_dir
        # The ingest loop calls fetch once per series; the three share one set of days.
        self._run_cache: dict[date, dict | None] = {}

    def resolve(self, source_code: str) -> str:
        if source_code not in KINDS:
            raise UnknownSeriesError(
                f"cboe_pc: {source_code!r} is not a ratio this adapter publishes. "
                f"Known: {sorted(KINDS)}"
            )
        return super().resolve(source_code)

    def fetch(
        self,
        series_codes: Sequence[str],
        start: date,
        end: date,
        source_batch: str,
    ) -> list[Observation]:
        days = self._daily(max(start, JSON_FIRST_DAY), end) if end >= JSON_FIRST_DAY else {}
        out: list[Observation] = []
        for code in series_codes:
            series_id = self.resolve(code)
            obs: list[Observation] = []
            if start <= CSV_LAST_DAY:
                text = self._get_text(CSV_URL.format(file=KINDS[code][0]))
                obs += self.parse_csv(text, series_id, start, min(end, CSV_LAST_DAY),
                                      source_batch)
            obs += self.parse_daily(days, code, series_id, source_batch)
            if not obs:
                raise EmptyFetchError(
                    f"cboe_pc: {code} ({series_id}) yielded no observations in {start}..{end}"
                )
            self.log.info("%s (%s): %d observations", code, series_id, len(obs))
            out += obs
        return out

    # --- history CSV ---------------------------------------------------------------------

    def _get_text(self, url: str) -> str:
        r = self.client.get(url)
        r.raise_for_status()
        return r.text

    def parse_csv(
        self, text: str, series_id: str, start: date, end: date, source_batch: str
    ) -> list[Observation]:
        """Parse a history CSV. The header row is found by content, not position: the
        disclaimer and product lines above it differ between the three files."""
        lines = text.splitlines()
        try:
            header = next(i for i, ln in enumerate(lines) if ln.strip().upper().startswith("DATE,"))
        except StopIteration:
            raise EmptyFetchError(
                f"cboe_pc: {series_id} history CSV has no DATE header; the layout changed"
            ) from None
        reader = csv.reader(io.StringIO("\n".join(lines[header + 1:])))
        obs: list[Observation] = []
        for row in reader:
            if len(row) < 5:
                continue
            try:
                value_date = datetime.strptime(row[0].strip(), CSV_DATE_FORMAT).date()  # noqa: DTZ007
                value = float(row[4])
            except ValueError:
                continue
            if start <= value_date <= end:
                obs.append(self._obs(series_id, value_date, value, source_batch))
        return obs

    # --- daily JSON ----------------------------------------------------------------------

    def _daily(self, start: date, end: date) -> dict[date, dict]:
        """Every weekday's payload in range, from the cache or the network. Missing days are
        absent from the result."""
        today = datetime.now(self.snapshot_tz).date()
        out: dict[date, dict] = {}
        missing: list[date] = []
        d = start
        while d <= end:
            if d.weekday() < 5:
                payload = self._day(d, today)
                if payload is None or payload.get("missing"):
                    missing.append(d)
                else:
                    out[d] = payload
            d += timedelta(days=1)
        if missing:
            self.log.info("cboe_pc: %d weekdays without a file (holidays or not yet "
                          "published), latest %s", len(missing), missing[-1])
        return out

    def _cache_path(self, d: date) -> Path | None:
        return None if self.cache_dir is None else self.cache_dir / f"{d.isoformat()}.json"

    def _write_cache(self, path: Path, payload: dict) -> None:
        # Created on first write, so constructing the adapter touches no disk.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _day(self, d: date, today: date) -> dict | None:
        if d not in self._run_cache:
            self._run_cache[d] = self._load_day(d, today)
        return self._run_cache[d]

    def _load_day(self, d: date, today: date) -> dict | None:
        path = self._cache_path(d)
        if path is not None and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        _time.sleep(REQUEST_PAUSE_SECONDS)
        r = self.client.get(JSON_URL.format(day=d.isoformat()))
        if r.status_code in (403, 404):
            if (today - d).days > MISSING_SETTLE_DAYS and path is not None:
                self._write_cache(path, _MISSING)
            return None
        r.raise_for_status()
        payload = r.json()
        # Not cached until it is old enough to be settled: June 2026 showed a file can be
        # rewritten the next day, and a cache that froze the first version would hide that.
        if path is not None and (today - d).days > MISSING_SETTLE_DAYS:
            self._write_cache(path, payload)
        return payload

    def parse_daily(
        self, days: dict[date, dict], code: str, series_id: str, source_batch: str
    ) -> list[Observation]:
        name = KINDS[code][1]
        obs: list[Observation] = []
        for d, payload in sorted(days.items()):
            ratio = next((r for r in payload.get("ratios", []) if r.get("name") == name), None)
            if ratio is None:
                raise EmptyFetchError(
                    f"cboe_pc: {d} payload has no {name!r}; got "
                    f"{[r.get('name') for r in payload.get('ratios', [])]}. The layout changed."
                )
            try:
                value = float(ratio["value"])
            except (TypeError, ValueError):
                self.log.warning("cboe_pc: %s %s unparseable value %r", d, code, ratio["value"])
                continue
            obs.append(self._obs(series_id, d, value, source_batch))
        return obs

    def _obs(self, series_id: str, value_date: date, value: float, batch: str) -> Observation:
        return Observation(
            series_id=series_id,
            value_date=value_date,
            as_of=self.at_local(value_date + PUBLICATION_LAG, PUBLICATION_LOCAL_TIME),
            value=value,
            source_batch=batch,
        )
