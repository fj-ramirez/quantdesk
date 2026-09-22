"""The ETF-proxy prices adapter (T91).

Offline: the adapter's one dependency is a `SELECT` against `gex.daily_bars`, so the store is
faked and what the adapter *asks for* is asserted alongside what it returns. The query text
matters as much as the rows here -- `store.db.connect()` pins `search_path` to the terminal
schema, so an unqualified `daily_bars` finds nothing at all, and that is a failure no
in-memory test of the parsing would catch.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.modules.terminal import cli, universe
from app.modules.terminal.adapters import prices as prices_module
from app.modules.terminal.adapters.prices import PricesAdapter
from app.modules.terminal.errors import EmptyFetchError, UnknownSeriesError

SERIES_MAP = {"IWM": "eq.rut", "EEM": "eq.msci_em", "GLD": "cmdty.gold"}

BARS = [
    (dt.date(2026, 9, 17), 244.11),
    (dt.date(2026, 9, 18), 246.02),
    (dt.date(2026, 9, 21), 245.5),
]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows, log):
        self._rows = rows
        self._log = log

    def execute(self, sql, params=None):
        self._log.append((sql, params))
        return _FakeCursor(self._rows)


class _FakeStore:
    def __init__(self, rows, log):
        self.conn = _FakeConn(rows, log)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def store_calls(monkeypatch):
    """Patches the Store the adapter opens and records every query it ran."""
    calls: list[tuple[str, list]] = []
    rows: list[tuple] = list(BARS)

    monkeypatch.setattr(prices_module, "Store", lambda **kw: _FakeStore(rows, calls))
    return calls, rows


def test_bars_become_observations_with_a_derived_lag_basis(store_calls):
    adapter = PricesAdapter(SERIES_MAP)

    obs = adapter.fetch(["IWM"], dt.date(2026, 9, 1), dt.date(2026, 9, 21), "batch-1")

    assert [o.value for o in obs] == [244.11, 246.02, 245.5]
    assert {o.series_id for o in obs} == {"eq.rut"}
    assert {o.as_of_basis for o in obs} == {"derived_lag"}
    # Every observation is dated at the bars job's 17:30 ET run, tz-aware. Not the 16:00
    # close: the value does not exist in this table until the job that writes it has run,
    # and an as_of may never claim knowledge earlier than the knowledge existed.
    assert all(o.as_of.tzinfo is not None for o in obs)
    assert {o.as_of.hour for o in obs} == {17}
    assert {o.as_of.minute for o in obs} == {30}
    assert {str(o.as_of.tzinfo) for o in obs} == {"America/New_York"}
    # `Observation.__post_init__` demands the exact type, so this also guards the frame
    # boundary the terminal store crosses.
    assert all(type(o.value_date) is dt.date for o in obs)
    assert {o.source_batch for o in obs} == {"batch-1"}


def test_the_query_is_schema_qualified_and_bounded(store_calls):
    """The documented first-contact failure, asserted rather than commented."""
    calls, _ = store_calls
    PricesAdapter(SERIES_MAP).fetch(
        ["GLD"], dt.date(2026, 1, 1), dt.date(2026, 9, 21), "batch-1"
    )

    sql, params = calls[0]
    assert "gex.daily_bars" in sql
    assert "FROM daily_bars" not in sql
    assert params == ["GLD", dt.date(2026, 1, 1), dt.date(2026, 9, 21)]


def test_an_empty_range_raises_rather_than_returning_nothing(store_calls):
    """The adapter contract: a silently truncated series shows up later as an inexplicable
    z-score, so an empty result is an error at the point it happens."""
    _, rows = store_calls
    rows.clear()

    with pytest.raises(EmptyFetchError, match="daily_bars"):
        PricesAdapter(SERIES_MAP).fetch(
            ["IWM"], dt.date(2026, 9, 1), dt.date(2026, 9, 21), "batch-1"
        )


def test_a_null_close_is_skipped_not_stored_as_a_number(store_calls):
    """Spec 0.5: a missing value is an absent row, never a stored NaN or a zero."""
    _, rows = store_calls
    rows.insert(1, (dt.date(2026, 9, 18), None))
    rows.pop(2)

    obs = PricesAdapter(SERIES_MAP).fetch(
        ["IWM"], dt.date(2026, 9, 1), dt.date(2026, 9, 21), "batch-1"
    )

    assert [o.value_date for o in obs] == [dt.date(2026, 9, 17), dt.date(2026, 9, 21)]


def test_an_unknown_symbol_says_where_a_new_one_has_to_be_added():
    with pytest.raises(UnknownSeriesError, match="SCAN_UNIVERSE"):
        PricesAdapter(SERIES_MAP).resolve("SPY")


def test_the_universe_maps_the_three_proxies_and_nothing_codeless():
    """`prices` was the eventual source of five pending series, three of which this adapter
    can fill. The other two have no code, and handing the adapter the symbol `""` would fail
    the whole source's batch for series that were never going to load."""
    assert universe.series_map("prices") == SERIES_MAP

    codeless = {m.series_id for m in universe.by_source("prices") if not m.source_code}
    assert codeless == {"eq.sx5e", "fx.usdcnh"}
    assert codeless.isdisjoint({m.series_id for m in universe.fetchable()})


def test_each_proxy_series_records_that_it_is_a_proxy():
    """An ETF close is not the index level, and a reader must not have to infer that."""
    for series_id, symbol, tracked in (
        ("eq.rut", "IWM", "the Russell 2000"),
        ("eq.msci_em", "EEM", "MSCI EM"),
        ("cmdty.gold", "GLD", "gold spot"),
    ):
        meta = next(m for m in universe.UNIVERSE if m.series_id == series_id)
        assert meta.source == "prices"
        assert meta.source_code == symbol
        assert symbol in meta.display_name
        assert symbol in (meta.notes or "")
        assert tracked in (meta.notes or "")
        assert meta.vintage_source == "derived_lag"
        assert meta.snapshot_local_time == "17:30"


@pytest.mark.parametrize("source", sorted(universe.FETCHABLE_SOURCES))
def test_every_fetchable_source_is_accepted_by_the_ingest_flag(source, monkeypatch):
    """`--source` used to restate the list, so T91's new source ran perfectly well under
    `--source all` and was rejected outright when named -- a discrepancy nothing else would
    have caught, and one that returns the moment someone adds the sixth source.

    The handler is stubbed: this is about argparse accepting the value, and running a real
    ingest here would fetch from four vendors.
    """
    seen: list[str] = []

    def _stub(args, settings):
        seen.append(args.source)
        return 0

    monkeypatch.setattr(cli, "cmd_ingest", _stub)
    monkeypatch.setattr(cli, "configure", lambda level: None)
    monkeypatch.setattr(cli, "load_settings", lambda: SimpleNamespace(log_level="INFO"))

    assert cli.main(["ingest", "--source", source]) == 0
    assert seen == [source]
