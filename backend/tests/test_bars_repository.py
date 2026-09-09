"""Tests for `app/storage/bars_repository.py`. Offline: every test uses a temp-file SQLite
`session_factory`, never real Postgres -- same pattern as `test_snapshot_repository.py`.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.models.bars import DailyBar as DailyBarIn
from app.models.db import Base, get_engine, get_sessionmaker
from app.storage.bars_repository import (
    last_bar_date,
    read_bars,
    read_universe_closes,
    upsert_bars,
)


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def _bar(symbol="SPY", date=dt.date(2026, 9, 4), **overrides) -> DailyBarIn:
    base = {
        "symbol": symbol,
        "date": date,
        "open": 650.0,
        "high": 655.0,
        "low": 648.0,
        "close": 652.0,
        "volume": 44_000_000,
        "source": "yahoo-splitadj",
    }
    base.update(overrides)
    return DailyBarIn(**base)


# --- upsert_bars: insert, idempotence, revision -----------------------------------------------


def test_upsert_bars_on_empty_db_inserts_everything(session_factory):
    bars = [
        _bar(date=dt.date(2026, 9, 2)),
        _bar(date=dt.date(2026, 9, 3)),
        _bar(date=dt.date(2026, 9, 4)),
    ]
    result = upsert_bars(bars, session_factory=session_factory)
    assert result.inserted == 3
    assert result.updated == 0


def test_upsert_bars_second_identical_run_inserts_zero_new_rows(session_factory):
    """The T42 acceptance check: run the same bars through twice, the second run inserts
    nothing new (though it still "updates" every row, since upsert_bars always writes)."""
    bars = [_bar(date=dt.date(2026, 9, 2)), _bar(date=dt.date(2026, 9, 3))]
    upsert_bars(bars, session_factory=session_factory)

    second = upsert_bars(bars, session_factory=session_factory)
    assert second.inserted == 0
    assert second.updated == 2


def test_upsert_bars_overwrites_existing_row_values(session_factory):
    """Picking up a vendor revision (plan design decision: 'fetch from last stored date minus
    5 days to pick up vendor revisions') means a re-upsert must change the stored value, not
    silently keep the old one."""
    upsert_bars([_bar(date=dt.date(2026, 9, 4), close=652.0)], session_factory=session_factory)
    upsert_bars([_bar(date=dt.date(2026, 9, 4), close=999.0)], session_factory=session_factory)

    df = read_bars("SPY", session_factory=session_factory)
    assert df.iloc[0]["close"] == pytest.approx(999.0)


def test_upsert_bars_handles_multiple_symbols_in_one_call(session_factory):
    bars = [
        _bar(symbol="SPY", date=dt.date(2026, 9, 4)),
        _bar(symbol="QQQ", date=dt.date(2026, 9, 4)),
    ]
    result = upsert_bars(bars, session_factory=session_factory)
    assert result.inserted == 2
    assert last_bar_date("SPY", session_factory=session_factory) == dt.date(2026, 9, 4)
    assert last_bar_date("QQQ", session_factory=session_factory) == dt.date(2026, 9, 4)


def test_upsert_bars_empty_list_is_a_no_op(session_factory):
    result = upsert_bars([], session_factory=session_factory)
    assert result.inserted == 0
    assert result.updated == 0


def test_upsert_bars_preserves_none_vs_zero_volume_distinction(session_factory):
    """^VIX volume 0 must round-trip as 0, not None; a symbol with genuinely unknown volume
    must round-trip as None, not 0. CLAUDE.md invariant 3, applied to volume."""
    upsert_bars(
        [
            _bar(symbol="^VIX", date=dt.date(2026, 9, 4), volume=0),
            _bar(symbol="UNKNOWNVOL", date=dt.date(2026, 9, 4), volume=None),
        ],
        session_factory=session_factory,
    )

    vix_df = read_bars("^VIX", session_factory=session_factory)
    unknown_df = read_bars("UNKNOWNVOL", session_factory=session_factory)
    assert vix_df.iloc[0]["volume"] == 0
    assert pd.isna(unknown_df.iloc[0]["volume"])


# --- last_bar_date -------------------------------------------------------------------------


def test_last_bar_date_none_for_unknown_symbol(session_factory):
    assert last_bar_date("NOPE", session_factory=session_factory) is None


def test_last_bar_date_returns_the_max_date(session_factory):
    upsert_bars(
        [_bar(date=dt.date(2026, 9, 2)), _bar(date=dt.date(2026, 9, 4)), _bar(date=dt.date(2026, 9, 3))],
        session_factory=session_factory,
    )
    assert last_bar_date("SPY", session_factory=session_factory) == dt.date(2026, 9, 4)


# --- read_bars: shape, ordering, the Windows/SQLite date round-trip hazard --------------------


def test_read_bars_empty_symbol_returns_empty_dataframe_with_expected_columns(session_factory):
    df = read_bars("NOPE", session_factory=session_factory)
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "source"]
    assert len(df) == 0


def test_read_bars_date_column_round_trips_as_a_real_date_not_a_string(session_factory):
    """The plan's explicitly-flagged Windows/SQLite hazard: assert the *type*, not merely
    the value."""
    upsert_bars([_bar(date=dt.date(2026, 9, 4))], session_factory=session_factory)
    df = read_bars("SPY", session_factory=session_factory)
    value = df.iloc[0]["date"]
    assert isinstance(value, dt.date)
    assert not isinstance(value, str)
    assert value == dt.date(2026, 9, 4)


def test_read_bars_is_ascending_by_date(session_factory):
    upsert_bars(
        [_bar(date=dt.date(2026, 9, 4)), _bar(date=dt.date(2026, 9, 2)), _bar(date=dt.date(2026, 9, 3))],
        session_factory=session_factory,
    )
    df = read_bars("SPY", session_factory=session_factory)
    assert list(df["date"]) == [dt.date(2026, 9, 2), dt.date(2026, 9, 3), dt.date(2026, 9, 4)]


def test_read_bars_respects_start_and_end_bounds(session_factory):
    upsert_bars(
        [_bar(date=dt.date(2026, 9, i)) for i in range(2, 6)], session_factory=session_factory
    )
    df = read_bars(
        "SPY", start=dt.date(2026, 9, 3), end=dt.date(2026, 9, 4), session_factory=session_factory
    )
    assert list(df["date"]) == [dt.date(2026, 9, 3), dt.date(2026, 9, 4)]


# --- read_universe_closes: wide frame -----------------------------------------------------


def test_read_universe_closes_returns_wide_frame(session_factory):
    upsert_bars(
        [
            _bar(symbol="SPY", date=dt.date(2026, 9, 3), close=650.0),
            _bar(symbol="SPY", date=dt.date(2026, 9, 4), close=651.0),
            _bar(symbol="QQQ", date=dt.date(2026, 9, 3), close=550.0),
            _bar(symbol="QQQ", date=dt.date(2026, 9, 4), close=551.0),
        ],
        session_factory=session_factory,
    )

    wide = read_universe_closes(
        ["SPY", "QQQ"], dt.date(2026, 9, 1), dt.date(2026, 9, 30), session_factory=session_factory
    )

    assert list(wide.columns) == ["SPY", "QQQ"]
    assert wide.loc[dt.date(2026, 9, 3), "SPY"] == pytest.approx(650.0)
    assert wide.loc[dt.date(2026, 9, 4), "QQQ"] == pytest.approx(551.0)


def test_read_universe_closes_fills_na_for_a_symbol_with_no_bars(session_factory):
    """A symbol newly added to SCAN_UNIVERSE before its first bars job run must appear as a
    column of NA, not raise a KeyError when a caller indexes it."""
    upsert_bars([_bar(symbol="SPY", date=dt.date(2026, 9, 3))], session_factory=session_factory)

    wide = read_universe_closes(
        ["SPY", "BRAND_NEW"],
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 30),
        session_factory=session_factory,
    )
    assert "BRAND_NEW" in wide.columns
    assert wide["BRAND_NEW"].isna().all()


def test_read_universe_closes_with_zero_matching_rows_still_has_every_column(session_factory):
    wide = read_universe_closes(
        ["SPY", "QQQ"], dt.date(2026, 9, 1), dt.date(2026, 9, 30), session_factory=session_factory
    )
    assert list(wide.columns) == ["SPY", "QQQ"]
    assert len(wide) == 0


def test_volume_column_is_64_bit_under_postgres():
    """SPX/^GSPC volume (~4.97e9) overflows Postgres's int4 ceiling of 2_147_483_647.

    This assertion exists because the whole suite runs on SQLite, whose INTEGER is 64-bit and
    therefore accepts a value real Postgres rejects with `NumericValueOutOfRange` -- the first
    live SPX backfill failed on exactly that after every test here passed. Compiling the column
    against the Postgres dialect is the cheapest way to pin the production type without a
    running server. Same class of SQLite-hides-a-Postgres-error hazard as `UTCDateTime`.
    """
    from sqlalchemy.dialects import postgresql

    from app.models.db import DailyBar

    compiled = DailyBar.__table__.c.volume.type.compile(dialect=postgresql.dialect())
    assert compiled == "BIGINT"


def test_a_bar_with_index_scale_volume_round_trips(session_factory):
    """The value that actually broke: one ^GSPC-sized volume through upsert and back."""
    spx_volume = 4_966_930_000
    assert spx_volume > 2_147_483_647  # would not fit in int4
    upsert_bars(
        [
            DailyBarIn(
                symbol="SPX",
                date=dt.date(2026, 9, 8),
                open=7717.81,
                high=7717.81,
                low=7666.99,
                close=7673.52,
                volume=spx_volume,
                source="yahoo-splitadj",
            )
        ],
        session_factory=session_factory,
    )
    df = read_bars("SPX", session_factory=session_factory)
    assert int(df.iloc[0]["volume"]) == spx_volume
