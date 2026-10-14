"""Contract tests for the CFTC adapter (T155): source codes, the three reports' fields, as_of.

The row fixtures carry the field names the live API returned on 2026-10-13, including the
Disaggregated report's `swap__positions_short_all` with its double underscore.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from app.modules.terminal import universe
from app.modules.terminal.adapters.cftc import (
    DELAYED_RELEASES,
    KNOWN_CONTRACTS,
    REPORTS,
    SHUTDOWN_WINDOWS,
    CftcAdapter,
    SourceCode,
    parse_source_code,
    release_date,
)
from app.modules.terminal.errors import EmptyFetchError, UnknownSeriesError

NY = ZoneInfo("America/New_York")


def _adapter() -> CftcAdapter:
    return CftcAdapter(universe.series_map("cftc"))


def _row(code: str, day: str, **fields: str) -> dict:
    return {
        "cftc_contract_market_code": code,
        "market_and_exchange_names": KNOWN_CONTRACTS[code],
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        **fields,
    }


def test_bare_code_is_legacy_noncommercial():
    assert parse_source_code("13874A") == SourceCode("legacy", "13874A", "noncomm")


def test_split_code_names_report_contract_and_group():
    assert parse_source_code("tff:209742:lev_money") == SourceCode("tff", "209742", "lev_money")


@pytest.mark.parametrize("bad", [
    "999999",                  # unknown contract
    "tff:999999:lev_money",    # unknown contract in a split code
    "cit:13874A:lev_money",    # unknown report
    "tff:13874A:m_money",      # group from the other report
    "tff:13874A",              # wrong arity
])
def test_unknown_codes_raise(bad):
    with pytest.raises(UnknownSeriesError):
        parse_source_code(bad)


def test_every_registered_cftc_series_parses():
    metas = universe.fetchable_by_source("cftc")
    assert metas
    for m in metas:
        parse_source_code(m.source_code)


def test_every_raw_series_has_a_percentile():
    ids = {m.series_id for m in universe.UNIVERSE}
    for m in universe.fetchable_by_source("cftc"):
        assert f"{m.series_id}.pctile" in ids


def test_tff_lev_money_net_is_long_minus_short():
    rows = [_row("13874A", "2026-10-06", lev_money_positions_long="100",
                 lev_money_positions_short="350", asset_mgr_positions_long="900",
                 asset_mgr_positions_short="10")]
    obs = _adapter().parse(rows, "tff:13874A:lev_money", "pos.cot.es.lev_money", "b")
    assert [o.value for o in obs] == [-250.0]
    obs = _adapter().parse(rows, "tff:13874A:asset_mgr", "pos.cot.es.asset_mgr", "b")
    assert [o.value for o in obs] == [890.0]


def test_disagg_fields_including_the_double_underscore():
    rows = [_row("088691", "2026-10-06", m_money_positions_long_all="5000",
                 m_money_positions_short_all="1200", swap_positions_long_all="7",
                 swap__positions_short_all="9")]
    obs = _adapter().parse(rows, "disagg:088691:m_money", "pos.cot.gold.m_money", "b")
    assert [o.value for o in obs] == [3800.0]
    assert REPORTS["disagg"].groups["swap"] == ("swap_positions_long_all",
                                                "swap__positions_short_all")


def test_as_of_is_the_friday_release_not_the_tuesday():
    rows = [_row("099741", "2026-10-06", lev_money_positions_long="1",
                 lev_money_positions_short="0")]
    (o,) = _adapter().parse(rows, "tff:099741:lev_money", "pos.cot.eur.lev_money", "b")
    assert o.value_date == date(2026, 10, 6)
    assert o.as_of == datetime(2026, 10, 9, 15, 30, tzinfo=NY)
    assert o.as_of_basis == "derived_lag"


def test_a_row_missing_the_groups_fields_is_skipped_not_zeroed():
    rows = [
        _row("13874A", "2026-09-29", lev_money_positions_long="5"),  # short missing
        _row("13874A", "2026-10-06", lev_money_positions_long="5",
             lev_money_positions_short="2"),
    ]
    obs = _adapter().parse(rows, "tff:13874A:lev_money", "pos.cot.es.lev_money", "b")
    assert [(o.value_date, o.value) for o in obs] == [(date(2026, 10, 6), 3.0)]


def test_reassigned_contract_raises():
    rows = [_row("209742", "2026-10-06", noncomm_positions_long_all="1",
                 noncomm_positions_short_all="1")]
    rows[0]["market_and_exchange_names"] = "SOMETHING ELSE - CME"
    with pytest.raises(UnknownSeriesError):
        _adapter().parse(rows, "209742", "pos.cot.nas", "b")


def test_old_names_are_tolerated_when_the_newest_matches():
    rows = [
        _row("209742", "2022-02-01", noncomm_positions_long_all="3",
             noncomm_positions_short_all="1"),
        _row("209742", "2022-02-08", noncomm_positions_long_all="4",
             noncomm_positions_short_all="1"),
    ]
    rows[0]["market_and_exchange_names"] = "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO MERCANTILE EXCHANGE"
    obs = _adapter().parse(rows, "209742", "pos.cot.nas", "b")
    assert [o.value for o in obs] == [2.0, 3.0]


def test_empty_rows_raise():
    with pytest.raises(EmptyFetchError):
        _adapter().parse([], "tff:13874A:lev_money", "pos.cot.es.lev_money", "b")


def test_fetch_downloads_from_the_reports_dataset(monkeypatch):
    seen: list[tuple[str, str]] = []

    def fake_download(url, contract, start, end):
        seen.append((url, contract))
        return [_row(contract, "2026-10-06", m_money_positions_long_all="2",
                     m_money_positions_short_all="1")]

    a = _adapter()
    monkeypatch.setattr(a, "_download", fake_download)
    obs = a.fetch(["disagg:067651:m_money"], date(2026, 1, 1), date(2026, 10, 9), "b")
    assert seen == [(REPORTS["disagg"].url, "067651")]
    assert obs[0].series_id == "pos.cot.crude.m_money"


# T158: the day CFTC actually published, not always the Friday.


@pytest.mark.parametrize("report, published", [
    (date(2026, 10, 6), date(2026, 10, 9)),     # ordinary Tuesday -> Friday
    (date(2024, 11, 26), date(2024, 12, 2)),    # Thanksgiving week: Black Friday -> Monday
    (date(2020, 12, 22), date(2020, 12, 28)),   # Christmas Friday -> Monday
    (date(2022, 11, 8), date(2022, 11, 14)),    # Veterans Day Friday -> Monday
    (date(2004, 11, 9), date(2004, 11, 12)),    # Veterans Day THURSDAY: not Black Friday
    (date(2008, 12, 23), date(2008, 12, 29)),   # executive-order closure, 2008-12-26
    (date(2004, 6, 8), date(2004, 6, 14)),      # national day of mourning, 2004-06-11
    (date(2021, 12, 21), date(2021, 12, 27)),   # Christmas observed Friday 12-24
])
def test_release_date_holidays_push_to_the_next_business_day(report, published):
    assert release_date(report) == published


# CFTC's accelerated schedule of 2025-12-09, the "New Publish Date" column, verbatim, then
# the normal schedule resuming (12-30's report on Monday 01-05, after the 01-02 closure).
CATCH_UP_2025 = {
    "2025-09-30": "2025-11-19", "2025-10-07": "2025-11-21", "2025-10-14": "2025-11-25",
    "2025-10-21": "2025-12-02", "2025-10-28": "2025-12-05", "2025-11-04": "2025-12-09",
    "2025-11-10": "2025-12-10", "2025-11-18": "2025-12-12", "2025-11-25": "2025-12-15",
    "2025-12-02": "2025-12-17", "2025-12-09": "2025-12-19", "2025-12-16": "2025-12-23",
    "2025-12-23": "2025-12-29", "2025-12-30": "2026-01-05", "2026-01-06": "2026-01-09",
}

# CFTC's ION announcements: each names the original publication date it is issuing.
CATCH_UP_2023 = {
    "2023-01-31": "2023-02-24", "2023-02-07": "2023-03-03", "2023-02-14": "2023-03-08",
    "2023-02-21": "2023-03-10", "2023-02-28": "2023-03-14", "2023-03-07": "2023-03-16",
    "2023-03-14": "2023-03-21", "2023-03-21": "2023-03-24",
}


@pytest.mark.parametrize(
    "report, published", sorted({**CATCH_UP_2023, **CATCH_UP_2025}.items())
)
def test_2023_and_2025_catch_up_schedules(report, published):
    assert release_date(date.fromisoformat(report)) == date.fromisoformat(published)


def test_2019_catch_up_runs_tuesday_and_friday_until_current():
    assert release_date(date(2018, 12, 24)) == date(2019, 2, 1)
    assert release_date(date(2019, 2, 26)) == date(2019, 3, 5)
    assert release_date(date(2019, 3, 5)) == date(2019, 3, 8)  # current again
    lo = date(2019, 2, 1)
    assert all(p.weekday() in (1, 4) for r, p in DELAYED_RELEASES.items() if p >= lo
               and p.year == 2019)


def test_a_shutdown_report_off_the_table_gets_the_resume_date():
    # A Wednesday-dated report inside the 2013 window is not in the table.
    assert release_date(date(2013, 10, 16)) == date(2013, 11, 8)


def test_the_corrected_rule_is_never_earlier_than_the_old_friday():
    # The old rule is what the stored rows carry; the migration deletes the rows it
    # made early, so the new date must never precede it.
    d = date(2003, 1, 7)
    while d < date(2027, 1, 1):
        earliest = d + timedelta(days=3)
        old = earliest + timedelta(days=(4 - earliest.weekday()) % 7)
        assert release_date(d) >= old, d
        d += timedelta(weeks=1)


def test_delayed_releases_are_monotone_and_inside_their_windows():
    pairs = sorted(DELAYED_RELEASES.items())
    # Non-decreasing: 2013's are dated to the end of a week, so two can share one.
    assert all(a[1] <= b[1] for a, b in pairwise(pairs))
    for r in DELAYED_RELEASES:
        assert any(lo <= r <= hi for lo, hi, _ in SHUTDOWN_WINDOWS), r


def test_parse_gives_a_shutdown_report_its_catch_up_as_of():
    rows = [_row("088691", "2025-10-07", noncomm_positions_long_all="10",
                 noncomm_positions_short_all="4")]
    (o,) = _adapter().parse(rows, "088691", "pos.cot.gold", "b")
    assert o.as_of == datetime(2025, 11, 21, 15, 30, tzinfo=NY)
