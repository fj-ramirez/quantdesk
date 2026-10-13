"""T156's pure core: release dates, signals, and trades (plans/cot/study.md)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.modules.research import cot_study as cs
from app.modules.terminal import derive


def test_percentile_matches_the_terminals_derivation():
    rng = np.random.default_rng(0)
    net = pd.Series(rng.normal(size=400).cumsum())
    ours = cs.percentile(net)
    theirs = derive.rolling_percentile(net, derive.COT_PERCENTILE_WEEKS)
    pd.testing.assert_series_equal(ours, theirs, check_names=False)


@pytest.mark.parametrize("nominal, expected", [
    (dt.date(2026, 10, 9), dt.date(2026, 10, 9)),     # ordinary Friday
    (dt.date(2025, 11, 28), dt.date(2025, 12, 1)),    # day after Thanksgiving -> Monday
    (dt.date(2020, 12, 25), dt.date(2020, 12, 28)),   # Christmas Friday -> Monday
    (dt.date(2022, 11, 11), dt.date(2022, 11, 14)),   # Veterans Day Friday, NYSE open
])
def test_release_date_never_earlier_than_cftc_could_publish(nominal, expected):
    assert cs.release_date(nominal) == expected


def test_release_dates_match_cftcs_2025_holiday_schedule():
    # From CFTC release 9138-25: 11/25 report "originally" due 12/01, 12/23 due 12/29.
    assert cs.release_date(dt.date(2025, 11, 28)) <= dt.date(2025, 12, 1)
    assert cs.release_date(dt.date(2025, 12, 26)) == dt.date(2025, 12, 26)  # NOT a holiday


def test_roll_mondays_match_t145():
    rolls = cs.roll_dates(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert dt.date(2026, 12, 14) in rolls
    assert dt.date(2026, 9, 14) in rolls
    assert all(d.weekday() == 0 for d in rolls)


def _reports(nets: list[float], start=dt.date(2010, 1, 5)) -> pd.DataFrame:
    vd = [start + dt.timedelta(weeks=i) for i in range(len(nets))]
    return pd.DataFrame({"value_date": vd, "release": [d + dt.timedelta(days=3) for d in vd],
                         "net": nets})


def test_signals_need_a_full_window_and_mark_episodes():
    nets = list(range(cs.PERCENTILE_WEEKS)) + [1000, 1001, 0, 1002]
    sig = cs.signals(_reports(nets))
    # The 156th report is the max of its window -> crowded long; then two more; one low.
    assert list(sig["crowd"]) == [1, 1, 1, -1, 1]
    eps = list(sig["episode"])
    assert eps[0] == eps[1] == eps[2]
    assert len(set(eps)) == 3


def test_shutdown_reports_are_dropped_not_redated():
    nets = list(range(cs.PERCENTILE_WEEKS + 20))
    rep = _reports(nets, start=dt.date(2016, 1, 5))
    sig = cs.signals(rep)
    assert not any(cs.in_shutdown(d) for d in sig["value_date"])
    assert any(cs.in_shutdown(d) for d in rep["value_date"])


def _bars(closes: list[float], start="2020-01-01", wiggle=1.0) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.array(closes, float)
    return pd.DataFrame({"open": c, "high": c + wiggle, "low": c - wiggle, "close": c},
                        index=idx)


INST = cs.Instrument("X", "X", 0.0, (0.0, 0.0), False)


def _sig(release: dt.date, crowd: int = -1, episode: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"value_date": [release - dt.timedelta(days=3)], "release": [release],
                         "crowd": [crowd], "episode": [episode], "pct": [5.0]})


def test_entry_is_the_close_of_the_first_day_strictly_after_release():
    bars = _bars([100.0] * 30 + list(np.arange(100, 120, 1.0)))
    release = bars.index[29].date()
    (t,) = cs.simulate(bars, _sig(release), INST, "contrarian", 5)
    assert t.entry_date == bars.index[30].date()
    assert t.side == 1  # crowded short, contrarian -> long
    assert t.entry == 100.0
    assert t.exit_date == bars.index[35].date()
    assert t.how == "time"
    assert t.gross_r == pytest.approx((105.0 - 100.0) / t.stop_dist)


def test_follow_is_the_mirror_of_contrarian_before_costs_when_neither_is_stopped():
    # A stop breaks the symmetry (the losing side is capped at -1R), so the move stays small.
    bars = _bars([100.0] * 30 + list(np.arange(100, 104, 0.2)))
    release = bars.index[29].date()
    (c,) = cs.simulate(bars, _sig(release), INST, "contrarian", 5)
    (f,) = cs.simulate(bars, _sig(release), INST, "follow", 5)
    assert f.side == -c.side
    assert f.gross_r == pytest.approx(-c.gross_r)


def test_stop_and_gap_fills():
    closes = [100.0] * 30 + [100.0, 99.0, 90.0, 90.0, 90.0, 90.0]
    bars = _bars(closes)
    release = bars.index[29].date()
    (t,) = cs.simulate(bars, _sig(release), INST, "contrarian", 5)
    # ATR(20) of a flat series with +-1 wiggle is 2, so the stop is 4 below 100.
    assert t.stop_dist == pytest.approx(4.0)
    assert t.how == "gap" and t.exit == 90.0  # opened at 90, past the stop at 96


def test_one_position_at_a_time():
    bars = _bars([100.0] * 60)
    r0 = bars.index[25].date()
    sig = pd.concat([_sig(r0), _sig(bars.index[27].date()), _sig(bars.index[40].date())])
    trades = cs.simulate(bars, sig, INST, "follow", 10)
    assert [t.entry_date for t in trades] == [bars.index[26].date(), bars.index[41].date()]


def test_a_swap_credit_is_not_doubled_and_a_charge_is():
    base = {"entry_date": dt.date(2020, 1, 1), "exit_date": dt.date(2020, 1, 8), "side": 1,
            "entry": 1.0, "exit": 1.0, "stop_dist": 1.0, "how": "time", "episode": 1,
            "gross_r": 0.0, "carry_r": 0.0, "spread_r": 0.1, "rolls": 0}
    assert cs.net_r(cs.Trade(**base, swap_r=0.05), 2.0) == pytest.approx(-0.2 + 0.05)
    assert cs.net_r(cs.Trade(**base, swap_r=-0.05), 2.0) == pytest.approx(-0.2 - 0.1)


def test_index_carry_charges_longs_and_credits_shorts():
    inst = cs.Instrument("I", "I", 0.0, (0.0, 0.0), True, div_yield=0.0)
    bars = _bars([100.0] * 40)
    rate = pd.Series(5.0, index=bars.index)
    (lng,) = cs.simulate(bars, _sig(bars.index[25].date(), crowd=-1), inst, "contrarian", 5,
                         rate)
    (sht,) = cs.simulate(bars, _sig(bars.index[25].date(), crowd=1), inst, "contrarian", 5,
                         rate)
    assert lng.carry_r < 0 < sht.carry_r
    assert lng.carry_r == pytest.approx(-sht.carry_r)


def test_the_declared_space_is_84_trials():
    assert len(cs.trials()) == 84
    assert len(set(cs.trials())) == 84
