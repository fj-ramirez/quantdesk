"""T139: the economic calendar in `terminal.releases`, point-in-time.

Most of this runs offline: the feed parser against rows recorded from the live feed on
2026-10-07, the value parser, the vintage rules as a pure function, and `persist` against a
small in-memory stand-in for the connection. The point-in-time *query* (`upcoming`) is Postgres
SQL (`DISTINCT ON`) and is tested against a real database, skipped when none is reachable,
the same arrangement `test_terminal_store.py` uses.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

from app.modules.terminal import releases
from app.modules.terminal.adapters import faireconomy
from app.modules.terminal.errors import DataIntegrityError, EmptyFetchError
from app.modules.terminal.fomc import Meeting, meetings_to_records
from app.modules.terminal.releases import (
    SOURCE_FAIRECONOMY,
    ReleaseRecord,
    make_release_id,
    parse_value,
    plan_vintages,
)

ET = ZoneInfo("America/New_York")
UTC = dt.UTC

#: Recorded from https://nfs.faireconomy.media/ff_calendar_thisweek.json on 2026-10-07,
#: trimmed to the shapes that matter: a USD release with forecast and prior, a USD speech with
#: neither, an `All` row, other countries that must be filtered out, and a compound prior.
RECORDED_ROWS = [
    {"title": "OPEC-JMMC Meetings", "country": "All", "date": "2026-10-04T05:15:00-04:00",
     "impact": "Medium", "forecast": "", "previous": ""},
    {"title": "Bank Holiday", "country": "AUD", "date": "2026-10-04T16:00:00-04:00",
     "impact": "Holiday", "forecast": "", "previous": ""},
    {"title": "30-y Bond Auction", "country": "JPY", "date": "2026-10-07T23:35:00-04:00",
     "impact": "Low", "forecast": "", "previous": "4.08|3.8"},
    {"title": "30-y Bond Auction", "country": "USD", "date": "2026-10-08T13:01:00-04:00",
     "impact": "Low", "forecast": "", "previous": "5.31|2.6"},
    {"title": "FOMC Member Waller Speaks", "country": "USD", "date": "2026-10-08T04:30:00-04:00",
     "impact": "Medium", "forecast": "", "previous": ""},
    {"title": "BOE Gov Bailey Speaks", "country": "GBP", "date": "2026-10-08T08:15:00-04:00",
     "impact": "High", "forecast": "", "previous": ""},
    {"title": "Unemployment Claims", "country": "USD", "date": "2026-10-08T08:30:00-04:00",
     "impact": "Medium", "forecast": "200K", "previous": "197K"},
]


def _at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=ET)


def _claims(**changes) -> ReleaseRecord:
    base = ReleaseRecord(
        release_id=make_release_id(SOURCE_FAIRECONOMY, "USD", "Unemployment Claims", _at(8, 8, 30)),
        source=SOURCE_FAIRECONOMY,
        title="Unemployment Claims",
        country="USD",
        scheduled_at=_at(8, 8, 30),
        impact="Medium",
        consensus_raw="200K",
        prior_raw="197K",
    )
    return replace(base, **changes) if changes else base


# --- values: a null is never a zero --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("200K", 200_000.0),
        ("0.7%", 0.7),
        ("-41.7K", -41_700.0),
        ("47.5", 47.5),
        ("14.5B", 14.5e9),
        ("1,234", 1234.0),
    ],
)
def test_published_figures_parse_to_one_number(raw, expected):
    assert parse_value(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "   ", "5.31|2.6", "<0.1%", "n/a", "4.6%%"])
def test_anything_not_one_number_is_none_never_zero(raw):
    """An auction's `high yield | bid-to-cover` is two numbers; guessing one is worse than
    keeping the text, which the caller does."""
    assert parse_value(raw) is None


# --- ids ------------------------------------------------------------------------------------


def test_a_same_day_time_change_keeps_the_release_id():
    """A release moved within its day is the same release with a new vintage, not a second
    release beside a stale one."""
    a = make_release_id(SOURCE_FAIRECONOMY, "USD", "Unemployment Claims", _at(8, 8, 30))
    b = make_release_id(SOURCE_FAIRECONOMY, "USD", "Unemployment Claims", _at(8, 10, 0))
    assert a == b == "ff:usd:unemployment-claims:2026-10-08"


def test_the_id_carries_the_et_date_not_the_utc_one():
    """20:00 ET on the 8th is 00:00 UTC on the 9th; the release is known as the 8th's."""
    evening = _at(8, 20, 0)
    assert evening.astimezone(UTC).date() == dt.date(2026, 10, 9)
    assert make_release_id(SOURCE_FAIRECONOMY, "USD", "X", evening).endswith(":2026-10-08")


def test_colliding_ids_in_one_fetch_are_both_disambiguated():
    a = _claims()
    b = replace(a, scheduled_at=_at(8, 14, 0))
    out = releases.disambiguate([a, b])
    assert {r.release_id for r in out} == {f"{a.release_id}@0830", f"{a.release_id}@1400"}


# --- the feed -------------------------------------------------------------------------------


def test_the_recorded_feed_parses_to_us_and_all_rows_only():
    records = faireconomy.parse_feed(RECORDED_ROWS)
    assert [(r.country, r.title) for r in records] == [
        ("All", "OPEC-JMMC Meetings"),
        ("USD", "30-y Bond Auction"),
        ("USD", "FOMC Member Waller Speaks"),
        ("USD", "Unemployment Claims"),
    ]


def test_feed_times_keep_their_offset_and_compare_in_utc():
    claims = next(r for r in faireconomy.parse_feed(RECORDED_ROWS) if "Claims" in r.title)
    assert claims.scheduled_at == dt.datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    assert claims.consensus_raw == "200K"
    assert claims.prior_raw == "197K"


def test_empty_feed_strings_become_none():
    waller = next(r for r in faireconomy.parse_feed(RECORDED_ROWS) if "Waller" in r.title)
    assert waller.consensus_raw is None
    assert waller.prior_raw is None


def test_a_naive_feed_date_is_refused_rather_than_assumed():
    row = dict(RECORDED_ROWS[-1], date="2026-10-08T08:30:00")
    with pytest.raises(EmptyFetchError, match="no offset"):
        faireconomy.parse_feed([row])


def test_a_row_missing_a_required_key_stops_the_step():
    with pytest.raises(EmptyFetchError, match="changed shape"):
        faireconomy.parse_feed([{"title": "X", "country": "USD"}])


def test_an_html_refusal_page_is_not_mistaken_for_an_empty_week():
    with pytest.raises(EmptyFetchError, match="not JSON"):
        faireconomy.decode("<!DOCTYPE html><title>Request Denied</title>")


# --- the vintage rules ----------------------------------------------------------------------

FETCH_TUE = dt.datetime(2026, 10, 6, 7, 0, tzinfo=UTC)  # 03:00 ET Tuesday
FETCH_WED = dt.datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
FETCH_FRI = dt.datetime(2026, 10, 9, 7, 0, tzinfo=UTC)  # after Thursday's claims


def test_an_unseen_release_is_added():
    assert plan_vintages([_claims()], {}, FETCH_TUE) == [_claims()]


def test_an_unchanged_refetch_adds_nothing():
    """Idempotency: the nightly step can be re-run by hand without growing the table."""
    latest = {_claims().release_id: _claims()}
    assert plan_vintages([_claims()], latest, FETCH_WED) == []


def test_a_revised_forecast_adds_a_vintage():
    latest = {_claims().release_id: _claims()}
    revised = _claims(consensus_raw="205K")
    assert plan_vintages([revised], latest, FETCH_WED) == [revised]


def test_a_reschedule_adds_a_vintage():
    latest = {_claims().release_id: _claims()}
    moved = _claims(scheduled_at=_at(8, 10, 0))
    assert plan_vintages([moved], latest, FETCH_WED) == [moved]


def test_a_past_event_is_frozen():
    """A forecast rewritten after the print must never reach the table (spec 7)."""
    latest = {_claims().release_id: _claims()}
    rewritten = _claims(consensus_raw="231K")
    assert plan_vintages([rewritten], latest, FETCH_FRI) == []


def test_a_past_event_seen_for_the_first_time_carries_no_consensus():
    out = plan_vintages([_claims()], {}, FETCH_FRI)
    assert len(out) == 1
    assert out[0].consensus_raw is None
    assert out[0].prior_raw == "197K"


def test_an_upcoming_event_dropped_from_the_feed_gets_a_removed_vintage():
    other = _claims(release_id="ff:usd:other:2026-10-09", title="Other",
                    scheduled_at=_at(9, 10, 0))
    gone = _claims(release_id="ff:usd:gone:2026-10-08", title="Gone", scheduled_at=_at(8, 9, 0))
    latest = {r.release_id: r for r in (_claims(), other, gone)}
    out = plan_vintages([_claims(), other], latest, FETCH_WED)
    assert [(r.release_id, r.status) for r in out] == [("ff:usd:gone:2026-10-08", "removed")]


def test_nothing_is_removed_outside_the_span_the_fetch_covers():
    """Last week's events are not in this week's feed. That is not a cancellation."""
    next_month = _claims(release_id="ff:usd:later:2026-11-05", scheduled_at=dt.datetime(
        2026, 11, 5, 8, 30, tzinfo=ET))
    latest = {r.release_id: r for r in (_claims(), next_month)}
    assert plan_vintages([_claims()], latest, FETCH_WED) == []


def test_a_removed_release_that_reappears_is_scheduled_again():
    latest = {_claims().release_id: _claims(status="removed")}
    assert plan_vintages([_claims()], latest, FETCH_WED) == [_claims()]


def test_a_naive_fetch_time_is_refused():
    with pytest.raises(DataIntegrityError):
        plan_vintages([_claims()], {}, FETCH_TUE.replace(tzinfo=None))


# --- persist, against an in-memory stand-in -------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Just enough of the facade for `persist`: stores INSERTs, answers the latest-vintage
    SELECT from them. The real SQL is exercised by the Postgres tests below."""

    COLS = ("release_id", "as_of", "source", "title", "country", "impact", "status",
            "scheduled_at", "starts_on", "series_id", "consensus", "consensus_raw",
            "consensus_as_of", "prior", "prior_raw", "source_batch")

    def __init__(self):
        self.rows: list[dict] = []

    def execute(self, sql, params=None):
        if sql.lstrip().startswith("INSERT"):
            self.rows.append(dict(zip(self.COLS, params, strict=True)))
            return _FakeResult([])
        source = params[0]
        newest: dict[str, dict] = {}
        for row in self.rows:
            if row["source"] == source and (
                row["release_id"] not in newest
                or row["as_of"] > newest[row["release_id"]]["as_of"]
            ):
                newest[row["release_id"]] = row
        return _FakeResult([
            (r["release_id"], r["source"], r["title"], r["country"], r["scheduled_at"],
             r["impact"], r["starts_on"], r["series_id"], r["consensus_raw"], r["prior_raw"],
             r["status"])
            for r in newest.values()
        ])


def test_persist_stores_parsed_values_beside_the_raw_text():
    conn = _FakeConn()
    records = faireconomy.parse_feed(RECORDED_ROWS)
    result = releases.persist(conn, records, source=SOURCE_FAIRECONOMY, fetched_at=FETCH_TUE,
                              source_batch="b1")
    assert result.inserted == 4
    claims = next(r for r in conn.rows if r["title"] == "Unemployment Claims")
    assert claims["consensus"] == 200_000.0
    assert claims["consensus_as_of"] == FETCH_TUE
    assert claims["prior"] == 197_000.0
    auction = next(r for r in conn.rows if r["title"] == "30-y Bond Auction")
    assert auction["prior"] is None
    assert auction["prior_raw"] == "5.31|2.6"
    assert auction["consensus_as_of"] is None


def test_persist_twice_with_the_same_fetch_adds_nothing():
    conn = _FakeConn()
    records = faireconomy.parse_feed(RECORDED_ROWS)
    releases.persist(conn, records, source=SOURCE_FAIRECONOMY, fetched_at=FETCH_TUE,
                     source_batch="b1")
    again = releases.persist(conn, records, source=SOURCE_FAIRECONOMY, fetched_at=FETCH_WED,
                             source_batch="b2")
    assert again.inserted == 0
    assert again.unchanged == 4
    assert len(conn.rows) == 4


def test_persist_keeps_the_old_vintage_when_the_forecast_moves():
    conn = _FakeConn()
    releases.persist(conn, [_claims()], source=SOURCE_FAIRECONOMY, fetched_at=FETCH_TUE,
                     source_batch="b1")
    releases.persist(conn, [_claims(consensus_raw="205K")], source=SOURCE_FAIRECONOMY,
                     fetched_at=FETCH_WED, source_batch="b2")
    vintages = sorted((r["as_of"], r["consensus"]) for r in conn.rows)
    assert vintages == [(FETCH_TUE, 200_000.0), (FETCH_WED, 205_000.0)]


def test_persist_refuses_records_from_another_source():
    with pytest.raises(DataIntegrityError, match="handed records from"):
        releases.persist(_FakeConn(), [_claims()], source=releases.SOURCE_FED,
                         fetched_at=FETCH_TUE, source_batch="b1")


# --- FOMC -----------------------------------------------------------------------------------


def test_an_fomc_meeting_becomes_a_release_at_the_statement():
    meeting = Meeting(start=dt.date(2026, 10, 27), end=dt.date(2026, 10, 28),
                      effective=dt.date(2026, 10, 29))
    (record,) = meetings_to_records([meeting])
    assert record.release_id == "fomc:2026-10-28"
    assert record.scheduled_at == dt.datetime(2026, 10, 28, 14, 0, tzinfo=ET)
    assert record.starts_on == dt.date(2026, 10, 27)
    assert record.source == releases.SOURCE_FED
    assert record.consensus_raw is None


# --- point-in-time reads: need Postgres -----------------------------------------------------

_DSN = os.environ.get("DATABASE_URL", "")
_needs_pg = pytest.mark.skipif(
    not _DSN.startswith("postgresql"),
    reason="`upcoming` is Postgres SQL (DISTINCT ON); set DATABASE_URL to run it.",
)


@pytest.fixture
def pg_conn():
    """A real connection inside a transaction that is always rolled back."""
    from app.modules.terminal.store.db import connect

    c = connect()
    c.raw.autocommit = False
    yield c
    c.rollback()
    c.close()


@_needs_pg
def test_upcoming_shows_the_consensus_as_it_was_known_then(pg_conn):
    rid = "ff:usd:t139-test-claims:2026-10-08"
    first = _claims(release_id=rid)
    releases.persist(pg_conn, [first], source=SOURCE_FAIRECONOMY, fetched_at=FETCH_TUE,
                     source_batch="t139-test-1")
    releases.persist(pg_conn, [replace(first, consensus_raw="205K")],
                     source=SOURCE_FAIRECONOMY, fetched_at=FETCH_WED, source_batch="t139-test-2")

    def consensus_at(when):
        rows = releases.upcoming(pg_conn, when, days=7, start=_at(5, 0))
        return next(r["consensus"] for r in rows if r["release_id"] == rid)

    assert consensus_at(FETCH_TUE + dt.timedelta(hours=1)) == 200_000.0
    assert consensus_at(FETCH_WED + dt.timedelta(hours=1)) == 205_000.0


@_needs_pg
def test_upcoming_hides_a_release_whose_newest_vintage_is_removed(pg_conn):
    rid = "ff:usd:t139-test-gone:2026-10-08"
    keep = _claims(release_id="ff:usd:t139-test-keep:2026-10-09", scheduled_at=_at(9, 10))
    gone = _claims(release_id=rid, scheduled_at=_at(8, 9))
    releases.persist(pg_conn, [keep, gone], source=SOURCE_FAIRECONOMY, fetched_at=FETCH_TUE,
                     source_batch="t139-test-1")
    releases.persist(pg_conn, [keep], source=SOURCE_FAIRECONOMY, fetched_at=FETCH_WED,
                     source_batch="t139-test-2")
    before = {r["release_id"] for r in releases.upcoming(pg_conn, FETCH_TUE, start=_at(5, 0))}
    after = {r["release_id"] for r in releases.upcoming(pg_conn, FETCH_WED, start=_at(5, 0))}
    assert rid in before
    assert rid not in after
