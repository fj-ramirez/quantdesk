"""The economic calendar: scheduled releases, point-in-time (T139).

`terminal.releases` was created empty in T79 and documented as "Empty, and knowingly so",
because the original spec assumed a paid consensus vendor. T139 fills it from two free sources:

* **faireconomy.media's weekly feed**: ForexFactory's own publisher, approved by the user for
  this single-user desk on 2026-10-07. It provides US data releases, Treasury auctions and Fed
  speakers, with the publisher's forecast and the prior print.
* **federalreserve.gov's FOMC calendar**, which `fomc.py` already parsed into a JSON file. That
  file is gone. The meetings live here now, so one table answers "what is scheduled".

**The point-in-time rule applies here exactly as it does to `observations`.** A release is
identified by `release_id`, and every change to what we knew about it adds a *vintage* keyed by
`(release_id, as_of)`, where `as_of` is the moment our fetch first saw that version. A
reschedule, a revised forecast or an event dropped from the feed each adds a row. None of them
overwrites one. That makes "what was on the calendar, and what was the consensus, as of last
Tuesday" answerable, which is the reason a consensus is worth storing at all.

**A consensus is only a consensus if it was known before the event.** `consensus_as_of` is the
fetch time and must be strictly before `scheduled_at` (spec 2.3). A forecast first seen after its
event is dropped, not stored with a later timestamp. Spec 7 warns that some vendors rewrite the
forecast after the print, which would silently turn every event study into hindsight. For the
same reason, an event whose time has passed is frozen: a later fetch never adds a vintage to it.

**A null is never a zero.** An empty forecast is null. So is a value that does not parse as one
number (`"5.31|2.6"`, an auction's high yield and bid-to-cover). The raw string is kept beside
the parsed column in both cases, so nothing is lost and nothing is guessed.

This module is pure apart from `persist`, `latest_vintages`, `upcoming` and the FOMC readers,
which take a connection. The fetchers live in `adapters/faireconomy.py` and `fomc.py`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import DataIntegrityError, XactxError
from .logging import get_logger

log = get_logger("releases")

__all__ = [
    "FOMC_ANNOUNCEMENT_TIME",
    "SOURCE_FAIRECONOMY",
    "SOURCE_FED",
    "PersistResult",
    "ReleaseRecord",
    "last_fetch_attempt",
    "last_successful_fetch",
    "latest_vintages",
    "make_release_id",
    "parse_value",
    "persist",
    "plan_vintages",
    "upcoming",
]

SOURCE_FAIRECONOMY = "faireconomy"
SOURCE_FED = "federalreserve"

STATUS_SCHEDULED = "scheduled"
STATUS_REMOVED = "removed"

#: The calendar's wall clock. Release ids carry the ET date, because that is the date a release
#: is known by ("Thursday's claims"), and a UTC date would split the evening events.
ET = ZoneInfo("America/New_York")

#: The FOMC statement is released at 14:00 ET on the meeting's final day.
FOMC_ANNOUNCEMENT_TIME = time(14, 0)

_SCALE = {"": 1.0, "%": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_VALUE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)([KMBT%]?)$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def parse_value(raw: str | None) -> float | None:
    """A published figure as one number, or None. Never a guess.

    `"200K"` -> 200000, `"0.7%"` -> 0.7 (percent, not a fraction), `"-41.7K"` -> -41700,
    `"47.5"` -> 47.5. Anything else is None: empty, compound (`"5.31|2.6"`), bounded (`"<0.1%"`)
    or unrecognised. The caller keeps the raw string beside the result.
    """
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    match = _VALUE_RE.match(text)
    if match is None:
        return None
    number, suffix = match.groups()
    return float(number) * _SCALE[suffix]


def make_release_id(source: str, country: str, title: str, scheduled_at: datetime) -> str:
    """`ff:usd:unemployment-claims:2026-10-08`. Stable across a same-day time change, which is
    the point: a release moved from 08:30 to 10:00 is the same release with a new vintage, not
    a new release beside a stale one."""
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    local_date = scheduled_at.astimezone(ET).date().isoformat()
    prefix = "ff" if source == SOURCE_FAIRECONOMY else source
    return f"{prefix}:{country.lower()}:{slug}:{local_date}"


@dataclass(frozen=True)
class ReleaseRecord:
    """One release as a source describes it at one fetch. What a vintage is built from."""

    release_id: str
    source: str
    title: str
    country: str
    scheduled_at: datetime
    impact: str | None = None
    starts_on: date | None = None
    series_id: str | None = None
    consensus_raw: str | None = None
    prior_raw: str | None = None
    status: str = STATUS_SCHEDULED

    def __post_init__(self) -> None:
        # Invariant 4, at the point a timestamp enters the module.
        if self.scheduled_at.tzinfo is None:
            raise DataIntegrityError(
                f"{self.release_id}: scheduled_at is naive; a release time must carry its zone"
            )

    def comparable(self) -> tuple:
        """The fields whose change makes a new vintage."""
        return (
            self.title,
            self.country,
            self.impact,
            self.scheduled_at.astimezone(UTC),
            self.starts_on,
            self.series_id,
            self.consensus_raw,
            self.prior_raw,
            self.status,
        )


def disambiguate(records: Sequence[ReleaseRecord]) -> list[ReleaseRecord]:
    """Two releases with one id in one fetch get the local time appended to both.

    Rare (the same title twice on one day for one country), but a silent collision would merge
    two events into one release's vintage history.
    """
    counts: dict[str, int] = {}
    for r in records:
        counts[r.release_id] = counts.get(r.release_id, 0) + 1
    out = []
    for r in records:
        if counts[r.release_id] > 1:
            stamp = r.scheduled_at.astimezone(ET).strftime("%H%M")
            r = replace(r, release_id=f"{r.release_id}@{stamp}")
        out.append(r)
    return out


def plan_vintages(
    fetched: Sequence[ReleaseRecord],
    latest: dict[str, ReleaseRecord],
    fetched_at: datetime,
) -> list[ReleaseRecord]:
    """Which vintages one fetch adds. Pure, so the rules are testable without a database.

    * **Unseen release:** add it. If its time has already passed, add it without a consensus.
      A forecast first seen after its event is not a consensus.
    * **Seen, time already passed:** nothing. A past event is frozen.
    * **Seen, still ahead, anything changed:** add a vintage with the new state.
    * **Previously scheduled, still ahead, inside the span this fetch covers, but absent from
      it:** add a `removed` vintage. Dropping it from the calendar is information too.

    `latest` maps release_id to the newest stored vintage for the same source.
    """
    if fetched_at.tzinfo is None:
        raise DataIntegrityError("fetched_at must be timezone-aware")

    out: list[ReleaseRecord] = []
    seen: set[str] = set()
    for record in fetched:
        seen.add(record.release_id)
        past = record.scheduled_at <= fetched_at
        if past:
            record = replace(record, consensus_raw=None)
        prev = latest.get(record.release_id)
        if prev is None:
            out.append(record)
        elif past:
            continue
        elif prev.comparable() != record.comparable():
            out.append(record)

    if fetched:
        span_start = min(r.scheduled_at for r in fetched)
        span_end = max(r.scheduled_at for r in fetched)
        for release_id, prev in sorted(latest.items()):
            if (
                release_id not in seen
                and prev.status == STATUS_SCHEDULED
                and span_start <= prev.scheduled_at <= span_end
                and prev.scheduled_at > fetched_at
            ):
                out.append(replace(prev, status=STATUS_REMOVED, consensus_raw=None))
    return out


# --- storage -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistResult:
    """What one persist call did, returned so the CLI and tests can assert on it."""

    source_batch: str
    fetched: int
    inserted: int
    removed: int

    @property
    def unchanged(self) -> int:
        return self.fetched - (self.inserted - self.removed)


_LATEST_SQL = """
    SELECT DISTINCT ON (release_id)
           release_id, source, title, country, scheduled_at, impact, starts_on, series_id,
           consensus_raw, prior_raw, status
    FROM releases
    WHERE source = ?
    ORDER BY release_id, as_of DESC
"""


def latest_vintages(conn, source: str) -> dict[str, ReleaseRecord]:
    """The newest stored vintage of every release from `source`."""
    rows = conn.execute(_LATEST_SQL, [source]).fetchall()
    return {
        r[0]: ReleaseRecord(
            release_id=r[0], source=r[1], title=r[2], country=r[3], scheduled_at=r[4],
            impact=r[5], starts_on=r[6], series_id=r[7], consensus_raw=r[8], prior_raw=r[9],
            status=r[10],
        )
        for r in rows
    }


def persist(
    conn,
    records: Iterable[ReleaseRecord],
    *,
    source: str,
    fetched_at: datetime,
    source_batch: str,
) -> PersistResult:
    """Add whatever vintages this fetch implies. Never updates or deletes a row.

    Re-running with the same fetch adds nothing, which is what makes the nightly step safe to
    repeat by hand.
    """
    fetched = disambiguate(list(records))
    wrong = sorted({r.source for r in fetched} - {source})
    if wrong:
        raise DataIntegrityError(f"persist({source!r}) was handed records from {wrong}")

    planned = plan_vintages(fetched, latest_vintages(conn, source), fetched_at)
    for r in planned:
        consensus = parse_value(r.consensus_raw)
        consensus_as_of = fetched_at if r.consensus_raw else None
        if consensus_as_of is not None and consensus_as_of >= r.scheduled_at:
            # Spec 2.3, enforced where the row is written so the error can name the release.
            raise DataIntegrityError(
                f"{r.release_id}: consensus_as_of {consensus_as_of.isoformat()} is not before "
                f"scheduled_at {r.scheduled_at.isoformat()}; that is hindsight, not consensus"
            )
        conn.execute(
            """
            INSERT INTO releases (
                release_id, as_of, source, title, country, impact, status, scheduled_at,
                starts_on, series_id, consensus, consensus_raw, consensus_as_of, prior,
                prior_raw, source_batch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                r.release_id, fetched_at, r.source, r.title, r.country, r.impact, r.status,
                r.scheduled_at, r.starts_on, r.series_id, consensus, r.consensus_raw,
                consensus_as_of, parse_value(r.prior_raw), r.prior_raw, source_batch,
            ],
        )

    removed = sum(1 for r in planned if r.status == STATUS_REMOVED)
    result = PersistResult(source_batch, len(fetched), len(planned), removed)
    log.info(
        "%s batch %s: %d fetched, %d new vintages (%d removed), %d unchanged",
        source, source_batch, result.fetched, result.inserted, result.removed, result.unchanged,
    )
    return result


def last_successful_fetch(conn, source: str) -> datetime | None:
    """When `source` last fetched cleanly. Not `max(as_of)`: an unchanged calendar adds no
    vintage, so the newest vintage says when something *changed*, not when we last looked."""
    row = conn.execute(
        "SELECT MAX(finished_at) FROM ingest_batches WHERE adapter = ? AND status = 'ok'",
        [source],
    ).fetchone()
    return row[0] if row else None


def last_fetch_attempt(conn, source: str) -> datetime | None:
    """When `source` was last *tried*, whatever the outcome. The rate-limit floor counts from
    here, so a failed attempt is not followed by an immediate retry."""
    row = conn.execute(
        "SELECT MAX(started_at) FROM ingest_batches WHERE adapter = ?", [source]
    ).fetchone()
    return row[0] if row else None


def upcoming(
    conn,
    as_of: datetime,
    *,
    days: int = 7,
    start: datetime | None = None,
    impacts: Sequence[str] | None = None,
) -> list[dict]:
    """Releases scheduled in a window, as the calendar was known at `as_of`.

    The window starts at midnight ET on `as_of`'s date unless `start` is given, so "today"
    includes this morning's releases. A release whose newest vintage at `as_of` is `removed` is
    left out.
    """
    if as_of.tzinfo is None:
        raise XactxError("as_of must be timezone-aware")
    if start is None:
        start = datetime.combine(as_of.astimezone(ET).date(), time(0), tzinfo=ET)
    end = start + timedelta(days=days)
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT DISTINCT ON (release_id)
                   release_id, source, title, country, impact, status, scheduled_at,
                   starts_on, consensus, consensus_raw, consensus_as_of, prior, prior_raw,
                   as_of
            FROM releases
            WHERE as_of <= ?
            ORDER BY release_id, as_of DESC
        ) r
        WHERE r.status = 'scheduled' AND r.scheduled_at >= ? AND r.scheduled_at < ?
        ORDER BY r.scheduled_at, r.release_id
        """,
        [as_of, start, end],
    ).fetchall()
    cols = [
        "release_id", "source", "title", "country", "impact", "status", "scheduled_at",
        "starts_on", "consensus", "consensus_raw", "consensus_as_of", "prior", "prior_raw",
        "as_of",
    ]
    out = [dict(zip(cols, r, strict=True)) for r in rows]
    if impacts:
        wanted = {i.lower() for i in impacts}
        out = [r for r in out if (r["impact"] or "").lower() in wanted]
    return out
