"""Bridge between one MT5 terminal and the desk (T143, T151; plans/charter-mt5/README.md).

Runs in the `mt5` container under Wine, in the embedded **Windows** Python that has the
official `MetaTrader5` package. It attaches to the terminal running beside it and serves
newline-delimited JSON over TCP on the compose network. `broker-ingest` is its only client.
Standard library only, apart from `MetaTrader5` itself.

    request   {"id": 1, "op": "rates_range", "symbol": "US500", "timeframe": "M1",
               "from": 1759900000, "to": 1759986400}
    answer    {"re": 1, "ok": true, "utc_ms": 1759990000123, "result": {...}}
              {"re": 1, "ok": false, "utc_ms": ..., "error": "..."}

**The order path is three operations and nothing else** (T151, step 4 of the plan):
`positions`, `order_market` and `close_position`. They are the only place in this file that may
name a trading function, and `tests/test_broker_bridge.py` checks that on the syntax tree. The
`executor` worker is their only intended caller. `order_market` refuses an order without a
stop-loss on the correct side of the price. The executor attaches one anyway, and this is the
second line of that rule. Whether the terminal may trade at all is the terminal's own setting
(`MT5_ALLOW_TRADING` in `entrypoint.sh`). Off, MT5 rejects every order with "AutoTrading
disabled", which this bridge reports as an error answer.

**Time.** MT5 reports and filters bar and tick times in the broker's *server wall-clock*,
expressed as epoch numbers that are not UTC. This bridge passes them through untouched, with
`from`/`to` interpreted the same way, and stamps every answer with the container's own UTC
clock (`utc_ms`). Converting to UTC is the caller's job: decision 3 of the plan says the offset
is measured, never configured, and measuring it needs both clocks side by side.

The connection to the terminal is checked before every request and re-established when the
terminal has gone away. A request that arrives while it is down gets an error answer, never a
hang.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import socketserver
import sys
import threading
import time

#: Longest range one `ticks_range` request may cover, in seconds. A busy day of XAUUSD is
#: 100k+ ticks; the caller chunks by hour or day and this cap keeps one answer bounded.
MAX_TICK_RANGE_S = 26 * 3600

#: How long `initialize()` may wait for the terminal. Short on purpose: a terminal that is not
#: logged in (a wrong server name in `.env` was the first case, 2026-10-08) should get a prompt
#: "not connected" answer, and the next request retries. At MT5's 60 s the healthcheck gave up
#: first and the answer hit a closed socket.
INIT_TIMEOUT_MS = 10_000

#: MT5 timeframe constants by the names the desk uses.
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

#: The symbol fields a spec snapshot carries (T144 stores one row per change).
SPEC_FIELDS = (
    "digits", "point", "trade_tick_size", "trade_tick_value", "trade_contract_size",
    "volume_min", "volume_max", "volume_step", "trade_stops_level", "trade_freeze_level",
    "swap_mode", "swap_long", "swap_short", "swap_rollover3days", "currency_base",
    # Not `spread`: that is the *current* spread, it changes every few seconds, and in a spec it
    # made every hourly check a "change" (DJ30.fs, 2026-10-08). Spreads are stored per bar.
    "currency_profit", "currency_margin", "trade_mode", "spread_float",
    "description", "path",
    # Futures-based CFDs (the `.fs` symbols the user trades) expire and roll. Their dates are
    # how a roll is detected, so the price jump at a roll is not mistaken for a move.
    "start_time", "expiration_time",
)


def log(*parts) -> None:
    print(*parts, file=sys.stderr, flush=True)


def _utc_dt(epoch_s: float) -> dt.datetime:
    # MT5 needs aware datetimes so it does not apply the local zone; the epoch is server time.
    return dt.datetime.fromtimestamp(epoch_s, tz=dt.UTC)


class BridgeError(Exception):
    pass


class Bridge:
    """The operations. `mt5` is the `MetaTrader5` module, injected so tests can pass a fake."""

    def __init__(self, mt5, *, terminal: str | None, login: int | None) -> None:
        self.mt5 = mt5
        self.terminal = terminal
        self.login = login
        self.lock = threading.Lock()  # the package is one process-wide connection
        self.connected = False

    # ---------------------------------------------------------------- connection

    def _ensure(self) -> None:
        mt5 = self.mt5
        if self.connected and mt5.terminal_info() is not None:
            return
        if self.connected:
            log("lost the terminal:", mt5.last_error())
            mt5.shutdown()
            self.connected = False
        kwargs = {"portable": True, "timeout": INIT_TIMEOUT_MS}
        ok = mt5.initialize(path=self.terminal, **kwargs) if self.terminal else mt5.initialize(**kwargs)
        if not ok:
            raise BridgeError(f"terminal not connected: {mt5.last_error()}")
        acc = mt5.account_info()
        if acc is None:
            mt5.shutdown()
            raise BridgeError(f"terminal is not logged in yet: {mt5.last_error()}")
        if self.login is not None and acc.login != self.login:
            mt5.shutdown()
            raise BridgeError(f"terminal is logged into #{acc.login}, expected #{self.login}")
        self.connected = True
        log(f"attached to the terminal: #{acc.login} on {acc.server}")

    def handle(self, req: dict) -> dict:
        op = req.get("op")
        fn = getattr(self, f"op_{op}", None) if isinstance(op, str) else None
        try:
            if fn is None:
                raise BridgeError(f"unknown op {op!r}")
            with self.lock:
                if op != "ping":
                    self._ensure()
                result = fn(req)
            return {"re": req.get("id"), "ok": True, "utc_ms": _now_ms(), "result": result}
        except BridgeError as e:
            return {"re": req.get("id"), "ok": False, "utc_ms": _now_ms(), "error": str(e)}
        except Exception as e:  # noqa: BLE001 -- a bug here must answer, not kill the connection
            log("op failed:", op, repr(e))
            return {"re": req.get("id"), "ok": False, "utc_ms": _now_ms(),
                    "error": f"{type(e).__name__}: {e}"}

    def _fail(self, what: str):
        raise BridgeError(f"{what} failed: {self.mt5.last_error()}")

    def _select(self, symbol) -> str:
        if not isinstance(symbol, str) or not symbol:
            raise BridgeError("symbol is required")
        if not self.mt5.symbol_select(symbol, True):
            raise BridgeError(f"symbol {symbol!r} not available: {self.mt5.last_error()}")
        return symbol

    # ---------------------------------------------------------------- operations

    def op_ping(self, req: dict) -> dict:
        return {"connected": self.connected}

    def op_account(self, req: dict) -> dict:
        mt5 = self.mt5
        acc, term = mt5.account_info(), mt5.terminal_info()
        if acc is None or term is None:
            self._fail("account_info")
        modes = {
            mt5.ACCOUNT_TRADE_MODE_DEMO: "demo",
            mt5.ACCOUNT_TRADE_MODE_CONTEST: "contest",
            mt5.ACCOUNT_TRADE_MODE_REAL: "real",
        }
        return {
            "login": acc.login, "server": acc.server, "company": acc.company,
            "currency": acc.currency, "leverage": acc.leverage,
            "trade_mode": modes.get(acc.trade_mode, str(acc.trade_mode)),
            # False under an investor (read-only) login: MT5 itself will not trade from it.
            "trade_allowed": bool(acc.trade_allowed and term.trade_allowed),
            "connected": bool(term.connected), "ping_ms": round(term.ping_last / 1000, 1),
            "terminal_build": term.build,
            # "Max bars in chart" as the terminal applied it: the cap on the history it serves.
            "max_bars": term.maxbars,
        }

    def op_symbols(self, req: dict) -> list[dict]:
        rows = self.mt5.symbols_get()
        if rows is None:
            self._fail("symbols_get")
        return [{"name": s.name, "path": s.path, "description": s.description,
                 "visible": bool(s.visible)} for s in rows]

    def op_spec(self, req: dict) -> dict:
        info = self.mt5.symbol_info(self._select(req.get("symbol")))
        if info is None:
            self._fail("symbol_info")
        return {"symbol": info.name, **{f: getattr(info, f) for f in SPEC_FIELDS}}

    def op_tick(self, req: dict) -> dict:
        tick = self.mt5.symbol_info_tick(self._select(req.get("symbol")))
        if tick is None:
            self._fail("symbol_info_tick")
        return {"server_time": tick.time, "time_msc": tick.time_msc,
                "bid": tick.bid, "ask": tick.ask, "last": tick.last}

    def _range(self, req: dict) -> tuple[float, float]:
        try:
            start, end = float(req["from"]), float(req["to"])
        except (KeyError, TypeError, ValueError):
            raise BridgeError("from and to (server-time epoch seconds) are required") from None
        if end <= start:
            raise BridgeError("to must be after from")
        return start, end

    def op_rates_range(self, req: dict) -> dict:
        """Bars whose open time is in [from, to), server time. The forming bar is included if
        the range reaches it: dropping it is the caller's job, because only the caller knows
        what "now" is in server time."""
        mt5 = self.mt5
        symbol = self._select(req.get("symbol"))
        tf = req.get("timeframe")
        if tf not in TIMEFRAMES:
            raise BridgeError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
        start, end = self._range(req)
        rows = mt5.copy_rates_range(symbol, getattr(mt5, f"TIMEFRAME_{tf}"),
                                    _utc_dt(start), _utc_dt(end))
        if rows is None:
            self._fail("copy_rates_range")
        rows = [r for r in rows if r["time"] < end]  # MT5's end bound is inclusive
        cols = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")
        out = {c: [_py(r[c]) for r in rows] for c in cols}
        out["server_time"] = out.pop("time")
        return {"symbol": symbol, "timeframe": tf, **out}

    def op_ticks_range(self, req: dict) -> dict:
        """Ticks in [from, to), server time, at most :data:`MAX_TICK_RANGE_S` wide."""
        mt5 = self.mt5
        symbol = self._select(req.get("symbol"))
        start, end = self._range(req)
        if end - start > MAX_TICK_RANGE_S:
            raise BridgeError(f"a ticks_range covers at most {MAX_TICK_RANGE_S} s")
        rows = mt5.copy_ticks_range(symbol, _utc_dt(start), _utc_dt(end), mt5.COPY_TICKS_ALL)
        if rows is None:
            self._fail("copy_ticks_range")
        end_ms = int(end * 1000)
        rows = [r for r in rows if r["time_msc"] < end_ms]
        cols = ("time_msc", "bid", "ask", "last", "flags")
        return {"symbol": symbol, **{c: [_py(r[c]) for r in rows] for c in cols}}


    # ---------------------------------------------------------------- the order path (T151)

    def _position_row(self, p) -> dict:
        return {"ticket": p.ticket, "symbol": p.symbol, "side": "buy" if p.type == 0 else "sell",
                "volume": p.volume, "price_open": p.price_open, "sl": p.sl, "tp": p.tp,
                "price_current": p.price_current, "swap": p.swap, "profit": p.profit,
                "magic": p.magic, "comment": p.comment, "server_time": p.time}

    def op_positions(self, req: dict) -> list[dict]:
        """Open positions, optionally for one symbol and one magic number."""
        symbol = req.get("symbol")
        rows = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        if rows is None:
            self._fail("positions_get")
        magic = req.get("magic")
        return [self._position_row(p) for p in rows if magic is None or p.magic == magic]

    def _filling(self, symbol: str) -> int:
        mt5 = self.mt5
        mode = mt5.symbol_info(symbol).filling_mode
        if mode & 1:
            return mt5.ORDER_FILLING_FOK
        if mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict) -> dict:
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(request["symbol"])
        result = mt5.order_send(request)
        if result is None:
            self._fail("order_send")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise BridgeError(f"order rejected: retcode {result.retcode} ({result.comment})")
        return {"retcode": result.retcode, "deal": result.deal, "order": result.order,
                "volume": result.volume, "price": result.price, "comment": result.comment,
                "quote_bid": tick.bid if tick else None, "quote_ask": tick.ask if tick else None}

    def op_order_market(self, req: dict) -> dict:
        """A market order with a mandatory stop-loss. `side` is buy or sell."""
        mt5 = self.mt5
        symbol = self._select(req.get("symbol"))
        side = req.get("side")
        if side not in ("buy", "sell"):
            raise BridgeError("side must be buy or sell")
        try:
            volume, sl = float(req["volume"]), float(req["sl"])
        except (KeyError, TypeError, ValueError):
            raise BridgeError("volume and sl are required") from None
        if volume <= 0:
            raise BridgeError("volume must be positive")
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            self._fail("symbol_info_tick")
        price = tick.ask if side == "buy" else tick.bid
        if (side == "buy" and not sl < price) or (side == "sell" and not sl > price):
            raise BridgeError(f"stop-loss {sl} is on the wrong side of {price} for a {side}")
        return self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price, "sl": sl, "deviation": int(req.get("deviation", 50)),
            "magic": int(req.get("magic", 0)), "comment": str(req.get("comment", ""))[:31],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(symbol),
        })

    def op_close_position(self, req: dict) -> dict:
        """Close one position by ticket, in full, at market."""
        mt5 = self.mt5
        try:
            ticket = int(req["ticket"])
        except (KeyError, TypeError, ValueError):
            raise BridgeError("ticket is required") from None
        rows = mt5.positions_get(ticket=ticket)
        if not rows:
            raise BridgeError(f"no open position #{ticket}")
        p = rows[0]
        tick = mt5.symbol_info_tick(p.symbol)
        if tick is None:
            self._fail("symbol_info_tick")
        buy_back = p.type == 1
        return self._send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
            "position": ticket,
            "type": mt5.ORDER_TYPE_BUY if buy_back else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if buy_back else tick.bid, "deviation": int(req.get("deviation", 50)),
            "magic": p.magic, "comment": str(req.get("comment", ""))[:31],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(p.symbol),
        })


def _py(v):
    """numpy scalar -> plain Python, so `json` can write it."""
    return v.item() if hasattr(v, "item") else v


def _now_ms() -> int:
    return int(time.time() * 1000)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            self._serve()
        except (ConnectionError, OSError):
            pass  # the client went away mid-answer; it reconnects if it still wants one

    def _serve(self) -> None:
        bridge: Bridge = self.server.bridge  # type: ignore[attr-defined]
        for line in self.rfile:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError("not an object")  # noqa: TRY004 -- caught just below
            except ValueError:
                answer = {"re": None, "ok": False, "utc_ms": _now_ms(), "error": "unreadable request"}
            else:
                answer = bridge.handle(req)
            self.wfile.write((json.dumps(answer, separators=(",", ":")) + "\n").encode())
            self.wfile.flush()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], bridge: Bridge) -> None:
        super().__init__(addr, _Handler)
        self.bridge = bridge


def main() -> None:
    ap = argparse.ArgumentParser(description="MT5 bridge for the desk")
    ap.add_argument("--terminal", help=r"Z:\...\terminal64.exe")
    ap.add_argument("--login", type=int, help="refuse to serve any other account")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=18812)
    args = ap.parse_args()

    import MetaTrader5 as mt5

    log(f"bridge starting on {args.host}:{args.port}, MetaTrader5 {mt5.__version__}")
    bridge = Bridge(mt5, terminal=args.terminal, login=args.login)
    try:
        with Server((args.host, args.port), bridge) as srv:
            srv.serve_forever()
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
