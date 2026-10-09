"""The executor and the gold-asia schedule (T151).

The bridge is a fake with the client's async interface, and the database is SQLite through the
same schema-translate trick as `test_broker_ingest.py`. Covered here: what gets sent, what does
not, and what is written. The real terminal is covered by the homeserver acceptance only.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.modules.broker.client import BridgeError, Reply
from app.modules.broker.executor import Executor
from app.modules.broker.strategies import ClosedTrade, GoldAsiaDrift
from app.modules.broker.tables import Base, OrderIntent, StrategyState

UTC = dt.UTC
GA = GoldAsiaDrift()
# Monday 2026-10-12 (EDT): 18:00 New York = 22:00 UTC; the exit is Tuesday 07:00 UTC.
OPEN_AT = dt.datetime(2026, 10, 12, 22, 0, 5, tzinfo=UTC)
CLOSE_AT = dt.datetime(2026, 10, 13, 7, 0, 5, tzinfo=UTC)


class FakeClient:
    def __init__(self):
        self.bid, self.ask = 4190.0, 4190.3
        self.open: list[dict] = []
        self.orders: list[tuple] = []
        self.closes: list[int] = []
        self.fail_close = False

    async def positions(self, *, symbol=None, magic=None):
        return [p for p in self.open if magic is None or p["magic"] == magic]

    async def account(self):
        return SimpleNamespace(login=52000001, trade_mode="demo")

    async def tick(self, symbol):
        return Reply({"bid": self.bid, "ask": self.ask}, 0)

    async def order_market(self, symbol, side, volume, sl, *, magic, comment=""):
        self.orders.append((symbol, side, volume, sl, magic))
        self.open.append({"ticket": 77, "magic": magic, "side": side})
        return Reply({"order": 77, "price": self.ask, "quote_bid": self.bid,
                      "quote_ask": self.ask}, 0)

    async def close_position(self, ticket, *, comment=""):
        if self.fail_close:
            raise BridgeError("order rejected: retcode 10018 (Market closed)")
        self.closes.append(ticket)
        self.open = [p for p in self.open if p["ticket"] != ticket]
        return Reply({"price": self.bid, "quote_bid": self.bid, "quote_ask": self.ask}, 0)


@pytest.fixture
def factory(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ex.db'}").execution_options(
        schema_translate_map={"broker": None})
    Base.metadata.create_all(engine)
    yield sessionmaker(engine)
    engine.dispose()


@pytest.fixture
def client():
    return FakeClient()


def make(client, factory, *, enabled=True, notes=None):
    return Executor(client, factory, [GA], enabled=enabled, volume=0.01, max_volume=0.05,
                    notify=(notes.append if notes is not None else lambda m: None))


def rows(factory):
    with factory() as s:
        return {r.key: r for r in s.scalars(sa.select(OrderIntent)).all()}


# -------------------------------------------------------------------- the schedule


def test_legs_are_monday_to_thursday_in_new_york_time():
    assert GA.legs(dt.date(2026, 10, 16)) == []  # Friday
    assert GA.legs(dt.date(2026, 10, 18)) == []  # Sunday
    o, c = GA.legs(dt.date(2026, 10, 12))
    assert o.due_at == dt.datetime(2026, 10, 12, 22, 0, tzinfo=UTC)
    assert c.due_at == dt.datetime(2026, 10, 13, 7, 0, tzinfo=UTC)
    winter_open, _ = GA.legs(dt.date(2026, 12, 7))  # EST: 18:00 = 23:00 UTC
    assert winter_open.due_at.hour == 23


def test_disaster_stop_is_six_percent_below_the_entry():
    assert GA.stop_for(4000.0) == 3760.0


def test_schedule_is_idempotent(client, factory):
    ex = make(client, factory)
    assert ex.schedule(OPEN_AT) == 2
    assert ex.schedule(OPEN_AT) == 0


# -------------------------------------------------------------------- sending


async def test_dry_run_journals_but_sends_nothing(client, factory):
    await make(client, factory, enabled=False).tick(OPEN_AT)
    r = rows(factory)["gold_asia:2026-10-12:open"]
    assert r.status == "skipped" and "dry run" in r.note
    assert client.orders == []


async def test_round_trip_fills_with_stop_and_records_pnl(client, factory):
    notes: list[str] = []
    ex = make(client, factory, notes=notes)
    await ex.tick(OPEN_AT)
    (order,) = client.orders
    assert order == ("XAUUSD", "buy", 0.01, GA.stop_for(4190.3), GA.magic)
    client.bid = 4200.0
    await ex.tick(CLOSE_AT)
    r = rows(factory)
    o, c = r["gold_asia:2026-10-12:open"], r["gold_asia:2026-10-12:close"]
    assert o.status == c.status == "filled"
    assert o.fill_price == 4190.3 and o.account_mode == "demo"
    assert c.pnl_pct == pytest.approx((4200.0 - 4190.3) / 4190.3 * 100)
    assert client.closes == [77] and len(notes) == 2


async def test_a_late_entry_expires_instead_of_chasing(client, factory):
    await make(client, factory).tick(OPEN_AT + dt.timedelta(minutes=15))
    assert rows(factory)["gold_asia:2026-10-12:open"].status == "expired"
    assert client.orders == []


async def test_an_open_position_blocks_a_second_entry(client, factory):
    client.open.append({"ticket": 5, "magic": GA.magic, "side": "buy"})
    await make(client, factory).tick(OPEN_AT)
    assert "already open" in rows(factory)["gold_asia:2026-10-12:open"].note
    assert client.orders == []


def test_volume_above_the_hard_cap_refuses_to_start(client, factory):
    with pytest.raises(ValueError, match="hard cap"):
        Executor(client, factory, [GA], enabled=True, volume=0.1, max_volume=0.05)


async def test_a_failed_close_stays_pending_and_is_retried(client, factory):
    ex = make(client, factory)
    await ex.tick(OPEN_AT)
    client.fail_close = True
    await ex.tick(CLOSE_AT)
    c = rows(factory)["gold_asia:2026-10-12:close"]
    assert c.status == "pending" and "Market closed" in c.note
    client.fail_close = False
    await ex.tick(CLOSE_AT + dt.timedelta(minutes=1))
    assert rows(factory)["gold_asia:2026-10-12:close"].status == "filled"


async def test_a_position_gone_before_the_exit_is_reported(client, factory):
    notes: list[str] = []
    ex = make(client, factory, notes=notes)
    await ex.tick(OPEN_AT)
    client.open.clear()  # disaster stop hit overnight
    await ex.tick(CLOSE_AT)
    assert rows(factory)["gold_asia:2026-10-12:close"].status == "skipped"
    assert any("position gone" in n for n in notes)


async def test_reconcile_flags_an_unmanaged_position(client, factory):
    client.open.append({"ticket": 5, "magic": GA.magic, "side": "buy"})
    problems = await make(client, factory).reconcile(OPEN_AT)
    assert problems and "no pending close" in problems[0]


# -------------------------------------------------------------------- kill rules


def test_kill_rules():
    assert GA.kill([ClosedTrade(-0.1, 0.2)] * 8) is None
    assert "losing nights" in GA.kill([ClosedTrade(-0.1, 0.2)] * 9)
    assert "drawdown" in GA.kill([ClosedTrade(-3.5, 0.2), ClosedTrade(0.1, 0.2),
                                  ClosedTrade(-3.5, 0.2)])
    assert "cost drift" in GA.kill([ClosedTrade(0.1, 0.6)] * 20)


async def test_a_kill_rule_pauses_and_the_next_entry_is_skipped(client, factory):
    ex = make(client, factory)
    day = dt.date(2026, 10, 12)
    with factory() as s:  # eight losing nights already journalled
        for k in range(8):
            key = f"gold_asia:2026-09-{k + 1:02d}"
            for leg, pnl in (("open", None), ("close", -0.1)):
                s.add(OrderIntent(key=f"{key}:{leg}", strategy="gold_asia", symbol="XAUUSD",
                                  action=leg, side="buy", volume=0.01, due_at=OPEN_AT
                                  - dt.timedelta(days=40 - k), expires_at=OPEN_AT,
                                  status="filled", attempts=1, created_at=OPEN_AT,
                                  fill_price=4000.0, pnl_pct=pnl, quote_bid=1, quote_ask=1.2))
        s.commit()
    client.bid = 4100.0  # the ninth night loses too
    await ex.tick(OPEN_AT)
    await ex.tick(CLOSE_AT)
    with factory() as s:
        st = s.get(StrategyState, "gold_asia")
        assert st.paused and "losing nights" in st.reason
    nxt = OPEN_AT + dt.timedelta(days=1)
    await ex.tick(nxt)
    assert rows(factory)[f"gold_asia:{day + dt.timedelta(days=1)}:open"].note.startswith("paused")
    assert len(client.orders) == 1
