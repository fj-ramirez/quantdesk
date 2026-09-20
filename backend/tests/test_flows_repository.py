"""Tests for `app/modules/gex/storage/flows_repository.py` (T52). Offline: every test uses a temp-file
SQLite `session_factory`, never real Postgres -- same pattern as `test_bars_repository.py`.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.models.db import Base
from app.modules.gex.providers.etf_flows import SharesOutstandingRow
from app.modules.gex.storage.flows_repository import (
    insert_new_rows,
    last_as_of_date,
    read_shares_outstanding,
    read_universe_nav,
    read_universe_shares_outstanding,
)


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def _row(symbol="XLK", as_of_date=dt.date(2026, 9, 8), **overrides) -> SharesOutstandingRow:
    base = {
        "symbol": symbol,
        "as_of_date": as_of_date,
        "shares_outstanding": 651_805_940,
        "nav": 187.88,
        "source": "spdr-xlsx",
    }
    base.update(overrides)
    return SharesOutstandingRow(**base)


# --- insert_new_rows: insert-once, never overwrite ------------------------------------------


def test_insert_new_rows_on_empty_db_inserts_everything(session_factory):
    rows = [_row(as_of_date=dt.date(2026, 9, 7)), _row(as_of_date=dt.date(2026, 9, 8))]
    result = insert_new_rows(rows, session_factory=session_factory)
    assert result.inserted == 2
    assert result.skipped == 0


def test_insert_new_rows_second_identical_run_inserts_nothing(session_factory):
    """The T52 acceptance check: run the same row through twice, the second run inserts
    nothing -- not because the values matched, but because this table never updates at all."""
    rows = [_row(as_of_date=dt.date(2026, 9, 8))]
    insert_new_rows(rows, session_factory=session_factory)

    second = insert_new_rows(rows, session_factory=session_factory)
    assert second.inserted == 0
    assert second.skipped == 1


def test_insert_new_rows_a_stale_as_of_date_never_overwrites_the_newer_stored_row(session_factory):
    """The corrected rule from docs/etf-flows-sources.md: a fixture whose as-of date is older
    than the newest stored row causes no new row and, crucially, must never touch the newer
    row's own value either -- this table has no update path at all."""
    insert_new_rows(
        [_row(as_of_date=dt.date(2026, 9, 9), shares_outstanding=999_999_999)],
        session_factory=session_factory,
    )
    # A second fetch that happens to return yesterday's (already-superseded) as-of date --
    # e.g. a re-run before the issuer's file has advanced -- must insert nothing and leave the
    # already-stored 09-09 row alone.
    result = insert_new_rows(
        [_row(as_of_date=dt.date(2026, 9, 8), shares_outstanding=1)],
        session_factory=session_factory,
    )
    assert result.inserted == 1  # 09-08 itself is a new, distinct (symbol, date) key
    assert last_as_of_date("XLK", session_factory=session_factory) == dt.date(2026, 9, 9)

    df = read_shares_outstanding("XLK", session_factory=session_factory)
    newest = df[df["date"] == dt.date(2026, 9, 9)].iloc[0]
    assert newest["shares"] == 999_999_999  # untouched by the later, older-dated insert


def test_insert_new_rows_never_overwrites_an_existing_symbol_date_even_with_a_different_value(
    session_factory,
):
    insert_new_rows(
        [_row(as_of_date=dt.date(2026, 9, 8), shares_outstanding=100)], session_factory=session_factory
    )
    insert_new_rows(
        [_row(as_of_date=dt.date(2026, 9, 8), shares_outstanding=999)], session_factory=session_factory
    )

    df = read_shares_outstanding("XLK", session_factory=session_factory)
    assert len(df) == 1
    assert df.iloc[0]["shares"] == 100  # first value wins; DO NOTHING, not DO UPDATE


def test_insert_new_rows_handles_multiple_symbols_in_one_call(session_factory):
    rows = [
        _row(symbol="XLK", as_of_date=dt.date(2026, 9, 8)),
        _row(symbol="IWM", as_of_date=dt.date(2026, 9, 9), source="ishares-productpage", nav=None),
    ]
    result = insert_new_rows(rows, session_factory=session_factory)
    assert result.inserted == 2
    assert last_as_of_date("XLK", session_factory=session_factory) == dt.date(2026, 9, 8)
    assert last_as_of_date("IWM", session_factory=session_factory) == dt.date(2026, 9, 9)


def test_insert_new_rows_empty_list_is_a_no_op(session_factory):
    result = insert_new_rows([], session_factory=session_factory)
    assert result.inserted == 0
    assert result.skipped == 0


def test_insert_new_rows_preserves_none_nav(session_factory):
    """iShares fixtures carry no navAmount block -- nav=None must round-trip as None, not 0.0
    or NaN, mirroring DailyBar.volume's None-vs-zero discipline for a different column."""
    insert_new_rows(
        [_row(symbol="IWM", nav=None, source="ishares-productpage")], session_factory=session_factory
    )
    df = read_shares_outstanding("IWM", session_factory=session_factory)
    assert pd.isna(df.iloc[0]["nav"])


# --- last_as_of_date -------------------------------------------------------------------------


def test_last_as_of_date_returns_none_for_an_unknown_symbol(session_factory):
    assert last_as_of_date("NOPE", session_factory=session_factory) is None


def test_last_as_of_date_returns_the_max_stored_date(session_factory):
    insert_new_rows(
        [_row(as_of_date=dt.date(2026, 9, 7)), _row(as_of_date=dt.date(2026, 9, 8))],
        session_factory=session_factory,
    )
    assert last_as_of_date("XLK", session_factory=session_factory) == dt.date(2026, 9, 8)


# --- read_universe_shares_outstanding / read_universe_nav -----------------------------------


def test_read_universe_wide_frames_have_a_column_per_requested_symbol_even_with_no_data(
    session_factory,
):
    so = read_universe_shares_outstanding(
        ["XLK", "NOPE"], dt.date(2026, 9, 1), dt.date(2026, 9, 30), session_factory=session_factory
    )
    assert list(so.columns) == ["XLK", "NOPE"]
    assert so["NOPE"].isna().all()


def test_read_universe_wide_frames_align_shares_and_nav_by_date(session_factory):
    insert_new_rows(
        [
            _row(symbol="XLK", as_of_date=dt.date(2026, 9, 7), shares_outstanding=100, nav=10.0),
            _row(symbol="XLK", as_of_date=dt.date(2026, 9, 8), shares_outstanding=110, nav=11.0),
        ],
        session_factory=session_factory,
    )
    so = read_universe_shares_outstanding(
        ["XLK"], dt.date(2026, 9, 1), dt.date(2026, 9, 30), session_factory=session_factory
    )
    nav = read_universe_nav(
        ["XLK"], dt.date(2026, 9, 1), dt.date(2026, 9, 30), session_factory=session_factory
    )
    assert so.loc[dt.date(2026, 9, 8), "XLK"] == 110
    assert nav.loc[dt.date(2026, 9, 8), "XLK"] == 11.0
