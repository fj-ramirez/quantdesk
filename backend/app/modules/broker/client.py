"""Async client for the `mt5` container's bridge (T143, T151; plans/charter-mt5/README.md).

The bridge (`docker/mt5/bridge/server.py`) speaks newline-delimited JSON over TCP. This is its
only client, used by the `broker-ingest` worker (T144). One connection, requests serialised;
a dropped connection is re-opened on the next request rather than retried in a loop here,
because the caller is a scheduled job and the next tick is the retry.

Times come back exactly as MT5 reports them, in **server wall-clock epoch** (`server_time`,
`time_msc`), next to the bridge's own UTC clock (`utc_ms`). Nothing here converts them:
measuring the offset is T144's job (decision 3 of the plan).
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass
from typing import Any, Self

import pandas as pd

__all__ = ["Account", "BridgeClient", "BridgeError", "Reply"]

#: An answer can carry a day of ticks; the default 64 KiB stream limit would split it.
_STREAM_LIMIT = 64 * 1024 * 1024


class BridgeError(RuntimeError):
    """The bridge answered with an error, or could not be reached."""


@dataclass(frozen=True, slots=True)
class Reply:
    """One successful answer: its `result` and the bridge's UTC clock when it answered."""

    result: Any
    utc_ms: int


@dataclass(frozen=True, slots=True)
class Account:
    login: int
    server: str
    company: str
    currency: str
    leverage: int
    trade_mode: str  # "demo", "contest" or "real"
    trade_allowed: bool  # False under an investor (read-only) login
    connected: bool
    ping_ms: float
    terminal_build: int


class BridgeClient:
    def __init__(self, host: str, port: int, *, timeout: float = 60.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = self._writer = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def request(self, op: str, **params: Any) -> Reply:
        async with self._lock:
            rid = next(self._ids)
            try:
                if self._writer is None:
                    self._reader, self._writer = await asyncio.wait_for(
                        asyncio.open_connection(self.host, self.port, limit=_STREAM_LIMIT),
                        self.timeout,
                    )
                assert self._reader is not None
                self._writer.write((json.dumps({"id": rid, "op": op, **params}) + "\n").encode())
                await self._writer.drain()
                line = await asyncio.wait_for(self._reader.readline(), self.timeout)
            except (TimeoutError, OSError, asyncio.LimitOverrunError, ValueError) as e:
                await self.close()
                raise BridgeError(f"bridge at {self.host}:{self.port} unreachable: {e!r}") from e
            if not line:
                await self.close()
                raise BridgeError("bridge closed the connection")
        answer = json.loads(line)
        if answer.get("re") != rid:
            await self.close()
            raise BridgeError(f"answer for request {answer.get('re')}, expected {rid}")
        if not answer.get("ok"):
            raise BridgeError(answer.get("error") or "unknown bridge error")
        return Reply(answer["result"], int(answer["utc_ms"]))

    # ------------------------------------------------------------------ typed operations

    async def account(self) -> Account:
        return Account(**(await self.request("account")).result)

    async def symbols(self) -> list[dict]:
        return (await self.request("symbols")).result

    async def spec(self, symbol: str) -> Reply:
        return await self.request("spec", symbol=symbol)

    async def tick(self, symbol: str) -> Reply:
        return await self.request("tick", symbol=symbol)

    async def rates(self, symbol: str, timeframe: str, start: int, end: int) -> pd.DataFrame:
        """Bars with open time in [start, end), both **server-time** epoch seconds.

        Columns `server_time, open, high, low, close, tick_volume, spread, real_volume`;
        `spread` is in points. The forming bar is included when the range reaches it.
        """
        r = (await self.request("rates_range", symbol=symbol, timeframe=timeframe,
                                **{"from": start, "to": end})).result
        cols = ["server_time", "open", "high", "low", "close", "tick_volume", "spread",
                "real_volume"]
        return pd.DataFrame({c: r[c] for c in cols}, columns=cols)

    # ------------------------------------------------------------- the order path (T151)
    # Only `app.modules.broker.executor` calls these. Every order carries a stop-loss; the bridge
    # refuses one without.

    async def positions(self, *, symbol: str | None = None, magic: int | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol
        if magic is not None:
            params["magic"] = magic
        return (await self.request("positions", **params)).result

    async def order_market(self, symbol: str, side: str, volume: float, sl: float, *,
                           magic: int, comment: str = "", deviation: int = 50) -> Reply:
        return await self.request("order_market", symbol=symbol, side=side, volume=volume, sl=sl,
                                  magic=magic, comment=comment, deviation=deviation)

    async def close_position(self, ticket: int, *, comment: str = "", deviation: int = 50) -> Reply:
        return await self.request("close_position", ticket=ticket, comment=comment,
                                  deviation=deviation)

    async def ticks(self, symbol: str, start: int, end: int) -> pd.DataFrame:
        """Ticks with `time_msc` in [start, end) seconds, server time; at most 26 hours."""
        r = (await self.request("ticks_range", symbol=symbol, **{"from": start, "to": end})).result
        cols = ["time_msc", "bid", "ask", "last", "flags"]
        return pd.DataFrame({c: r[c] for c in cols}, columns=cols)
