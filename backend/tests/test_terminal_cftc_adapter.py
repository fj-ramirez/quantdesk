"""Contract tests for the CFTC adapter (T155): source codes, the three reports' fields, as_of.

The row fixtures carry the field names the live API returned on 2026-10-13, including the
Disaggregated report's `swap__positions_short_all` with its double underscore.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.modules.terminal import universe
from app.modules.terminal.adapters.cftc import (
    KNOWN_CONTRACTS,
    REPORTS,
    CftcAdapter,
    SourceCode,
    parse_source_code,
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
