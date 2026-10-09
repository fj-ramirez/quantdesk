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

    def __init__(self, *, login=LOGIN, trade_mode=0, init_ok=True):
        self.login, self.trade_mode, self.init_ok = login, trade_mode, init_ok
        self.alive = False
        self.initialize_calls = 0
        self.rate_calls: list[tuple] = []

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
        fields.update(name=symbol, digits=2, swap_long=-5.1, description="S&P", path="Indices")
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
            await c.request("order_send")
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


def test_bridge_never_calls_a_trading_function():
    """Decision 2: the bridge has no order path. Checked on the syntax tree, not the text, so
    the docstring may say what is absent."""
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    forbidden = {n for n in names if n.startswith(("order_", "positions_", "orders_"))}
    assert forbidden == set()
