"""The MT5 bridge and its client (T143, plans/charter-mt5/README.md).

The bridge runs under Wine against the real `MetaTrader5` package, which cannot be imported
here. It takes the module as an argument, so these tests hand it a fake one and run the real
TCP server and the real async client against each other. What this cannot test is the
terminal itself; that is T143's acceptance on the homeserver.
"""

from __future__ import annotations

import ast
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.modules.broker.client import BridgeClient, BridgeError

SERVER_PY = Path(__file__).resolve().parents[2] / "docker" / "mt5" / "bridge" / "server.py"

_spec = importlib.util.spec_from_file_location("mt5_bridge_server", SERVER_PY)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

LOGIN = 52000001


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
    COPY_TICKS_ALL = -1
    TRADE_ACTION_DEAL, ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TIME_GTC = 1, 0, 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, *, login=LOGIN, trade_mode=0, init_ok=True):
        self.login, self.trade_mode, self.init_ok = login, trade_mode, init_ok
        self.alive = False
        self.initialize_calls = 0
        self.rate_calls: list[tuple] = []
        self.sent: list[dict] = []
        self.open: dict[int, SimpleNamespace] = {}
        self.retcode = self.TRADE_RETCODE_DONE

    def positions_get(self, symbol=None, ticket=None):
        rows = list(self.open.values())
        if symbol is not None:
            rows = [p for p in rows if p.symbol == symbol]
        if ticket is not None:
            rows = [p for p in rows if p.ticket == ticket]
        return tuple(rows)

    def order_send(self, request):
        self.sent.append(request)
        price = request["price"]
        if self.retcode == self.TRADE_RETCODE_DONE:
            if "position" in request:
                self.open.pop(request["position"], None)
            else:
                ticket = 1000 + len(self.sent)
                self.open[ticket] = SimpleNamespace(
                    ticket=ticket, symbol=request["symbol"], type=request["type"],
                    volume=request["volume"], price_open=price, sl=request["sl"], tp=0.0,
                    price_current=price, swap=0.0, profit=0.0, magic=request["magic"],
                    comment=request["comment"], time=1_000)
        return SimpleNamespace(retcode=self.retcode, deal=7, order=8, volume=request["volume"],
                               price=price, comment="done" if self.retcode == 10009 else "no")

    def initialize(self, path=None, **kw):
        self.initialize_calls += 1
        self.alive = self.init_ok
        return self.init_ok

    def shutdown(self):
        self.alive = False

    def last_error(self):
        return (-10004, "No IPC connection")

    def terminal_info(self):
        if not self.alive:
            return None
        return SimpleNamespace(trade_allowed=True, connected=True, ping_last=12345, build=5200)

    def account_info(self):
        return SimpleNamespace(login=self.login, server="Axi-US51-Demo", company="AxiCorp",
                               currency="USD", leverage=100, trade_mode=self.trade_mode,
                               trade_allowed=False)

    def symbol_select(self, symbol, enable):
        return symbol in ("US500", "XAUUSD")

    def symbols_get(self):
        return [SimpleNamespace(name="US500", path="Indices\\US500", description="S&P", visible=True)]

    def symbol_info(self, symbol):
        fields = {f: 0 for f in server.SPEC_FIELDS}
        fields.update(name=symbol, digits=2, swap_long=-5.1, description="S&P", path="Indices",
                      filling_mode=2)
        return SimpleNamespace(**fields)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(time=1_000, time_msc=1_000_123, bid=6600.1, ask=6600.6, last=0.0)

    def copy_rates_range(self, symbol, tf, start, end):
        self.rate_calls.append((symbol, tf, start, end))
        dtype = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
                 ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"),
                 ("real_volume", "<u8")]
        t0 = int(start.timestamp())
        # Three bars, the last starting exactly at `end`: MT5's end bound is inclusive.
        return np.array([(t0 + 60 * i, 1.0, 2.0, 0.5, 1.5, 10, 3, 0) for i in range(3)], dtype=dtype)

    def copy_ticks_range(self, symbol, start, end, flags):
        dtype = [("time_msc", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"),
                 ("flags", "<u4")]
        end_ms = int(end.timestamp() * 1000)
        return np.array([(end_ms - 1, 1.0, 1.1, 0.0, 6), (end_ms, 1.0, 1.1, 0.0, 6)], dtype=dtype)


@pytest.fixture
def fake():
    return FakeMT5()


@pytest.fixture
def bridge_addr(fake):
    srv = server.Server(("127.0.0.1", 0), server.Bridge(fake, terminal=None, login=LOGIN))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address
    srv.shutdown()
    srv.server_close()


async def test_account_reports_demo_and_trade_allowed(bridge_addr):
    async with BridgeClient(*bridge_addr) as c:
        acc = await c.account()
    assert acc.login == LOGIN and acc.trade_mode == "demo"
    assert acc.trade_allowed is False  # investor login in the fake
    assert acc.ping_ms == 12.3


async def test_rates_drop_the_inclusive_end_bar(bridge_addr, fake):
    async with BridgeClient(*bridge_addr) as c:
        bars = await c.rates("US500", "M1", 1_000_000, 1_000_120)
    assert bars["server_time"].tolist() == [1_000_000, 1_000_060]
    assert fake.rate_calls[0][1] == FakeMT5.TIMEFRAME_M1
    assert fake.rate_calls[0][2].tzinfo is not None  # aware, so MT5 applies no local zone


async def test_ticks_drop_the_inclusive_end_tick(bridge_addr):
    async with BridgeClient(*bridge_addr) as c:
        ticks = await c.ticks("XAUUSD", 1_000, 2_000)
    assert ticks["time_msc"].tolist() == [1_999_999]


async def test_ticks_range_is_capped(bridge_addr):
    async with BridgeClient(*bridge_addr) as c:
        with pytest.raises(BridgeError, match="at most"):
            await c.ticks("XAUUSD", 0, 27 * 3600)


async def test_spec_and_tick_carry_the_bridge_clock(bridge_addr):
    async with BridgeClient(*bridge_addr) as c:
        spec = await c.spec("US500")
        tick = await c.tick("US500")
    assert spec.result["swap_long"] == -5.1 and set(server.SPEC_FIELDS) <= set(spec.result)
    assert tick.result["time_msc"] == 1_000_123 and tick.utc_ms > 1_700_000_000_000


async def test_errors_answer_and_keep_the_connection(bridge_addr):
    async with BridgeClient(*bridge_addr) as c:
        with pytest.raises(BridgeError, match="not available"):
            await c.spec("NOPE")
        with pytest.raises(BridgeError, match="unknown op"):
            await c.request("order_send")  # the raw MT5 call is not an op; order_market is
        with pytest.raises(BridgeError, match="timeframe"):
            await c.rates("US500", "M7", 0, 60)
        assert (await c.account()).login == LOGIN


async def test_reconnects_after_the_terminal_goes_away(bridge_addr, fake):
    async with BridgeClient(*bridge_addr) as c:
        await c.account()
        fake.alive = False  # terminal restarted under the bridge
        await c.account()
    assert fake.initialize_calls == 2


async def test_wrong_account_is_refused(fake):
    fake.login = 99
    srv = server.Server(("127.0.0.1", 0), server.Bridge(fake, terminal=None, login=LOGIN))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async with BridgeClient(*srv.server_address) as c:
            with pytest.raises(BridgeError, match="expected #52000001"):
                await c.account()
            assert (await c.request("ping")).result == {"connected": False}
    finally:
        srv.shutdown()
        srv.server_close()


async def test_unreachable_bridge_raises():
    with pytest.raises(BridgeError, match="unreachable"):
        await BridgeClient("127.0.0.1", 1, timeout=2).account()


#: The only functions that may name an MT5 trading call (T151).
ORDER_PATH = {"op_positions", "op_order_market", "op_close_position", "_send"}


def test_trading_calls_live_only_in_the_order_path():
    """T151: the bridge's order path is three ops (plus their shared `_send`). Checked on the
    syntax tree, so a trading call added anywhere else fails here."""
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    trading = ("order_send", "order_check", "positions_get", "orders_get", "position_close")
    offenders = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        if fn.name in ORDER_PATH:
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Attribute) and n.attr in trading:
                offenders.append(f"{fn.name}: {n.attr}")
    assert offenders == []


async def test_order_market_refuses_a_missing_or_wrong_side_stop(bridge_addr, fake):
    async with BridgeClient(*bridge_addr) as c:
        with pytest.raises(BridgeError, match="volume and sl"):
            await c.request("order_market", symbol="XAUUSD", side="buy", volume=0.01)
        with pytest.raises(BridgeError, match="wrong side"):
            await c.order_market("XAUUSD", "buy", 0.01, sl=6700.0, magic=1)
        with pytest.raises(BridgeError, match="wrong side"):
            await c.order_market("XAUUSD", "sell", 0.01, sl=6500.0, magic=1)
    assert fake.sent == []


async def test_order_market_fills_at_the_ask_and_closes_at_the_bid(bridge_addr, fake):
    async with BridgeClient(*bridge_addr) as c:
        r = await c.order_market("XAUUSD", "buy", 0.01, sl=6200.0, magic=4242, comment="t")
        assert r.result["price"] == 6600.6 and r.result["quote_bid"] == 6600.1
        (pos,) = await c.positions(magic=4242)
        assert pos["side"] == "buy" and pos["sl"] == 6200.0
        assert await c.positions(magic=1) == []
        closed = await c.close_position(pos["ticket"])
        assert closed.result["price"] == 6600.1
        assert await c.positions() == []
    assert fake.sent[0]["type_filling"] == FakeMT5.ORDER_FILLING_IOC
    assert fake.sent[1]["position"] == pos["ticket"] and fake.sent[1]["type"] == FakeMT5.ORDER_TYPE_SELL


async def test_a_rejected_order_is_an_error_answer(bridge_addr, fake):
    fake.retcode = 10027  # AutoTrading disabled by the client terminal
    async with BridgeClient(*bridge_addr) as c:
        with pytest.raises(BridgeError, match="retcode 10027"):
            await c.order_market("XAUUSD", "buy", 0.01, sl=6200.0, magic=1)
        with pytest.raises(BridgeError, match="no open position"):
            await c.close_position(12345)
