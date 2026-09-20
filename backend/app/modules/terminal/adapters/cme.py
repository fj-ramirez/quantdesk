"""CME settlement adapter: 30-day Fed Funds futures (ZQ), from a LOCAL FILE.

This adapter does not fetch from cmegroup.com, and that is deliberate.

WHAT HAPPENED
-------------
The obvious route is the settlements endpoint the CME website calls
(/CmeWS/mvc/Settlements/Futures/Settlements/305/FUT). Probing it on 2026-09-12
established two things:

  1. It serves only about five business days of history. Older trade dates
     return HTTP 200 with an empty list, so the implied policy path could never
     have been backfilled from it anyway.
  2. Automated use of it is prohibited. After a handful of requests the response
     became HTTP 403: "This IP address is blocked due to suspected web scraping
     activity ... Use of scripts, software, spiders, robots, avatars, agents,
     tools or other scraping mechanisms is strictly prohibited by CME Group's
     website Data Terms of Use."

So spec 1.2's premise -- CME settlements as a free adapter -- does not hold.
Scraping it anyway would mean evading an access control and breaching the terms
the data is published under, and a personal-use tool is not a reason to do that.

WHAT THIS DOES INSTEAD
----------------------
It parses a settlement file the user has obtained through a route they are
entitled to use, and stores it exactly like any other source. Supported inputs:

  * CSV with columns month,settle (and optionally openInterest)
  * The JSON shape CME's own tools emit: {"settlements": [{"month": "OCT 26",
    "settle": "96.1400", ...}]}

Legitimate sources for that file include a CME DataMine subscription (which is
the only route with real history), a market-data vendor licence, or a manual
download the user performs themselves.

The consequence for spec 2.1 is unchanged and worth stating plainly: there is no
free archive of the ZQ strip, so the implied policy path accumulates forward
from whenever settlements start being supplied. It cannot be backfilled, and the
FedWatch comparison spec 2.1 asks for cannot be built.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, time
from pathlib import Path

from ..errors import DataIntegrityError, EmptyFetchError, UnknownSeriesError
from ..logging import get_logger
from ..models import Observation, SeriesMeta

log = get_logger("adapters.cme")

# Products this adapter can interpret. ZQ settles on the arithmetic average of
# the daily effective fed funds rate, which is what policy.solve_path assumes.
KNOWN_ROOTS = frozenset({"ZQ"})

# Settlements are final in the evening of the trade date.
PUBLICATION_LOCAL_TIME = time(18, 0)

# CME quotes ZQ as 100 minus the average daily effective rate.
PRICE_BASE = 100.0

# Below this open interest a settlement is a mark, not a market. Far-dated ZQ
# contracts trade thinly, and the path chains through every month in between,
# so a thin leg quietly carries the ones after it.
THIN_OPEN_INTEREST = 5000

MONTH_ABBR = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MONTH_RE = re.compile(r"^([A-Z]{3})\s*[- ]?\s*(\d{2}|\d{4})$")
_ISO_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def parse_contract_month(text: str) -> date | None:
    """'OCT 26', 'OCT 2026' or '2026-10' -> date(2026, 10, 1).

    The first of the month is the contract's canonical identity, not a trading
    date.
    """
    raw = str(text).strip().upper()
    iso = _ISO_MONTH_RE.match(raw)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), 1)
    m = _MONTH_RE.match(raw)
    if not m or m.group(1) not in MONTH_ABBR:
        return None
    yy = int(m.group(2))
    year = yy if yy > 1000 else 2000 + yy
    return date(year, MONTH_ABBR[m.group(1)], 1)


def contract_series_id(root: str, contract_month: date) -> str:
    return f"ff.{root.lower()}.{contract_month:%Y-%m}"


def contract_meta(root: str, contract_month: date) -> SeriesMeta:
    """Metadata for a contract series, generated on first sight.

    Registered dynamically rather than listed in universe.py: the set grows by
    twelve a year forever, and a hand-maintained list would be missing exactly
    the contract that just appeared.

    Contract-dated (ff.zq.2026-10), not nth-nearby: a series whose meaning rolls
    every month would need a documented roll convention and would be useless as
    an audit trail for the path it feeds (spec 7).
    """
    return SeriesMeta(
        series_id=contract_series_id(root, contract_month),
        display_name=f"{root} settlement, {contract_month:%b %Y}",
        source="cme",
        source_code=f"{root}:{contract_month:%Y-%m}",
        asset_class="rates",
        category="implied",
        unit="index",
        frequency="d",
        default_transform="diff",
        revisable=False,
        vintage_source="derived_lag",
        snapshot_tz="America/New_York",
        snapshot_local_time="18:00",
        notes=(
            f"Daily settlement of the {contract_month:%B %Y} {root} contract, "
            "quoted as 100 minus the average daily effective rate. Raw input to "
            "the implied policy path (spec 2.1). Loaded from a user-supplied "
            "settlement file; see adapters/cme.py for why this is not fetched."
        ),
    )


class CmeFileAdapter:
    """Reads settlements from a local file. Deliberately has no HTTP client."""

    name = "cme"

    def __init__(self, snapshot_tz: str = "America/New_York") -> None:
        from zoneinfo import ZoneInfo

        self.snapshot_tz = ZoneInfo(snapshot_tz)
        self.log = log
        self.discovered: dict[str, SeriesMeta] = {}

    def list_available(self) -> list[str]:
        return sorted(KNOWN_ROOTS)

    def at_local(self, d: date, local_time: time):
        from datetime import datetime

        return datetime.combine(d, local_time, tzinfo=self.snapshot_tz)

    def fetch_file(
        self, path: Path, root: str, trade_date: date, source_batch: str
    ) -> list[Observation]:
        if root not in KNOWN_ROOTS:
            raise UnknownSeriesError(
                f"cme: {root!r} is not a product this adapter interprets. "
                f"Known: {sorted(KNOWN_ROOTS)}. The monthly-average "
                "decomposition in policy.py is specific to ZQ."
            )
        if not path.exists():
            raise EmptyFetchError(
                f"cme: no settlement file at {path}. This adapter does not "
                "fetch from cmegroup.com -- see the module docstring. Supply a "
                "file obtained through DataMine, a vendor licence, or a manual "
                "download."
            )
        text = path.read_text(encoding="utf-8")
        rows = self.parse_file(text)
        return self.to_observations(rows, root, trade_date, source_batch)

    def parse_file(self, text: str) -> list[dict]:
        """Accept either the CME JSON shape or a two-column CSV."""
        stripped = text.lstrip()
        if stripped.startswith("{"):
            payload = json.loads(text)
            rows = payload.get("settlements")
            if rows is None:
                raise DataIntegrityError(
                    "cme: JSON file has no 'settlements' key; got "
                    f"{sorted(payload)[:6]}"
                )
            return rows
        if stripped.startswith("["):
            return json.loads(text)

        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise DataIntegrityError("cme: settlement file is empty")
        fields = {f.strip().lower() for f in reader.fieldnames}
        if not {"month", "settle"} <= fields:
            raise DataIntegrityError(
                "cme: CSV needs at least 'month' and 'settle' columns; got "
                f"{reader.fieldnames}"
            )
        return [{k.strip().lower(): v for k, v in row.items()} for row in reader]

    def to_observations(
        self, rows: list[dict], root: str, trade_date: date, source_batch: str
    ) -> list[Observation]:
        as_of = self.at_local(trade_date, PUBLICATION_LOCAL_TIME)
        obs: list[Observation] = []
        skipped = 0
        for row in rows:
            month = parse_contract_month(row.get("month", ""))
            if month is None:
                # Strip listings carry a "Total" summary row.
                skipped += 1
                continue
            raw = str(row.get("settle", "")).replace(",", "").strip()
            if not raw or raw in {"-", "."}:
                skipped += 1
                continue
            try:
                settle = float(raw)
            except ValueError:
                skipped += 1
                continue
            if not 80.0 <= settle <= 100.0:
                # ZQ prices as 100 minus a rate. Outside this band the file is
                # not what it claims to be, and storing it would poison a path
                # that chains through every month.
                raise DataIntegrityError(
                    f"cme: {root} {month:%Y-%m} settle {settle} is outside the "
                    "plausible 80-100 band for a 100-minus-rate quote"
                )

            meta = contract_meta(root, month)
            self.discovered[meta.series_id] = meta
            obs.append(
                Observation(
                    series_id=meta.series_id,
                    value_date=trade_date,
                    as_of=as_of,
                    value=settle,
                    source_batch=source_batch,
                    as_of_basis="derived_lag",
                )
            )
        if skipped:
            self.log.info("%s %s: %d non-contract row(s) skipped", root, trade_date, skipped)
        if not obs:
            raise EmptyFetchError(
                f"cme: {root} {trade_date} parsed no usable settlements from "
                f"{len(rows)} row(s)."
            )
        self.log.info("%s %s: %d contract months", root, trade_date, len(obs))
        return obs

    def thin_contracts(self, rows: list[dict]) -> list[date]:
        """Contract months whose open interest is below THIN_OPEN_INTEREST.

        Only meaningful if the supplied file carries open interest; returns
        nothing when it does not, rather than pretending everything is liquid.
        """
        thin: list[date] = []
        for row in rows:
            month = parse_contract_month(row.get("month", ""))
            raw = str(row.get("openinterest") or row.get("openInterest") or "")
            raw = raw.replace(",", "").strip()
            if month is not None and raw.isdigit() and int(raw) < THIN_OPEN_INTEREST:
                thin.append(month)
        return sorted(thin)
