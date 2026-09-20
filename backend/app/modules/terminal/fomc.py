"""FOMC meeting calendar.

The meeting calendar is the single most error-prone input to the implied policy
path. Spec 2.1 says so outright: when the computed probabilities drift from
published ones, the calendar is usually the culprit. So it is fetched from the
Federal Reserve rather than typed from memory, cached to a file with the moment
it was fetched, and never silently guessed.

What the path solver actually needs is the EFFECTIVE date: the day the new
target range begins to apply. The FOMC announces at the end of the final meeting
day, and the new rate takes effect the following day. A two-day meeting on the
27th-28th is therefore effective on the 29th, and putting the effective date on
the 28th shifts a whole month's weighting.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import httpx

from .errors import EmptyFetchError, XactxError
from .logging import get_logger

log = get_logger("fomc")

CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# The new target range applies the day after the announcement.
EFFECTIVE_LAG_DAYS = 1

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

_YEAR_RE = re.compile(r"(\d{4})\s+FOMC Meetings")
_MEETING_RE = re.compile(
    r'fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([A-Za-z/]+)\s*(?:</strong>)?\s*</div>'
    r'.*?fomc-meeting__date[^>]*>\s*([0-9\-/ *]+?)\s*(?:<|\()',
    re.DOTALL,
)


@dataclass(frozen=True)
class Meeting:
    """One FOMC meeting."""

    start: date
    end: date
    effective: date
    scheduled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> Meeting:
        return cls(
            start=date.fromisoformat(d["start"]),
            end=date.fromisoformat(d["end"]),
            effective=date.fromisoformat(d["effective"]),
            scheduled=d.get("scheduled", True),
        )

    def to_dict(self) -> dict:
        out = asdict(self)
        for k in ("start", "end", "effective"):
            out[k] = out[k].isoformat()
        return out


def _parse_day_range(year: int, month_text: str, day_text: str) -> tuple[date, date] | None:
    """'27-28' in January 2026 -> (2026-01-27, 2026-01-28).

    Handles the month-straddling form the Fed uses for meetings that cross a
    month boundary, e.g. 'April/May' with days '28-1'.
    """
    months = [MONTHS[m.strip().lower()] for m in month_text.split("/")
              if m.strip().lower() in MONTHS]
    if not months:
        return None

    days = [int(d) for d in re.findall(r"\d+", day_text)]
    if not days:
        return None

    first_month = months[0]
    last_month = months[-1] if len(months) > 1 else first_month
    start = date(year, first_month, days[0])
    end_day = days[-1]
    # A straddling meeting rolls into the next month, and if that month is
    # January it rolls into the next year too.
    end_year = year + 1 if last_month < first_month else year
    end = date(end_year, last_month, end_day)
    if end < start:
        return None
    return start, end


def parse_calendar(html: str) -> list[Meeting]:
    """Extract every meeting from the Fed's calendar page."""
    meetings: list[Meeting] = []
    # Split the document by year heading so each meeting is attributed correctly.
    marks = [(m.start(), int(m.group(1))) for m in _YEAR_RE.finditer(html)]
    if not marks:
        raise EmptyFetchError(
            "fomc: no year headings found on the calendar page; its layout "
            "changed and the parser needs updating rather than guessing."
        )
    marks.append((len(html), 0))

    for (start_pos, year), (end_pos, _) in pairwise(marks):
        block = html[start_pos:end_pos]
        for month_text, day_text in _MEETING_RE.findall(block):
            parsed = _parse_day_range(year, month_text, day_text)
            if parsed is None:
                continue
            start, end = parsed
            meetings.append(
                Meeting(
                    start=start,
                    end=end,
                    effective=end + timedelta(days=EFFECTIVE_LAG_DAYS),
                )
            )

    unique = sorted({(m.start, m.end, m.effective) for m in meetings})
    out = [Meeting(start=s, end=e, effective=f) for s, e, f in unique]
    if not out:
        raise EmptyFetchError(
            "fomc: the calendar page parsed to zero meetings; refusing to "
            "return an empty calendar that would silently flatten the path."
        )
    return out


def fetch_calendar(timeout: float = 30.0) -> list[Meeting]:
    r = httpx.get(
        CALENDAR_URL, timeout=timeout, follow_redirects=True,
        headers={"User-Agent": "xactx/0.1 (personal research)"},
    )
    r.raise_for_status()
    meetings = parse_calendar(r.text)
    log.info("fomc: parsed %d meetings, %s..%s", len(meetings),
             meetings[0].end, meetings[-1].end)
    return meetings


def save_calendar(meetings: list[Meeting], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source": CALENDAR_URL,
                "fetched_at": datetime.now(UTC).isoformat(),
                "effective_lag_days": EFFECTIVE_LAG_DAYS,
                "meetings": [m.to_dict() for m in meetings],
            },
            indent=1,
        ),
        encoding="utf-8",
    )


def load_calendar(path: Path) -> list[Meeting]:
    """Read the cached calendar, or say plainly that it needs fetching."""
    if not path.exists():
        raise XactxError(
            f"fomc: no calendar at {path}. Run `python -m xactx fomc --refresh` "
            "first: the policy path cannot be computed without meeting dates, "
            "and guessing them is the documented way this goes wrong (spec 2.1)."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Meeting.from_dict(m) for m in payload["meetings"]]


def calendar_fetched_at(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8")).get("fetched_at", "unknown")


def meetings_after(meetings: list[Meeting], after: date, count: int) -> list[Meeting]:
    """The next `count` meetings whose effective date is after `after`.

    Raises rather than returning a short list: a policy path quietly built from
    five meetings instead of eight is the kind of error that produces a
    confident, wrong answer.
    """
    upcoming = [m for m in meetings if m.effective > after]
    if len(upcoming) < count:
        raise EmptyFetchError(
            f"fomc: only {len(upcoming)} meeting(s) known after {after}, "
            f"need {count}. The cached calendar ends {meetings[-1].end}; "
            "refresh it (`python -m xactx fomc --refresh`)."
        )
    return upcoming[:count]
