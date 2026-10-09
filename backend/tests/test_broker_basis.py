"""Basis, rolls and the basis job (T145, plans/charter-mt5/README.md).

The series here are **synthetic**, built to carry exactly one feature each: a roll, a bad print
that reverts, an early-close day. The real-data findings they encode (roll steps of +0.49 % to
+1.24 % on roll Mondays, the desk's bad SPX close on 2026-06-26) are in `basis.py`'s docstring.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.modules.broker import basis
from app.modules.broker.basis_job import run_basis
from app.modules.broker.tables import Bar, BasisRow, RollRow, ensure_schema
from app.modules.gex.models.db import Base as GexBase
from app.modules.gex.models.db import DailyBar, IntradayBar

UTC = dt.UTC


def _series(values, start="2026-08-03 20:00", freq="B"):
    idx = pd.date_range(start, periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def _basis_with(ratios):
    desk = _series([7000.0] * len(ratios))
    return basis.measure(desk, desk * np.asarray(ratios))


# --------------------------------------------------------------------------- pure


def test_measure_joins_on_instants_only():
    desk = _series([100.0, 101.0, 102.0])
    cfd = _series([1010.0, 1020.0], start="2026-08-04 20:00")
    out = basis.measure(desk, cfd)
    assert len(out) == 2
    assert out["offset"].iloc[0] == pytest.approx(1010 - 101)
    assert out["ratio"].iloc[1] == pytest.approx(1020 / 102)


def test_a_persistent_step_is_a_roll_confirmed_later():
    ratios = [1.002] * 30 + [1.011] * 30  # +0.9 %: a roll
    rolls = basis.detect_rolls(_basis_with(ratios))
    assert len(rolls) == 1
    idx = _basis_with(ratios).index
    assert rolls[0].occurred == idx[30]
    assert rolls[0].confirmed == idx[30 + basis.ROLL_PERSIST - 1]
    assert rolls[0].step == pytest.approx(math.log(1.011 / 1.002))


def test_a_bad_print_that_reverts_is_not_a_roll():
    """The shape of the desk's SPX close on 2026-06-26: down 0.73 %, back up 0.52 % next day."""
    ratios = [1.007] * 30 + [1.007 * math.exp(-0.0073)] + [1.007 * math.exp(-0.0021)] * 29
    assert basis.detect_rolls(_basis_with(ratios)) == []


def test_ordinary_noise_is_not_a_roll():
    rng = np.random.default_rng(7)
    ratios = 1.005 * np.exp(np.cumsum(rng.normal(0, 0.0003, 400)))
    assert basis.detect_rolls(_basis_with(ratios)) == []


def test_early_close_candidates():
    assert basis.is_early_close_candidate(dt.date(2026, 11, 27))  # day after Thanksgiving
    assert not basis.is_early_close_candidate(dt.date(2026, 11, 20))
    assert basis.is_early_close_candidate(dt.date(2026, 12, 24))
    assert basis.is_early_close_candidate(dt.date(2026, 7, 2))
    assert not basis.is_early_close_candidate(dt.date(2026, 7, 6))


def test_translate_by_method_and_null_stays_null():
    spx, spy = basis.PAIRS[0], basis.PAIRS[1]
    assert basis.translate(7700.0, spx, offset=52.0, ratio=1.0067) == 7752.0
    assert basis.translate(770.0, spy, offset=0.0, ratio=10.07) == pytest.approx(7753.9)
    assert basis.translate(None, spx, offset=52.0, ratio=1.0) is None


# --------------------------------------------------------------------------- the job


@pytest.fixture
def factory(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'b.db'}").execution_options(
        schema_translate_map={"broker": None, "gex": None})
    ensure_schema(engine)
    GexBase.metadata.create_all(engine, tables=[DailyBar.__table__, IntradayBar.__table__])
    yield sessionmaker(engine)
    engine.dispose()


def _close_utc(day: dt.date) -> dt.datetime:
    return pd.Timestamp(dt.datetime.combine(day, dt.time(16)), tz="America/New_York").tz_convert(
        "UTC").to_pydatetime()


def test_job_measures_daily_and_5m_basis_and_places_the_roll(factory):
    """SPX at 7,000 every day; S&P.fs 20 points above it, then 80 above from roll day on.
    5-minute bars on the two days around the roll, with the step at 13:35 ET on roll day."""
    days = [d.date() for d in pd.bdate_range("2026-08-03", "2026-10-30")]
    roll_day = dt.date(2026, 9, 14)
    roll_at = pd.Timestamp("2026-09-14 13:35", tz="America/New_York").tz_convert("UTC")
    now = dt.datetime(2026, 11, 2, tzinfo=UTC)
    with factory() as s:
        for d in days:
            s.add(DailyBar(symbol="SPX", date=d, open=7000, high=7000, low=7000, close=7000.0,
                           volume=0, source="synthetic"))
            close = _close_utc(d)
            px = 7080.0 if d >= roll_day else 7020.0
            s.add(Bar(symbol="S&P.fs", timeframe="M1", ts=close - dt.timedelta(minutes=1),
                      open=px, high=px, low=px, close=px, tick_volume=1, spread=90,
                      real_volume=0, offset_s=10800, ingested_at=now))
        for d in (dt.date(2026, 9, 11), roll_day):
            for t in pd.date_range(f"{d} 09:30", f"{d} 15:50", freq="5min", tz="America/New_York"):
                t = t.tz_convert("UTC")
                s.add(IntradayBar(symbol="SPX", interval="5m", ts=t.to_pydatetime(), open=7000,
                                  high=7000, low=7000, close=7000.0, volume=0, source="synthetic"))
                close = t + pd.Timedelta(minutes=5)
                px = 7080.0 if close > roll_at else 7020.0
                if close == _close_utc(d):
                    continue  # the daily row above already holds this bar
                s.add(Bar(symbol="S&P.fs", timeframe="M1",
                          ts=(close - pd.Timedelta(minutes=1)).to_pydatetime(), open=px, high=px,
                          low=px, close=px, tick_volume=1, spread=90, real_volume=0,
                          offset_s=10800, ingested_at=now))
        s.commit()

    result = run_basis(factory, now)

    with factory() as s:
        rolls = s.execute(sa.select(RollRow)).scalars().all()
        assert [(r.cfd_symbol, r.resolution) for r in rolls] == [("S&P.fs", "5m")]
        assert pd.Timestamp(rolls[0].rolled_at) == roll_at + pd.Timedelta(minutes=5)
        assert rolls[0].confirmed_at > rolls[0].rolled_at
        daily = s.execute(sa.select(BasisRow).where(BasisRow.kind == "1d")
                          .order_by(BasisRow.at)).scalars().all()
        assert {round(r.offset) for r in daily} == {20, 80}
        assert all((r.segment_start is None) == (r.offset < 50) for r in daily)
    assert result["SPX/1d"] == len(days)
    assert result["SPX/5m"] > 0
    # A second run replaces rather than appends.
    run_basis(factory, now)
    with factory() as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(RollRow)) == 1
