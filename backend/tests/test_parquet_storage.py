"""Round-trip tests for `app/storage/parquet.py`.

No fixture data exists yet (the Cboe provider, T02, is being built concurrently in a
different worktree) so every snapshot here is constructed by hand. The "nasty" fixture is
deliberately uglier than anything `test_chain_models.py` uses: mixed SPX/SPXW roots on the
same expiry/strike/right, a mix of populated and all-`None` optional columns, `gamma=0.0` as
a genuine value, and several distinct expiries -- because those are exactly the conditions
under which pyarrow's dtype *inference* (as opposed to the explicit schema this module uses)
would go wrong, and where `None` silently turning into `0`/`NaN` would corrupt GEX totals
without any visible symptom (see `app/models/chain.py`'s module docstring).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.models.chain import ChainSnapshot, OptionContract, Underlying
from app.storage.parquet import _encode_captured_at, read_snapshot, write_snapshot

NY = ZoneInfo("America/New_York")


def make_contract(symbol: str, **kw) -> OptionContract:
    return OptionContract.from_occ(symbol, **kw)


def nasty_spx_snapshot(captured_at: dt.datetime | None = None) -> ChainSnapshot:
    """SPX snapshot with SPX/SPXW roots colliding on (expiry, strike, right), a spread of
    optional-field states, and several distinct expiries -- see module docstring."""
    captured_at = captured_at or dt.datetime(2026, 9, 4, 18, 5, 33, 123456, tzinfo=dt.UTC)
    contracts = [
        # Fully populated AM-settled SPX contract.
        make_contract(
            "SPX260918C07710000",
            bid=120.1,
            ask=120.9,
            last=120.5,
            volume=42,
            open_interest=1288,
            iv=0.1064,
            delta=0.52,
            gamma=0.0021,
            vega=1.9,
            theta=-3.2,
            last_trade_time=dt.datetime(2026, 9, 4, 13, 50, 31, tzinfo=NY),
        ),
        # Same expiry/strike/right, PM-settled SPXW root -- must not collide with the above.
        make_contract(
            "SPXW260918C07710000",
            bid=118.0,
            ask=118.8,
            last=None,
            volume=None,
            open_interest=1288,
            iv=0.1080,
            delta=0.50,
            gamma=0.0021,
            vega=1.85,
            theta=-3.0,
        ),
        # Genuinely zero open interest -- must read back as 0, not None.
        make_contract(
            "SPXW260904P07700000",
            open_interest=0,
            iv=0.14,
            delta=-0.31,
            gamma=0.0,  # a real vendor value, not a sentinel
            vega=0.4,
            theta=-1.1,
        ),
        # Every optional field unknown: the "vendor never reported this" case.
        make_contract("SPX261016C08000000"),
        # gamma present and exactly 0.0 alongside other fields fully None.
        make_contract("SPXW261016P06000000", gamma=0.0, open_interest=None, iv=None),
        # Deep ITM extreme IV, per docs/schema.md's note that these are real, not a bug.
        make_contract("SPX270617C01000000", iv=8.3191, open_interest=3, gamma=0.5),
    ]
    return ChainSnapshot(
        underlying=Underlying.SPX,
        spot=7709.52,
        captured_at=captured_at,
        source="cboe",
        delayed_minutes=15,
        contracts=contracts,
    )


def test_round_trip_preserves_full_snapshot_equality(tmp_path: Path):
    snap = nasty_spx_snapshot()
    path = write_snapshot(snap, data_dir=tmp_path)
    result = read_snapshot(path)
    assert result == snap


def test_round_trip_preserves_none_vs_zero_open_interest(tmp_path: Path):
    snap = nasty_spx_snapshot()
    result = read_snapshot(write_snapshot(snap, data_dir=tmp_path))
    by_symbol = {c.occ_symbol: c for c in result.contracts}

    zero_oi = by_symbol["SPXW260904P07700000"]
    unknown_oi = by_symbol["SPX261016C08000000"]
    assert zero_oi.open_interest == 0
    assert zero_oi.open_interest is not None
    assert unknown_oi.open_interest is None


def test_round_trip_preserves_gamma_zero_as_a_real_value_not_none(tmp_path: Path):
    snap = nasty_spx_snapshot()
    result = read_snapshot(write_snapshot(snap, data_dir=tmp_path))
    by_symbol = {c.occ_symbol: c for c in result.contracts}

    assert by_symbol["SPXW260904P07700000"].gamma == 0.0
    assert by_symbol["SPXW260904P07700000"].gamma is not None
    assert by_symbol["SPX261016C08000000"].gamma is None


def test_round_trip_preserves_last_trade_time_none_and_aware(tmp_path: Path):
    snap = nasty_spx_snapshot()
    result = read_snapshot(write_snapshot(snap, data_dir=tmp_path))
    by_symbol = {c.occ_symbol: c for c in result.contracts}

    with_trade = by_symbol["SPX260918C07710000"]
    assert with_trade.last_trade_time == dt.datetime(2026, 9, 4, 17, 50, 31, tzinfo=dt.UTC)
    assert with_trade.last_trade_time.tzinfo is not None
    assert by_symbol["SPXW260918C07710000"].last_trade_time is None


def test_round_trip_preserves_spx_spxw_collision_on_expiry_strike_right(tmp_path: Path):
    """The natural key without `root` is not unique on this fixture; storage must not dedupe
    or otherwise merge these two rows."""
    snap = nasty_spx_snapshot()
    result = read_snapshot(write_snapshot(snap, data_dir=tmp_path))
    assert len(result) == len(snap)
    assert result.roots == ("SPX", "SPXW")
    am = next(c for c in result.contracts if c.occ_symbol == "SPX260918C07710000")
    pm = next(c for c in result.contracts if c.occ_symbol == "SPXW260918C07710000")
    assert (am.expiry, am.strike, am.right) == (pm.expiry, pm.strike, pm.right)
    assert am.root != pm.root


def test_round_trip_preserves_captured_at_instant_and_utc_tzinfo(tmp_path: Path):
    captured_at = dt.datetime(2026, 9, 4, 14, 5, 33, 987654, tzinfo=NY)
    snap = nasty_spx_snapshot(captured_at)
    result = read_snapshot(write_snapshot(snap, data_dir=tmp_path))
    assert result.captured_at == snap.captured_at
    assert result.captured_at.tzinfo is dt.UTC


def test_write_snapshot_layout_and_metadata_driven_read(tmp_path: Path):
    snap = nasty_spx_snapshot()
    path = write_snapshot(snap, data_dir=tmp_path)

    expected_dir = tmp_path / "chains" / "SPX" / "2026" / "09"
    assert path.parent == expected_dir
    assert path.suffix == ".parquet"
    assert ":" not in path.name  # illegal in a Windows filename

    # Renaming/relocating the file must not matter: only the embedded metadata is read.
    moved = tmp_path / "elsewhere.parquet"
    path.replace(moved)
    result = read_snapshot(moved)
    assert result == snap


def test_encoded_filenames_sort_chronologically():
    earlier = dt.datetime(2026, 9, 4, 9, 0, 0, tzinfo=dt.UTC)
    later_same_second = dt.datetime(2026, 9, 4, 9, 0, 0, 500000, tzinfo=dt.UTC)
    later = dt.datetime(2026, 9, 4, 9, 15, 0, tzinfo=dt.UTC)
    names = [
        _encode_captured_at(later),
        _encode_captured_at(earlier),
        _encode_captured_at(later_same_second),
    ]
    assert sorted(names) == [
        _encode_captured_at(earlier),
        _encode_captured_at(later_same_second),
        _encode_captured_at(later),
    ]
    assert all(":" not in n for n in names)


def test_write_snapshot_with_no_contracts(tmp_path: Path):
    """An empty chain (e.g. a symbol with a temporary vendor outage on a single expiry)
    should still write and read back rather than crashing on empty-column type inference."""
    snap = ChainSnapshot(
        underlying=Underlying.QQQ,
        spot=480.12,
        captured_at=dt.datetime(2026, 9, 4, 20, 0, 0, tzinfo=dt.UTC),
        source="cboe",
        delayed_minutes=15,
        contracts=(),
    )
    result = read_snapshot(write_snapshot(snap, data_dir=tmp_path))
    assert result == snap


def test_default_data_dir_comes_from_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app import config

    monkeypatch.setattr(config.settings, "DATA_DIR", str(tmp_path))
    snap = nasty_spx_snapshot()
    path = write_snapshot(snap)  # no data_dir passed
    assert path.is_relative_to(tmp_path)
    assert read_snapshot(path) == snap
