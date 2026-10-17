"""nas-4h-atr3 in the executor (T180): 4h decide legs, the signal-candle stop, the raise-only
trail, broker stop-outs and the entry alert.

Same harness as `test_broker_nas_mr.py` (a fake bridge, SQLite through the schema-translate
map). Bars are seeded one M1 bar per 4h bin, which is all `h4_bars` needs. Parity of the rule
with backtest #562 on real history is `test_broker_nas_4h_parity.py`.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.modules.broker.client import Reply
from app.modules.broker.executor import Executor
from app.modules.broker.strategies import NY, ClosedTrade, Held, Nas4hAtr3
from app.modules.broker.tables import Bar, Base, OrderIntent
from tests.test_broker_executor import FakeClient

UTC = dt.UTC
S = Nas4hAtr3()
DAY = dt.date(2026, 10, 15)  # Thursday, EDT


def ny(day: dt.date, h: int, m: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(h, m), tzinfo=NY).astimezone(UTC)


class Fake4h(FakeClient):
    def __init__(self):
        super().__init__()
        self.bid, self.ask = 20_000.0, 20_002.0
        self.modified: list[tuple[int, float]] = []
        self.deal_rows: list[dict] = []

    async def modify_sl(self, ticket, sl):
        self.modified.append((ticket, sl))
        return Reply({"retcode": 10009}, 0)

    async def deals(self, position):
        return self.deal_rows


def bar(ts, close, hi=5.0, lo=5.0):
    return Bar(symbol=S.symbol, timeframe="M1", ts=ts, open=close, high=close + hi,
               low=close - lo, close=close, tick_volume=1, spread=200, real_volume=0,
               offset_s=10800, ingested_at=ts)


def seed_uptrend(factory, end: dt.datetime, n: int = 150, step: float = 10.0) -> float:
    """One bar 30 minutes into each of the `n` 4h bins before `end`, rising `step` a bin to
    19,990, just under the fake quote."""
    base = 19_990.0 - step * (n - 1)
    with factory() as s:
        for k in range(n):
            s.add(bar(end - dt.timedelta(hours=4 * (n - k)) + dt.timedelta(minutes=30),
                      base + step * k))
        s.commit()
    return 19_990.0


def add(factory, *bars):
    with factory() as s:
        s.add_all(bars)
        s.commit()


@pytest.fixture
def factory(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'h4.db'}").execution_options(
        schema_translate_map={"broker": None})
    Base.metadata.create_all(engine)
    yield sessionmaker(engine)
    engine.dispose()


@pytest.fixture
def client():
    return Fake4h()


def make(client, factory, notes=None, *, enabled=True):
    return Executor(client, factory, [S], enabled=enabled, volume=0.01, max_volume=0.05,
                    notify=(notes.append if notes is not None else lambda m: None))


def rows(factory):
    with factory() as s:
        return {r.key: r for r in s.scalars(sa.select(OrderIntent)).all()}


# -------------------------------------------------------------------- the pure parts


def test_a_decide_leg_at_the_end_of_every_4h_bin():
    legs = S.legs(DAY)
    assert legs[0].key == "nas_4h_atr3:2026-10-15T01:decide"
    assert [lg.due_at for lg in legs] == [ny(DAY, h) for h in (1, 5, 9, 13, 17, 21)]
    assert {lg.action for lg in legs} == {"decide"}
    assert len(S.legs(dt.date(2026, 10, 16))) == 5  # Friday: the 17-21 bin has no market
    assert S.legs(dt.date(2026, 10, 17)) == []  # Saturday
    assert [lg.due_at for lg in S.legs(dt.date(2026, 10, 18))] == [ny(dt.date(2026, 10, 18), 21)]


def test_h4_bars_anchor_at_17_new_york():
    ts = [ny(DAY, 16, 59), ny(DAY, 17, 0), ny(DAY, 20, 59), ny(DAY, 21, 0)]
    m1 = pd.DataFrame({"high": [1.0, 2, 3, 4], "low": [0.0, 1, 2, 3], "close": [0.5, 1.5, 2.5, 3.5]},
                      index=pd.DatetimeIndex(ts))
    h4 = S.h4_bars(m1)
    assert [str(i) for i in h4.index] == ["2026-10-15 13:00:00", "2026-10-15 17:00:00",
                                          "2026-10-15 21:00:00"]
    assert h4.iloc[1][["high", "low", "close"]].tolist() == [3.0, 1.0, 2.5]
    assert S.bin_of(ny(DAY, 20, 59)) == pd.Timestamp("2026-10-15 17:00")


def test_signal_open_close_and_a_trail_that_only_rises():
    c = pd.Series([100.0 + i for i in range(80)])
    h4 = S.h4_bars(pd.DataFrame({"high": c.values + 1, "low": c.values - 1, "close": c.values},
                                index=pd.date_range("2026-09-01", periods=80, freq="4h", tz=UTC)))
    action, _, anchor = S.signal(h4, None)
    assert action == "open" and anchor == pytest.approx(178.0)
    entry_at = h4.index[-10].tz_localize(NY).astimezone(UTC).to_pydatetime()
    action, _, stop = S.signal(h4, Held(entry_at, 170.0, 150.0))
    expected = round(h4.high.iloc[-10:].max() - 3 * h4.atr14.iloc[-1], 2)
    assert action == "trail" and stop == expected
    assert S.signal(h4, Held(entry_at, 170.0, expected + 1))[0] is None  # never lower
    down = pd.concat([c, pd.Series([150.0])], ignore_index=True)
    h4d = S.h4_bars(pd.DataFrame({"high": down.values + 1, "low": down.values - 1,
                                  "close": down.values},
                                 index=pd.date_range("2026-09-01", periods=81, freq="4h", tz=UTC)))
    assert S.signal(h4d, Held(entry_at, 170.0, 150.0))[0] == "close"  # close < EMA20
    assert S.signal(h4d, None)[0] is None


def test_initial_stop_is_one_spread_below_the_candle_low():
    assert S.initial_stop(19_995.0, bid=20_000.0, ask=20_002.5) == 19_992.5


def test_kill_rules():
    assert S.kill([ClosedTrade(0.5, 2.0)] * 10) is None
    assert "within the first 30" in S.kill([ClosedTrade(-1.0, 2.0)] * 10)
    assert "losing trades" in S.kill([ClosedTrade(5.0, 2.0)] + [ClosedTrade(-0.01, 2.0)] * 28)
    assert "cost drift" in S.kill([ClosedTrade(0.2, 3.5)] * 20)
    # A slow bleed: no drawdown limit yet, but the CUSUM sees it.
    bleed = [ClosedTrade(x, 2.0) for x in [0.1] * 30 + [-0.15, 0.05] * 80]
    assert "CUSUM" in S.kill(bleed)


# -------------------------------------------------------------------- through the executor


async def test_entry_alerts_opens_with_the_candle_stop_then_trails(client, factory):
    notes: list[str] = []
    end = ny(DAY, 13)
    last = seed_uptrend(factory, end)
    add(factory, bar(end, last + 10))  # the first bar after the bin: the fill
    ex = make(client, factory, notes)

    await ex.tick(end + dt.timedelta(minutes=1))
    r = rows(factory)
    assert r["nas_4h_atr3:2026-10-15T13:decide"].note.startswith("open: 4h close")
    opened = r["nas_4h_atr3:2026-10-15T13:open"]
    assert opened.status == "filled" and opened.fill_price == client.ask
    assert opened.sl == round((last - 5.0) - (client.ask - client.bid), 2)
    assert client.orders == [("NAS100.fs", "buy", 0.01, opened.sl, S.magic)]
    assert any("ENTRY signal on the Thu 09:00 NY 4h bin" in n for n in notes)

    # The 13-17 bin runs far above the entry: the chandelier passes the candle stop.
    add(factory, bar(end + dt.timedelta(minutes=30), last + 400, hi=5.0, lo=5.0),
        bar(ny(DAY, 17), last + 400))
    await ex.tick(ny(DAY, 17, 1))
    r = rows(factory)
    mod = r["nas_4h_atr3:2026-10-15T17:modify"]
    assert mod.status == "filled" and mod.sl > opened.sl
    assert client.modified == [(77, mod.sl)]
    assert r["nas_4h_atr3:2026-10-15T17:decide"].note.startswith("trail:")


async def test_a_broker_stop_out_is_journalled_from_the_deals(client, factory):
    notes: list[str] = []
    end = ny(DAY, 13)
    last = seed_uptrend(factory, end)
    add(factory, bar(end, last + 10))
    ex = make(client, factory, notes)
    await ex.tick(end + dt.timedelta(minutes=1))

    client.open = []  # the broker's stop took it
    client.deal_rows = [{"entry": 0, "price": client.ask}, {"entry": 1, "price": 19_990.0}]
    add(factory, bar(end + dt.timedelta(minutes=30), last - 300), bar(ny(DAY, 17), last - 300))
    await ex.tick(ny(DAY, 17, 1))
    r = rows(factory)
    stopped = r["nas_4h_atr3:2026-10-15T13:stopped"]
    assert stopped.action == "close" and stopped.fill_price == 19_990.0
    assert stopped.pnl_pct == pytest.approx((19_990.0 - 20_002.0) / 20_002.0 * 100)
    assert r["nas_4h_atr3:2026-10-15T17:decide"].status == "decided"
    assert any("stopped out @ 19990.0" in n for n in notes)
    assert ex.closed_trades(factory(), S.name)[0].pnl_pct == stopped.pnl_pct


async def test_a_close_below_ema20_sells(client, factory):
    end = ny(DAY, 13)
    last = seed_uptrend(factory, end)
    add(factory, bar(end, last + 10))
    ex = make(client, factory)
    await ex.tick(end + dt.timedelta(minutes=1))
    client.bid = 19_500.0
    add(factory, bar(end + dt.timedelta(minutes=30), last - 600, lo=1.0),
        bar(ny(DAY, 17), last - 600))
    await ex.tick(ny(DAY, 17, 1))
    closed = rows(factory)["nas_4h_atr3:2026-10-15T17:close"]
    assert closed.status == "filled" and client.closes == [77]
    assert closed.pnl_pct == pytest.approx((19_500.0 - 20_002.0) / 20_002.0 * 100)


async def test_dry_run_alerts_a_fresh_signal_once_and_sends_nothing(client, factory):
    notes: list[str] = []
    end = ny(DAY, 13)
    last = seed_uptrend(factory, end)
    add(factory, bar(end, last + 10))
    ex = make(client, factory, notes, enabled=False)
    # The previous bin was on too, so in a dry run this is not a fresh signal.
    await ex.tick(end + dt.timedelta(minutes=1))
    assert client.orders == [] and not [n for n in notes if "ENTRY" in n]
    assert rows(factory)["nas_4h_atr3:2026-10-15T13:open"].status == "skipped"


async def test_dry_run_alerts_when_the_signal_turns_on(client, factory):
    notes: list[str] = []
    end = ny(DAY, 13)
    last = seed_uptrend(factory, end - dt.timedelta(hours=8))
    # A dip bin below EMA20, then the bin that turns the signal back on.
    add(factory, bar(end - dt.timedelta(hours=7, minutes=30), last - 200))
    add(factory, bar(end - dt.timedelta(hours=3, minutes=30), last + 200), bar(end, last + 200))
    ex = make(client, factory, notes, enabled=False)
    await ex.tick(end + dt.timedelta(minutes=1))
    assert [n for n in notes if "ENTRY signal (dry run: no order)" in n]
    assert client.orders == []


async def test_no_trade_when_the_stop_would_be_above_the_bid(client, factory):
    end = ny(DAY, 13)
    last = seed_uptrend(factory, end)
    add(factory, bar(end, last + 10))
    client.bid, client.ask = last - 20, last - 18  # gapped below the signal candle's low
    ex = make(client, factory)
    await ex.tick(end + dt.timedelta(minutes=1))
    opened = rows(factory)["nas_4h_atr3:2026-10-15T13:open"]
    assert opened.status == "skipped" and opened.note.startswith("gap:")
    assert client.orders == []


async def test_waits_for_the_next_bar_and_expires_when_late(client, factory):
    notes: list[str] = []
    end = ny(DAY, 13)
    seed_uptrend(factory, end)
    ex = make(client, factory, notes)
    await ex.tick(end + dt.timedelta(minutes=1))
    assert rows(factory)["nas_4h_atr3:2026-10-15T13:decide"].status == "pending"
    add(factory, bar(end, 25_000.0))
    await ex.tick(end + dt.timedelta(minutes=12))  # the bar opened 12 minutes ago
    leg = rows(factory)["nas_4h_atr3:2026-10-15T13:decide"]
    assert leg.status == "expired" and client.orders == []
    assert any("not decided in time" in n for n in notes)


async def test_a_bin_with_no_bars_is_skipped(client, factory):
    end = ny(DAY, 13)
    seed_uptrend(factory, end - dt.timedelta(hours=8))
    add(factory, bar(end, 25_000.0))
    ex = make(client, factory)
    await ex.tick(end + dt.timedelta(minutes=1))
    assert rows(factory)["nas_4h_atr3:2026-10-15T13:decide"].status == "skipped"
