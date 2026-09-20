"""Tests for `app/modules/gex/scan/flows.py` (T52). Pure -- no DB, no HTTP; every fixture here is a
hand-built `DataFrame`, same style as `test_scan_trend.py`/`test_scan_rotation.py`.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.modules.gex.scan.flows import compute_flows


def _wide(dates: list[dt.date], values: dict[str, list[float | None]]) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.Index(dates, name="date"))


# --- hand-computed example (also recorded in docs/validation-scan.md) -----------------------


def test_compute_flows_matches_a_hand_computed_2day_window():
    """SO: d0=100, d1=110, d2=105. NAV: d0=10, d1=11, d2=9.

    By hand:
        flow_d1 = (110 - 100) * 11 = 110
        flow_d2 = (105 - 110) * 9  = -45
        aggregate flow (w=2)       = 110 + (-45) = 65
        window-start AUM           = SO_d0 * NAV_d0 = 100 * 10 = 1000
        flow_pct (w=2)             = 65 / 1000 = 0.065
    """
    dates = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
    so = _wide(dates, {"XLK": [100, 110, 105]})
    nav = _wide(dates, {"XLK": [10, 11, 9]})

    result = compute_flows(so, nav, windows=[2])
    row = result[result["symbol"] == "XLK"].iloc[0]

    assert row["flow_2"] == pytest.approx(65.0)
    assert row["flow_pct_2"] == pytest.approx(0.065)
    assert row["history_since"] == dt.date(2026, 9, 1)
    assert row["latest_date"] == dt.date(2026, 9, 3)


def test_compute_flows_1day_window_matches_the_definition_directly():
    dates = [dt.date(2026, 9, 1), dt.date(2026, 9, 2)]
    so = _wide(dates, {"XLK": [100, 110]})
    nav = _wide(dates, {"XLK": [10, 11]})

    result = compute_flows(so, nav, windows=[1])
    row = result[result["symbol"] == "XLK"].iloc[0]

    # flow_1 = (110 - 100) * 11 = 110; flow_pct_1 = 110 / (100 * 10) = 0.11
    assert row["flow_1"] == pytest.approx(110.0)
    assert row["flow_pct_1"] == pytest.approx(0.11)


# --- insufficient history -> None, never a fabricated number --------------------------------


def test_compute_flows_returns_none_when_fewer_than_window_plus_one_days_exist():
    """The plan: 'compute a 20-day flow only when 20 days exist' -- a 5-day window needs 6
    paired observations; this fixture has only 3."""
    dates = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
    so = _wide(dates, {"XLK": [100, 110, 105]})
    nav = _wide(dates, {"XLK": [10, 11, 9]})

    result = compute_flows(so, nav, windows=[5])
    row = result[result["symbol"] == "XLK"].iloc[0]

    assert row["flow_5"] is None
    assert row["flow_pct_5"] is None
    assert row["history_since"] == dt.date(2026, 9, 1)  # still reported, for "history since"


def test_compute_flows_exactly_window_plus_one_days_computes_a_value():
    dates = [dt.date(2026, 9, d) for d in range(1, 4)]  # 3 days -> a 2-day window fits exactly
    so = _wide(dates, {"XLK": [100, 110, 105]})
    nav = _wide(dates, {"XLK": [10, 11, 9]})

    result = compute_flows(so, nav, windows=[2])
    row = result[result["symbol"] == "XLK"].iloc[0]
    assert row["flow_2"] is not None


def test_compute_flows_symbol_with_zero_history_returns_none_and_no_history_since():
    dates = [dt.date(2026, 9, 1), dt.date(2026, 9, 2)]
    so = _wide(dates, {"NOPE": [None, None]})
    nav = _wide(dates, {"NOPE": [None, None]})

    result = compute_flows(so, nav, windows=[1])
    row = result[result["symbol"] == "NOPE"].iloc[0]

    assert row["flow_1"] is None
    assert row["history_since"] is None
    assert row["latest_date"] is None


def test_compute_flows_drops_dates_where_either_so_or_nav_is_missing():
    """A date where NAV is known but SO is not (or vice versa) contributes no flow -- never
    forward-filled or fabricated."""
    dates = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
    so = _wide(dates, {"XLK": [100, None, 105]})
    nav = _wide(dates, {"XLK": [10, 11, 9]})

    result = compute_flows(so, nav, windows=[1])
    row = result[result["symbol"] == "XLK"].iloc[0]

    # Only d0 and d2 are paired; that is a 1-row-apart pair for a window=1 computation, but
    # the *dates* in between are simply absent from the aligned series, not zero-filled.
    assert row["history_since"] == dt.date(2026, 9, 1)
    assert row["latest_date"] == dt.date(2026, 9, 3)
    # flow_1 = (105 - 100) * 9 = 45 (computed across the gap, using the two paired rows only).
    assert row["flow_1"] == pytest.approx(45.0)


def test_compute_flows_multiple_windows_in_one_call():
    dates = [dt.date(2026, 9, d) for d in range(1, 5)]
    so = _wide(dates, {"XLK": [100, 110, 105, 120]})
    nav = _wide(dates, {"XLK": [10, 11, 9, 12]})

    result = compute_flows(so, nav, windows=[1, 3])
    row = result[result["symbol"] == "XLK"].iloc[0]

    assert row["flow_1"] is not None  # 3 days of history -> window=1 (needs 2) is fine
    assert row["flow_3"] is not None  # needs 4 -- exactly what this fixture has
    assert "flow_1" in result.columns and "flow_pct_1" in result.columns
    assert "flow_3" in result.columns and "flow_pct_3" in result.columns


def test_compute_flows_zero_start_aum_gives_none_percent_not_a_division_error():
    dates = [dt.date(2026, 9, 1), dt.date(2026, 9, 2)]
    so = _wide(dates, {"XLK": [0, 10]})
    nav = _wide(dates, {"XLK": [10, 11]})

    result = compute_flows(so, nav, windows=[1])
    row = result[result["symbol"] == "XLK"].iloc[0]

    assert row["flow_1"] == pytest.approx(110.0)  # (10 - 0) * 11
    assert row["flow_pct_1"] is None  # start AUM is 0 * 10 = 0 -- undefined, not inf/NaN


def test_compute_flows_multiple_symbols_each_computed_independently():
    dates = [dt.date(2026, 9, 1), dt.date(2026, 9, 2)]
    so = _wide(dates, {"XLK": [100, 110], "IWM": [50, 40]})
    nav = _wide(dates, {"XLK": [10, 11], "IWM": [20, 22]})

    result = compute_flows(so, nav, windows=[1])
    by_symbol = {row["symbol"]: row for _, row in result.iterrows()}

    assert by_symbol["XLK"]["flow_1"] == pytest.approx(110.0)
    # IWM: (40 - 50) * 22 = -220
    assert by_symbol["IWM"]["flow_1"] == pytest.approx(-220.0)
