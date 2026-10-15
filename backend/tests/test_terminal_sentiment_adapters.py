"""The three sentiment-plan adapters (T168-T170): Cboe put/call, AAII, the desk's own gamma.

Offline. Fixtures carry the shapes the live sources returned on 2026-10-15 (plans/sentiment,
*Verified facts*): the Cboe CSV's disclaimer and product lines above the header, the daily
JSON's string values, S3's 403 for a day with no file, AAII's letterhead rows and trailing
yearly counts.
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest

from app.modules.terminal import cli, universe
from app.modules.terminal.adapters import aaii as aaii_module
from app.modules.terminal.adapters import cboe_pc as cboe_pc_module
from app.modules.terminal.adapters import desk_gex as desk_gex_module
from app.modules.terminal.adapters.aaii import AaiiAdapter
from app.modules.terminal.adapters.cboe_pc import CboePutCallAdapter
from app.modules.terminal.adapters.desk_gex import DeskGexAdapter, parse_code
from app.modules.terminal.errors import EmptyFetchError, UnknownSeriesError

CSV = (
    "Cboe Volume and Put/Call Ratio data is compiled for the convenience of site visitors\n"
    ", PRODUCT: EQUITY,,EXCHANGE: Cboe,\n"
    "DATE,CALL,PUT,TOTAL,P/C Ratio\n"
    "10/03/2019, 847645, 656572, 1504217, 0.77\n"
    "10/04/2019, 916877, 598296, 1515173, 0.65\n"
)


def _payload(equity: str) -> dict:
    return {"ratios": [
        {"name": "TOTAL PUT/CALL RATIO", "value": "0.88"},
        {"name": "INDEX PUT/CALL RATIO", "value": "1.01"},
        {"name": "EQUITY PUT/CALL RATIO", "value": equity},
    ]}


def _transport(daily: dict[str, dict], seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url.endswith(".csv"):
            return httpx.Response(200, text=CSV)
        day = url.rsplit("/", 1)[1].split("_", 1)[0]
        if day in daily:
            return httpx.Response(200, json=daily[day])
        return httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>")
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(cboe_pc_module, "REQUEST_PAUSE_SECONDS", 0)


def _cboe(tmp_path, daily, seen):
    a = CboePutCallAdapter(universe.series_map("cboe_pc"), cache_dir=tmp_path)
    a.client = httpx.Client(transport=_transport(daily, seen))
    return a


def test_csv_and_daily_json_join_at_the_seam(tmp_path):
    seen: list[str] = []
    a = _cboe(tmp_path, {"2019-10-07": _payload("0.70"), "2019-10-08": _payload("0.65")}, seen)

    obs = a.fetch(["EQUITY"], dt.date(2019, 10, 3), dt.date(2019, 10, 8), "b")

    assert [(o.value_date, o.value) for o in obs] == [
        (dt.date(2019, 10, 3), 0.77), (dt.date(2019, 10, 4), 0.65),
        (dt.date(2019, 10, 7), 0.70), (dt.date(2019, 10, 8), 0.65),
    ]
    assert {o.series_id for o in obs} == {"sent.pc.equity"}
    # Published the same evening, placed at 06:00 ET the next day.
    first = obs[0].as_of
    assert (first.date(), first.hour, str(first.tzinfo)) == (
        dt.date(2019, 10, 4), 6, "America/New_York")


def test_weekends_are_never_requested_and_old_403s_are_cached_as_missing(tmp_path):
    seen: list[str] = []
    a = _cboe(tmp_path, {"2020-06-01": _payload("0.5")}, seen)
    # 2020-05-29 Fri has no file here (stands in for a holiday); 05-30/31 are a weekend.
    a.fetch(["EQUITY"], dt.date(2020, 5, 29), dt.date(2020, 6, 1), "b")
    daily = [u for u in seen if "daily_options" in u]
    assert len(daily) == 2
    assert json.loads((tmp_path / "2020-05-29.json").read_text()) == {"missing": True}

    seen.clear()
    b = _cboe(tmp_path, {}, seen)
    b.fetch(["EQUITY"], dt.date(2020, 5, 29), dt.date(2020, 6, 1), "b")
    assert not [u for u in seen if "daily_options" in u], "settled days come from the cache"


def test_a_recent_403_is_retried_next_run_not_cached(tmp_path):
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    seen: list[str] = []
    a = _cboe(tmp_path, {}, seen)
    with pytest.raises(EmptyFetchError):
        a.fetch(["EQUITY"], today - dt.timedelta(days=3), today, "b")
    assert not list(tmp_path.iterdir())


def test_three_series_share_one_set_of_daily_requests(tmp_path):
    seen: list[str] = []
    a = _cboe(tmp_path, {"2020-06-01": _payload("0.5")}, seen)
    for code in ("EQUITY", "TOTAL", "INDEX"):
        a.fetch([code], dt.date(2020, 6, 1), dt.date(2020, 6, 1), "b")
    assert len([u for u in seen if "daily_options" in u]) == 1


def test_a_renamed_ratio_fails_loudly(tmp_path):
    seen: list[str] = []
    a = _cboe(tmp_path, {"2020-06-01": {"ratios": [{"name": "SOMETHING", "value": "1"}]}}, seen)
    with pytest.raises(EmptyFetchError, match="layout changed"):
        a.fetch(["EQUITY"], dt.date(2020, 6, 1), dt.date(2020, 6, 1), "b")


def test_unknown_ratio_raises():
    with pytest.raises(UnknownSeriesError):
        CboePutCallAdapter(universe.series_map("cboe_pc")).resolve("VIX")


# --- AAII ------------------------------------------------------------------------------


def _aaii_sheet() -> pd.DataFrame:
    """The layout of sentiment.xls: letterhead, a header pair, weekly rows, yearly counts."""
    return pd.DataFrame([
        [None, None, None, "American Association of Individual Investors", None],
        ["Reported", None, None, None, None],
        ["Date", "Bullish", "Neutral", "Bearish", "Total"],
        [pd.Timestamp("1987-06-26"), None, None, None, None],
        [pd.Timestamp("2026-10-01"), 0.346491, 0.188596, 0.464912, 1.0],
        [pd.Timestamp("2026-10-08"), 0.402524, 0.207627, 0.389831, 1.0],
        ["Count '25", 52, 52, 52, None],
    ])


def test_aaii_rows_in_percent_dated_thursday_noon(monkeypatch):
    monkeypatch.setattr(aaii_module.pd, "read_excel", lambda *a, **k: _aaii_sheet())
    frame = AaiiAdapter.read_sheet(b"")
    a = AaiiAdapter(universe.series_map("aaii"))
    obs = a.parse(frame, "BULLISH", "sent.aaii.bull", dt.date(1980, 1, 1),
                  dt.date(2026, 12, 31), "b")

    # The blank 1987 week and the count row are gone; values are percent.
    assert [(o.value_date, o.value) for o in obs] == [
        (dt.date(2026, 10, 1), 34.6491), (dt.date(2026, 10, 8), 40.2524)]
    assert (obs[-1].as_of.hour, str(obs[-1].as_of.tzinfo)) == (12, "America/New_York")
    assert obs[-1].as_of.date() == dt.date(2026, 10, 8)


def test_aaii_fetch_downloads_once_for_three_series(monkeypatch):
    monkeypatch.setattr(aaii_module.pd, "read_excel", lambda *a, **k: _aaii_sheet())
    calls: list[str] = []
    a = AaiiAdapter(universe.series_map("aaii"))
    a.client = httpx.Client(transport=httpx.MockTransport(
        lambda r: calls.append(str(r.url)) or httpx.Response(200, content=b"xls")))
    for code in ("BULLISH", "NEUTRAL", "BEARISH"):
        a.fetch([code], dt.date(2026, 1, 1), dt.date(2026, 12, 31), "b")
    assert len(calls) == 1


def test_aaii_layout_change_fails_loudly(monkeypatch):
    monkeypatch.setattr(aaii_module.pd, "read_excel",
                        lambda *a, **k: pd.DataFrame([["no dates here", 1, 2, 3]]))
    with pytest.raises(EmptyFetchError, match="layout changed"):
        AaiiAdapter.read_sheet(b"")


# --- desk gamma ------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, rows, log):
        self.conn = SimpleNamespace(
            execute=lambda sql, params=None: log.append((sql, params))
            or SimpleNamespace(fetchall=lambda: rows))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


COMPUTED = dt.datetime(2026, 10, 14, 20, 21, tzinfo=dt.UTC)


def test_desk_gamma_reads_eod_all_levels_schema_qualified(monkeypatch):
    log: list = []
    rows = [(dt.date(2026, 10, 14), COMPUTED, -1.5e9, 6600.0, 6534.0),
            (dt.date(2026, 10, 13), COMPUTED, None, None, 6500.0)]
    monkeypatch.setattr(desk_gex_module, "Store", lambda **kw: _FakeStore(rows, log))
    a = DeskGexAdapter(universe.series_map("desk_gex"))

    net = a.fetch(["SPX:net"], dt.date(2026, 10, 1), dt.date(2026, 10, 14), "b")
    flip = a.fetch(["SPX:flip_dist"], dt.date(2026, 10, 1), dt.date(2026, 10, 14), "b")

    sql, params = log[0]
    assert "gex.snapshots" in sql and "gex.gex_levels" in sql and "is_eod" in sql
    assert params[:2] == ["SPX", "ALL"]
    # Unknown inputs are skipped, never stored as 0 (invariant 3's spirit).
    assert [(o.series_id, o.value) for o in net] == [("desk.gex.spx.net", -1.5e9)]
    assert flip[0].series_id == "desk.gex.spx.flip_dist"
    assert flip[0].value == pytest.approx((6534.0 - 6600.0) / 6534.0 * 100)
    assert net[0].as_of == COMPUTED


def test_desk_gamma_empty_raises(monkeypatch):
    monkeypatch.setattr(desk_gex_module, "Store", lambda **kw: _FakeStore([], []))
    with pytest.raises(EmptyFetchError, match="gex_levels"):
        DeskGexAdapter(universe.series_map("desk_gex")).fetch(
            ["QQQ:net"], dt.date(2026, 10, 1), dt.date(2026, 10, 14), "b")


@pytest.mark.parametrize("bad", ["SPX", "SPX:gamma", ":net"])
def test_desk_gamma_bad_codes(bad):
    with pytest.raises(UnknownSeriesError):
        parse_code(bad)


# --- registration ----------------------------------------------------------------------


def test_every_new_source_builds_and_maps_its_series():
    settings = cli.load_settings()
    for source, ids in {
        "cboe_pc": {"sent.pc.total", "sent.pc.equity", "sent.pc.index"},
        "aaii": {"sent.aaii.bull", "sent.aaii.neutral", "sent.aaii.bear"},
        "desk_gex": {"desk.gex.spx.net", "desk.gex.spx.flip_dist",
                     "desk.gex.qqq.net", "desk.gex.qqq.flip_dist"},
    }.items():
        assert source in universe.FETCHABLE_SOURCES
        assert set(universe.series_map(source).values()) == ids
        adapter = cli.build_adapter(source, settings)
        try:
            for code in universe.series_map(source):
                adapter.resolve(code)
        finally:
            adapter.close()
