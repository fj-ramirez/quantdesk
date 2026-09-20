"""FRED / ALFRED adapter.

Uses the ALFRED realtime parameters wherever they exist, so the stored as_of is
the source's own vintage rather than anything we infer. A real-time window
returns one row per (date, vintage), which is exactly the shape the observations
table wants.

Four source behaviours this adapter must not paper over:

  * A missing value arrives as the string ".". It is skipped and logged, never
    stored and never filled (spec 0.5). Treasury holidays are the common case.
  * A retired or renamed series returns HTTP 400 "The series does not exist".
    That raises. GOLDPMGBD228NLBM is a live example: it was the daily LBMA gold
    fix, it is gone, and an adapter that returned [] for it would have quietly
    dropped gold from the universe.
  * ALFRED caps a request at MAX_VINTAGE_DATES_PER_REQUEST vintage dates, which
    a multi-year daily backfill exceeds. Requests are chunked to stay under it,
    and a series declared revisable refuses to be chunked rather than silently
    dropping late revisions.
  * ALFRED's vintage archive starts at a series-specific date and holds nothing
    before it (DGS10: 2005, WTI: 2011, EURUSD: 2014, HY OAS: 2023, SP500: never;
    PAYEMS reaches 1955, which is what matters since it is the revisable one).
    The archive start is discovered per series, and observations older than it
    are stored with as_of_basis=archive_floor rather than being given a
    fabricated publication date.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time, timedelta

import httpx

from ..errors import EmptyFetchError, UnknownSeriesError
from ..models import Observation
from .base import BaseAdapter

BASE_URL = "https://api.stlouisfed.org/fred"

# ALFRED vintages have day resolution: realtime_start is a date, not a
# timestamp. We place as_of at this local wall-clock time on that date. Later
# than the true release moment (payrolls lands 08:30 ET), which is the safe
# direction: a backtest may miss a value it could technically have had, but can
# never see one before it existed. Intraday release precision is not obtainable
# from ALFRED and would need a different source (spec phase 8).
VINTAGE_LOCAL_TIME = time(16, 0)

# FRED's sentinel for "no observation on this date".
MISSING_MARKER = "."

# Passed as realtime_end to request every vintage up to and including today.
REALTIME_MAX = "9999-12-31"

# ALFRED refuses a JSON request whose real-time period spans more than this many
# vintage dates. Measured from the API on 2026-09-12; the error text states it
# explicitly. A daily series publishes a new vintage every business day, so a
# 23-year backfill of DGS10 spans ~5100 vintage dates and must be chunked.
MAX_VINTAGE_DATES_PER_REQUEST = 2000

# Vintage dates a series accrues per year, by frequency. Used to size chunks
# before making a request rather than discovering the cap by hitting it.
VINTAGE_DATES_PER_YEAR = {"d": 260, "w": 52, "m": 12, "q": 4, "irregular": 12}

# Fraction of the cap a single chunk may target. Headroom, because the estimate
# above is approximate and an off-by-a-few would fail the whole run.
CHUNK_SAFETY_FRACTION = 0.5

# When chunking, the real-time window is extended this far past the chunk's last
# value_date so a value published just after the boundary is still captured with
# its true vintage rather than being clipped to the next chunk's start.
REALTIME_BUFFER_DAYS = 45

# Substring ALFRED returns when a series has no vintage history at all. Detected
# rather than hardcoded per series, because which series the licensors allow into
# ALFRED changes without notice.
NO_ALFRED_MARKER = "does not exist in ALFRED"

# Series known to have no ALFRED history as of 2026-09-12, kept for documentation
# and asserted by the universe tests. The adapter does not branch on this set --
# it asks the API (see archive_start) -- so a series added to or removed from
# ALFRED is handled correctly without editing this list.
NO_ALFRED_CODES = frozenset({"SP500"})

# as_of for a NO_ALFRED_CODES series: the value date at this local time. This is
# the snapshot convention (the index close), NOT a publication vintage -- FRED
# posts some time after the close. It is therefore marginally optimistic, the
# only place in this adapter that is, and it applies only to series for which no
# vintage information exists at any price.
NO_ALFRED_LOCAL_TIME = time(16, 0)


class FredAdapter(BaseAdapter):
    name = "fred"

    def __init__(self, series_map: dict[str, str], api_key: str, **kw) -> None:
        super().__init__(series_map, **kw)
        if not api_key:
            raise UnknownSeriesError(
                "FRED adapter requires an API key: set XA_FRED_API_KEY. "
                "Free key at https://fred.stlouisfed.org/docs/api/api_key.html"
            )
        self.api_key = api_key
        # archive_start costs a request per code; a backfill asks repeatedly.
        self._archive_start_cache: dict[str, date | None] = {}

    def _get(self, endpoint: str, params: dict) -> dict:
        p = {"api_key": self.api_key, "file_type": "json", **params}
        r = self.client.get(f"{BASE_URL}/{endpoint}", params=p)
        if r.status_code == 400:
            # FRED reports a retired, renamed or malformed code this way.
            try:
                msg = r.json().get("error_message", r.text[:200])
            except ValueError:
                msg = r.text[:200]
            raise UnknownSeriesError(
                f"fred: {params.get('series_id')!r} rejected by the API: {msg}"
            )
        r.raise_for_status()
        return r.json()

    def describe(self, source_code: str) -> dict:
        """Live metadata for one code. Raises if the series no longer exists,
        which is how the universe check verifies codes before a backfill."""
        d = self._get("series", {"series_id": source_code})
        return d["seriess"][0]

    def fetch(
        self,
        series_codes: Sequence[str],
        start: date,
        end: date,
        source_batch: str,
        *,
        frequency: str = "d",
        revisable: bool = False,
    ) -> list[Observation]:
        """frequency and revisable come from the series metadata and decide how
        the real-time period is chunked. They are passed in rather than looked up
        so the adapter stays independent of the store."""
        out: list[Observation] = []
        for code in series_codes:
            series_id = self.resolve(code)
            archive_start = self.archive_start(code)

            if archive_start is None:
                # No vintage history at any date. Everything is derived.
                out.extend(
                    self._fetch_without_vintages(code, series_id, start, end, source_batch)
                )
                continue

            if archive_start > start:
                # The archive begins after the backfill does. Split rather than
                # pretend: observations before the archive get an archive_floor
                # as_of, which is true (the value was current by then) but weak,
                # and is marked so it can never be mistaken for a real vintage.
                self.log.warning(
                    "%s (%s): ALFRED vintages begin %s, after the requested start "
                    "%s. %s..%s will be stored with as_of_basis=archive_floor: "
                    "known by that date, true publication date unrecoverable.",
                    code, series_id, archive_start, start, start,
                    archive_start - timedelta(days=1),
                )
                out.extend(
                    self._fetch_pre_archive(
                        code, series_id, start, archive_start, source_batch
                    )
                )

            covered_start = max(start, archive_start)
            if covered_start <= end:
                out.extend(
                    self._fetch_with_vintages(
                        code, series_id, covered_start, end, source_batch,
                        frequency, revisable,
                    )
                )
        return out

    def archive_start(self, code: str) -> date | None:
        """First date ALFRED holds a vintage for this series, or None if it holds
        none at all.

        One cheap request, and the answer varies enormously: PAYEMS reaches back
        to 1955 (which is what matters, since it is revisable), DGS10 to 2005,
        WTI to 2011, EURUSD to 2014, HY OAS to 2023, and SP500 not at all.
        Discovering it beats assuming it: assuming produced a backfill in which
        every pre-2011 oil observation claimed to have become known in 2011.
        """
        if code in self._archive_start_cache:
            return self._archive_start_cache[code]
        try:
            payload = self._get(
                "series/vintagedates", {"series_id": code, "limit": 1}
            )
            first = date.fromisoformat(payload["vintage_dates"][0])
        except UnknownSeriesError as e:
            if NO_ALFRED_MARKER not in str(e):
                raise  # a genuinely retired or misspelled code
            first = None
        except (KeyError, IndexError):
            first = None
        self._archive_start_cache[code] = first
        return first

    def chunk_years(self, frequency: str) -> int:
        """How many years of value dates one request may cover.

        Derived from the vintage cap and the series frequency rather than
        guessed: a daily series accrues ~260 vintage dates a year, so ~3 years
        fits inside half the cap. Monthly series get a span so wide that any
        realistic range is a single request.
        """
        per_year = VINTAGE_DATES_PER_YEAR.get(frequency, 260)
        budget = MAX_VINTAGE_DATES_PER_REQUEST * CHUNK_SAFETY_FRACTION
        return max(int(budget // per_year), 1)

    def plan_windows(
        self, start: date, end: date, frequency: str
    ) -> list[tuple[date, date, str]]:
        """Split a backfill into (observation_start, observation_end, realtime_end)
        requests that each stay under the vintage cap.

        Both the observation range and the real-time window move together. That
        matters: ALFRED clips realtime_start to the start of the requested window,
        so asking for old observations inside a late real-time window would report
        a vintage years after the value actually became known. Each chunk's
        real-time window is extended by REALTIME_BUFFER_DAYS so a value published
        just after the boundary keeps its true vintage.
        """
        span = self.chunk_years(frequency)
        if self._add_years(start, span) > end:
            return [(start, end, REALTIME_MAX)]

        windows: list[tuple[date, date, str]] = []
        lo = start
        while lo <= end:
            hi = min(self._add_years(lo, span), end)
            # ALFRED rejects a realtime_end after today unless it is exactly
            # REALTIME_MAX, so the final chunk asks for "every later vintage".
            buffered = hi + timedelta(days=REALTIME_BUFFER_DAYS)
            rt_end = REALTIME_MAX if buffered >= self._today() else buffered.isoformat()
            windows.append((lo, hi, rt_end))
            lo = hi + timedelta(days=1)
        return windows

    @staticmethod
    def _add_years(d: date, years: int) -> date:
        try:
            return d.replace(year=d.year + years)
        except ValueError:  # 29 February
            return d.replace(year=d.year + years, day=28)

    @staticmethod
    def _today() -> date:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # ALFRED's "today" is US Central (St. Louis Fed). Using the machine's
        # local date could ask for a realtime_end the API considers future.
        return datetime.now(ZoneInfo("America/Chicago")).date()

    def _fetch_pre_archive(
        self, code: str, series_id: str, start: date, archive_start: date,
        source_batch: str,
    ) -> list[Observation]:
        """Observations older than the vintage archive.

        as_of is the archive start: the earliest moment we can prove the value
        was already current. Never too early, so no-lookahead is preserved, but
        it is weaker than a real vintage and is marked archive_floor so that an
        as-of query before the floor correctly returns nothing rather than
        silently trusting a fabricated publication date.
        """
        payload = self._get(
            "series/observations",
            {
                "series_id": code,
                "observation_start": start.isoformat(),
                "observation_end": (archive_start - timedelta(days=1)).isoformat(),
            },
        )
        rows = payload.get("observations", [])
        if not rows:
            return []
        return self._to_observations(
            rows, code, series_id, start, archive_start, source_batch,
            basis="archive_floor", floor=archive_start,
        )

    def _fetch_with_vintages(
        self,
        code: str,
        series_id: str,
        start: date,
        end: date,
        source_batch: str,
        frequency: str,
        revisable: bool,
    ) -> list[Observation]:
        windows = self.plan_windows(start, end, frequency)

        if len(windows) > 1:
            if revisable:
                # Chunking bounds each request's real-time window, so a revision
                # arriving after its chunk closes would be missed. For a series
                # declared revisable that is a silent hole in exactly the data
                # the point-in-time store exists to protect, so refuse instead.
                raise EmptyFetchError(
                    f"fred: {code} ({series_id}) is marked revisable but its "
                    f"frequency ({frequency}) needs {len(windows)} chunked vintage "
                    f"requests over {start}..{end}. Chunking would drop revisions "
                    "published after each chunk closes. Narrow the range so it "
                    "fits one request."
                )
            self.log.info(
                "%s (%s): %d chunked requests (vintage cap %d per request)",
                code, series_id, len(windows), MAX_VINTAGE_DATES_PER_REQUEST,
            )

        rows: list[dict] = []
        for obs_start, obs_end, rt_end in windows:
            payload = self._get(
                "series/observations",
                {
                    "series_id": code,
                    "observation_start": obs_start.isoformat(),
                    "observation_end": obs_end.isoformat(),
                    # Every vintage, not just the currently-published one.
                    "realtime_start": obs_start.isoformat(),
                    "realtime_end": rt_end,
                },
            )
            rows.extend(payload.get("observations", []))

        return self._to_observations(
            rows, code, series_id, start, end, source_batch, basis="source_vintage"
        )

    def _fetch_without_vintages(
        self, code: str, series_id: str, start: date, end: date, source_batch: str
    ) -> list[Observation]:
        """For series ALFRED carries no vintage history for. as_of is derived
        from the value date; see NO_ALFRED_LOCAL_TIME."""
        self.log.warning(
            "%s (%s): no ALFRED vintage history for this series; as_of is derived "
            "from the value date, not a publication vintage",
            code, series_id,
        )
        payload = self._get(
            "series/observations",
            {
                "series_id": code,
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
            },
        )
        return self._to_observations(
            payload.get("observations", []), code, series_id, start, end,
            source_batch, basis="derived_lag",
        )

    def _to_observations(
        self,
        rows: list[dict],
        code: str,
        series_id: str,
        start: date,
        end: date,
        source_batch: str,
        *,
        basis: str,
        floor: date | None = None,
    ) -> list[Observation]:
        if not rows:
            raise EmptyFetchError(
                f"fred: {code} ({series_id}) returned no observations for "
                f"{start}..{end}. The code resolves but the range is empty; "
                "check the range against the series' observation_start."
            )

        obs: list[Observation] = []
        missing = 0
        for row in rows:
            raw = row["value"]
            if raw == MISSING_MARKER:
                missing += 1
                continue
            value_date = date.fromisoformat(row["date"])
            if basis == "source_vintage":
                as_of = self.at_local(
                    date.fromisoformat(row["realtime_start"]), VINTAGE_LOCAL_TIME
                )
            elif basis == "archive_floor":
                as_of = self.at_local(floor, VINTAGE_LOCAL_TIME)
            else:
                as_of = self.at_local(value_date, NO_ALFRED_LOCAL_TIME)
            obs.append(
                Observation(
                    series_id=series_id,
                    value_date=value_date,
                    as_of=as_of,
                    value=float(raw),
                    source_batch=source_batch,
                    as_of_basis=basis,
                )
            )
        if missing:
            # Loud by design: these are holidays and outages. Nothing fills them.
            self.log.warning(
                "%s (%s): %d of %d rows had no value and were skipped, not filled",
                code, series_id, missing, len(rows),
            )
        self.log.info(
            "%s (%s): %d observations (as_of_basis=%s)", code, series_id, len(obs), basis
        )
        return obs


def check_codes(api_key: str, codes: Sequence[str], timeout: float = 30.0) -> dict[str, str]:
    """Verify codes are live. Returns {code: 'ok' | reason}.

    Kept separate from fetch so the universe can be validated without pulling
    data, and so a backfill never starts against a map containing a dead code.
    """
    adapter = FredAdapter({c: c for c in codes}, api_key, timeout=timeout)
    result: dict[str, str] = {}
    try:
        for code in codes:
            try:
                d = adapter.describe(code)
                result[code] = (
                    f"ok {d['frequency_short']} "
                    f"{d['observation_start']}..{d['observation_end']}"
                )
            except (UnknownSeriesError, httpx.HTTPError) as e:
                result[code] = f"FAIL {e}"
    finally:
        adapter.close()
    return result
