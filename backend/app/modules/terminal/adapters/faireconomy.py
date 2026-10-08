"""faireconomy.media's weekly economic calendar (T139).

    https://nfs.faireconomy.media/ff_calendar_thisweek.json

ForexFactory's own publisher, approved by the user for this single-user desk on 2026-10-07.
Fetched once per night by the `calendar` step, never polled.

The feed's shape as observed on 2026-10-07: a JSON array of
`{title, country, date, impact, forecast, previous}`, about 11 KB for every country.

* `date` is ISO 8601 with an offset (`-04:00` observed, which is ET). The offset is read, never
  assumed. A naive timestamp is refused.
* `impact` is `High | Medium | Low | Holiday`. It is the publisher's generic rating and is kept
  as given. It rated a 30-year auction "Low" on a day the desk's whole rates thesis hinged on it,
  so nothing downstream should treat it as a filter it can trust.
* `forecast` and `previous` are free text (`"200K"`, `"0.7%"`, `"5.31|2.6"`, or empty). They are
  kept raw and parsed by `releases.parse_value`.
* There is **no `actual` field**, and the feed covers the current week only. History therefore
  accrues from the first nightly run and cannot be backfilled from this source.

Like every adapter here, this one never writes. It returns records; `releases.persist` stores
them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime

import httpx

from ..errors import EmptyFetchError
from ..logging import get_logger
from ..releases import SOURCE_FAIRECONOMY, ReleaseRecord, make_release_id

log = get_logger("adapters.faireconomy")

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

#: US releases, plus the feed's `All` rows (OPEC meetings and the like).
FEED_COUNTRIES: frozenset[str] = frozenset({"USD", "All"})

_REQUIRED_KEYS = ("title", "country", "date")


def _blank_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_feed(
    payload: Iterable[dict], countries: frozenset[str] = FEED_COUNTRIES
) -> list[ReleaseRecord]:
    """The feed's rows as release records, for the countries asked for.

    Raises `EmptyFetchError` if a row lacks a required key or carries a naive or unparseable
    date: a layout change must stop the step loudly rather than quietly shorten the calendar.
    """
    out: list[ReleaseRecord] = []
    for i, row in enumerate(payload):
        missing = [k for k in _REQUIRED_KEYS if not row.get(k)]
        if missing:
            raise EmptyFetchError(f"faireconomy: row {i} lacks {missing}; the feed changed shape")
        if row["country"] not in countries:
            continue
        try:
            scheduled_at = datetime.fromisoformat(row["date"])
        except ValueError as exc:
            raise EmptyFetchError(
                f"faireconomy: row {i} has an unparseable date {row['date']!r}"
            ) from exc
        if scheduled_at.tzinfo is None:
            raise EmptyFetchError(
                f"faireconomy: row {i} date {row['date']!r} has no offset; refusing to assume one"
            )
        title = str(row["title"]).strip()
        out.append(
            ReleaseRecord(
                release_id=make_release_id(
                    SOURCE_FAIRECONOMY, row["country"], title, scheduled_at
                ),
                source=SOURCE_FAIRECONOMY,
                title=title,
                country=row["country"],
                scheduled_at=scheduled_at,
                impact=_blank_to_none(row.get("impact")),
                consensus_raw=_blank_to_none(row.get("forecast")),
                prior_raw=_blank_to_none(row.get("previous")),
            )
        )
    return out


def decode(body: str) -> list[dict]:
    """The response body as the feed's array. When the publisher refuses a request it answers
    with an HTML page rather than an error status, so the body is checked, not trusted."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise EmptyFetchError(
            f"faireconomy: response is not JSON (starts {body[:120]!r}); the feed may be "
            "rate-limiting this host"
        ) from exc
    if not isinstance(payload, list) or not payload:
        raise EmptyFetchError("faireconomy: the feed returned no events")
    return payload


def fetch_feed(timeout: float = 30.0) -> list[ReleaseRecord]:
    response = httpx.get(
        FEED_URL,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "xactx/0.1 (personal research)"},
    )
    response.raise_for_status()
    records = parse_feed(decode(response.text))
    if not records:
        raise EmptyFetchError("faireconomy: the feed carried no US events this week")
    log.info("faireconomy: %d events for %s", len(records), sorted(FEED_COUNTRIES))
    return records
