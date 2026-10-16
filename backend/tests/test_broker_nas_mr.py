"""nas-mean-reversion in the executor (T152): the decide leg, multi-day holds, roll-adjusted P&L.

Same harness as `test_broker_executor.py` (a fake bridge, SQLite through the schema-translate
map). Bars are seeded three per session -- 09:30, 15:59 and 16:00 New York -- which is all
`rth_daily` reads. Parity of the rule with the backtest on real NAS100.fs history (1,794
sessions, 177 entries, no mismatched day) was checked by hand on 2026-10-15; these tests pin
the mechanics around it.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.modules.broker.executor import Executor
from app.modules.broker.strategies import NY, ClosedTrade, NasMeanReversion, rth_daily
from app.modules.broker.tables import Bar, Base, OrderIntent, RollRow
from tests.test_broker_executor import FakeClient

UTC = dt.UTC
NAS = NasMeanReversion()
DIP_DAY = dt.date(2026, 10, 15)  # Thursday, EDT: 16:00 New York = 20:00 UTC
EXIT_DAY = dt.date(2026, 10, 16)


def at(day: dt.date, h: int, m: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(h, m), tzinfo=NY).astimezone(UTC)


def sessions(end: dt.date, n: int) -> list[dt.date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out[::-1]


def bar(day, h, m, o, hi, lo):
    return Bar(symbol=NAS.symbol, timeframe="M1", ts=at(day, h, m), open=o, high=hi, low=lo,
               close=o, tick_volume=1, spread=200, real_volume=0, offset_s=10800,
               ingested_at=at(day, h, m))


def session_bars(day, close, lo=None, hi=None, *, at16=True):
    rows = [bar(day, 9, 30, close, hi if hi is not None else close + 20,
                lo if lo is not None else close - 20),
            bar(day, 15, 59, close, close, close)]
    if at16:
        rows.append(bar(day, 16, 0, close, close, close))
    return rows


@pytest.fixture
def factory(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'nas.db'}").execution_options(
        schema_translate_map={"broker": None})
    Base.metadata.create_all(engine)
    yield sessionmaker(engine)
    engine.dispose()


@pytest.fixture
def client():
    c = FakeClient()
    c.bid, c.ask = 24_000.0, 24_002.0
    return c


def seed_uptrend_then_dip(factory):
    """215 sessions rising 10 a day to 10-14, then a 300-point drop into the 10-15 close: above
    SMA200, RSI(2) far below 10, a 7-session low."""
    days = sessions(DIP_DAY, 215)
    with factory() as s:
        for i, d in enumerate(days[:-1]):
            s.add_all(session_bars(d, 22_000.0 + 10 * i))
        last = 22_000.0 + 10 * (len(days) - 2)
        s.add_all(session_bars(DIP_DAY, last - 300, lo=last - 305, hi=last))
        s.commit()
    return last


def make(client, factory, notes=None):
    return Executor(client, factory, [NAS], enabled=True, volume=0.01, max_volume=0.05,
                    notify=(notes.append if notes is not None else lambda m: None))


def rows(factory):
    with factory() as s:
        return {r.key: r for r in s.scalars(sa.select(OrderIntent)).all()}


# -------------------------------------------------------------------- the pure parts


def test_one_decide_leg_per_weekday_at_the_new_york_close():
    (leg,) = NAS.legs(DIP_DAY)
    assert leg.key == "nas_mr:2026-10-15:decide" and leg.action == "decide"
    assert leg.due_at == dt.datetime(2026, 10, 15, 20, 0, tzinfo=UTC)
    assert NAS.legs(dt.date(2026, 12, 15))[0].due_at.hour == 21  # EST
    assert NAS.legs(dt.date(2026, 10, 17)) == []  # Saturday


def test_rth_daily_needs_a_1559_bar_and_takes_the_16_00_open():
    d = dt.date(2026, 10, 14)
    bars = session_bars(d, 100.0, lo=90.0, hi=110.0)
    bars += [bar(DIP_DAY, 9, 30, 50.0, 55.0, 45.0), bar(DIP_DAY, 16, 0, 52.0, 52.0, 52.0)]
    m1 = pd.DataFrame([{"ts": b.ts, "open": b.open, "high": b.high, "low": b.low} for b in bars])
    daily = rth_daily(m1.set_index(pd.DatetimeIndex(m1.pop("ts"))))
    assert list(daily.index) == [d]  # 10-15 has no 15:59 bar: not a session
    assert daily.loc[d].to_dict() == {"close": 100.0, "high": 110.0, "low": 90.0}


def test_signal_entry_exit_and_too_little_history():
    c = pd.Series([100.0 + i for i in range(210)] + [250.0])
    daily = pd.DataFrame({"close": c, "high": c + 1, "low": c - 1})
    assert NAS.signal(daily, holding=False)[0] == "open"  # 250 > SMA200, RSI(2) < 10
    assert NAS.signal(daily.iloc[-150:], holding=False)[0] is None  # no SMA200 yet
    up = pd.concat([c, pd.Series([400.0])], ignore_index=True)
    daily_up = pd.DataFrame({"close": up, "high": up + 1, "low": up - 1})
    assert NAS.signal(daily_up, holding=True)[0] == "close"  # 400 > SMA5
    assert NAS.signal(daily, holding=True)[0] is None  # 250 < SMA5


def test_kill_rules_are_the_frozen_four():
    assert NAS.stop_for(24_000.0) == 22_080.0
    assert NAS.kill([ClosedTrade(1.0, None)] * 10) is None
    assert "drawdown" in NAS.kill([ClosedTrade(5.0, None)] + [ClosedTrade(-3.2, None)] * 5)
    assert "6 losing" in NAS.kill([ClosedTrade(-0.1, None)] * 6)
    assert NAS.kill([ClosedTrade(0.5, None), ClosedTrade(-0.6, None)] * 15) is None  # +1 SE > 0
    assert "expectancy" in NAS.kill([ClosedTrade(0.1, None), ClosedTrade(-0.5, None)] * 15)


# -------------------------------------------------------------------- through the executor


async def test_dip_opens_and_a_later_close_pairs_with_it_minus_the_roll(client, factory):
    notes: list[str] = []
    last = seed_uptrend_then_dip(factory)
    ex = make(client, factory, notes)

    await ex.tick(at(DIP_DAY, 16, 1) + dt.timedelta(seconds=30))
    r = rows(factory)
    assert r["nas_mr:2026-10-15:decide"].status == "decided"
    assert r["nas_mr:2026-10-15:decide"].note.startswith("open: close")
    opened = r["nas_mr:2026-10-15:open"]
    assert opened.status == "filled" and opened.fill_price == client.ask
    assert client.orders == [("NAS100.fs", "buy", 0.01, NAS.stop_for(client.ask), NAS.magic)]

    # Next session: back above SMA5, after a confirmed +0.5 % roll during the hold.
    with factory() as s:
        s.add_all(session_bars(EXIT_DAY, last + 100))
        s.add(RollRow(cfd_symbol="NAS100.fs", rolled_at=at(EXIT_DAY, 9, 30),
                      confirmed_at=at(EXIT_DAY, 10, 0), step=0.005, desk_symbol="NDX",
                      resolution="5m"))
        s.commit()
    client.bid = 24_300.0
    await ex.tick(at(EXIT_DAY, 16, 1) + dt.timedelta(seconds=30))
    closed = rows(factory)["nas_mr:2026-10-16:close"]
    assert closed.status == "filled" and client.closes == [77]
    expected = ((24_300.0 - 24_002.0) / 24_002.0 - 0.005) * 100
    assert closed.pnl_pct == pytest.approx(expected)
    assert "roll steps removed: +0.50%" in closed.note
    assert ex.closed_trades(factory(), "nas_mr")[0].pnl_pct == pytest.approx(expected)


async def test_holding_with_no_exit_signal_trades_nothing(client, factory):
    last = seed_uptrend_then_dip(factory)
    ex = make(client, factory)
    await ex.tick(at(DIP_DAY, 16, 2))
    with factory() as s:
        s.add_all(session_bars(EXIT_DAY, last - 400))  # lower again: below SMA5
        s.commit()
    await ex.tick(at(EXIT_DAY, 16, 2))
    r = rows(factory)
    assert r["nas_mr:2026-10-16:decide"].note.startswith("no trade:")
    assert "nas_mr:2026-10-16:close" not in r and "nas_mr:2026-10-16:open" not in r
    assert len(client.orders) == 1 and client.open
    assert await ex.reconcile(at(EXIT_DAY, 16, 3)) == []  # a held position is covered


async def test_waits_for_the_16_00_bar_then_decides(client, factory):
    seed_uptrend_then_dip(factory)
    with factory() as s:
        s.execute(sa.delete(Bar).where(Bar.ts == at(DIP_DAY, 16, 0)))
        s.commit()
    ex = make(client, factory)
    await ex.tick(at(DIP_DAY, 16, 0) + dt.timedelta(seconds=20))
    assert rows(factory)["nas_mr:2026-10-15:decide"].status == "pending"
    assert client.orders == []


async def test_a_holiday_is_skipped_quietly(client, factory):
    notes: list[str] = []
    seed_uptrend_then_dip(factory)
    ex = make(client, factory, notes)
    await ex.tick(at(EXIT_DAY, 16, 20))  # no bars at all on 10-16
    leg = rows(factory)["nas_mr:2026-10-16:decide"]
    assert leg.status == "skipped" and "holiday" in leg.note
    assert not [n for n in notes if "2026-10-16" in n]


async def test_an_early_close_is_skipped_quietly(client, factory):
    notes: list[str] = []
    seed_uptrend_then_dip(factory)
    with factory() as s:
        s.add(bar(EXIT_DAY, 13, 0, 24_000.0, 24_000.0, 24_000.0))
        s.commit()
    ex = make(client, factory, notes)
    await ex.tick(at(EXIT_DAY, 16, 20))
    assert rows(factory)["nas_mr:2026-10-16:decide"].status == "skipped"


async def test_a_missed_window_expires_and_alerts(client, factory):
    notes: list[str] = []
    seed_uptrend_then_dip(factory)
    ex = make(client, factory, notes)
    await ex.tick(at(DIP_DAY, 16, 20))  # the executor came back after the window
    assert rows(factory)["nas_mr:2026-10-15:decide"].status == "expired"
    assert client.orders == []
    assert any("no decision on 2026-10-15" in n for n in notes)
