"""Intraday GEX levels rebuilt from ThetaData history, at the live capture's 15-minute cadence
(T165, `plans/gex-intraday-backfill/README.md`).

For each (symbol, session D):

* per expiry listed on D, ``/v3/option/history/greeks/first_order`` at ``interval=15m``. The
  endpoint takes **one expiry per request** (checked on the terminal, 2026-10-14);
* the OI **reported on D** (= D-1's close), the OI a live capture carries all day (thetadata
  plan rule 3);
* per 15-minute stamp, one chain built through the provider's own ``_assemble``, then
  ``engine.compute_all`` for ``ALL`` and ``ZERO_DTE``, exactly as ``compute_and_store`` calls it;
* 5-minute bars from **1-minute** ``underlying_price`` on one contract of the nearest expiry
  (any contract carries the same underlying). There is no Stocks/Indices subscription. A sample
  stamped t is the price at t, so the bar [s, s+5) opens at the s sample, **closes at the s+5
  sample**, and its high/low are taken over the six samples s..s+5 inclusive. That still
  understates true extremes (the plan's *Known biases*).

Output: Parquet under ``<out>/<SYMBOL>/<D>.levels.parquet`` and ``.bars.parquet``. **Never**
``gex.*`` (plan rule 2). Per-contract rows are not kept. Resumable per file: an existing file is
not fetched again.

A stamp whose rows carry no ``underlying_price`` (SPX at 09:30, before the index prints) yields
no levels for that stamp, never a guessed spot.

Run only outside 09:15-16:30 ET: the homeserver's terminal also serves the live capture, and
serves four requests at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import time
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.modules.gex.gex.engine import ExpiryFilter, compute_all, to_frame
from app.modules.gex.providers.base import Underlying, UpstreamUnavailable
from app.modules.gex.providers.thetadata import ThetaDataClient, _assemble, _snapshot, vendor_roots

__all__ = [
    "FILTERS", "LOADER_VERSION", "bars_from_minutes", "levels_by_stamp", "load_bars", "load_levels", "main",
]

logger = logging.getLogger("app.modules.gex.gex.intraday_history")

_NY = ZoneInfo("America/New_York")
LOADER_VERSION = 1
FILTERS: tuple[ExpiryFilter, ...] = (ExpiryFilter.ALL, ExpiryFilter.ZERO_DTE)
_BLACKOUT = (dt.time(9, 15), dt.time(16, 30))
#: Terminal concurrency (the probe reports 4 max concurrent requests).
_CONCURRENCY = 4
_SOURCE = "thetadata-history"


def _stamp(raw: str) -> dt.datetime:
    return dt.datetime.fromisoformat(raw).replace(tzinfo=_NY).astimezone(dt.UTC)


def levels_by_stamp(
    underlying: Underlying,
    greeks: list[tuple[str, dict[str, str]]],
    oi: list[tuple[str, dict[str, str]]],
) -> list[dict]:
    """One row per (15-minute stamp, filter). Pure but for the engine's own logging."""
    by_ts: dict[str, list[tuple[str, dict[str, str]]]] = defaultdict(list)
    for root, row in greeks:
        by_ts[row["timestamp"]].append((root, row))
    out: list[dict] = []
    for raw in sorted(by_ts):
        captured_at = _stamp(raw)
        try:
            contracts, spot = _assemble(underlying, raw, by_ts[raw], oi)
            snap = _snapshot(underlying, raw, contracts, spot, captured_at, _SOURCE)
        except UpstreamUnavailable as exc:
            logger.info("intraday_history: %s %s skipped: %s", underlying.value, raw, exc)
            continue
        frame = to_frame(snap)
        for f in FILTERS:
            lv = compute_all(snap, f, frame=frame).levels
            out.append({
                "symbol": underlying.value, "captured_at": captured_at, "filter": f.value,
                "spot": spot, "flip_point": lv.flip_point, "call_wall": lv.call_wall,
                "put_wall": lv.put_wall, "net_gex": lv.net_gex, "contracts": len(contracts),
                "loader_version": LOADER_VERSION,
            })
    return out


def bars_from_minutes(symbol: str, minutes: list[dict[str, str]]) -> list[dict]:
    """5-minute OHLC from 1-minute ``underlying_price`` samples; zero prices are absent.

    The bar opening at s takes the samples s..s+5 inclusive: it opens at s and closes at s+5,
    so its close is the price at the moment it closes, as a live bar's is. A bar is written only
    when both of those samples exist.
    """
    px: dict[dt.datetime, float] = {}
    for row in minutes:
        p = float(row.get("underlying_price") or 0)
        if p > 0:
            t = dt.datetime.fromisoformat(row["timestamp"]).replace(tzinfo=_NY)
            px.setdefault(t.replace(second=0, microsecond=0), p)
    out: list[dict] = []
    for s in sorted(t for t in px if t.minute % 5 == 0 and dt.time(9, 30) <= t.time() < dt.time(16, 0)):
        end = s + dt.timedelta(minutes=5)
        if end not in px:
            continue
        v = [px[s + dt.timedelta(minutes=k)] for k in range(6) if s + dt.timedelta(minutes=k) in px]
        out.append({"symbol": symbol, "ts": s.astimezone(dt.UTC), "open": px[s], "high": max(v),
                    "low": min(v), "close": px[end], "samples": len(v), "loader_version": LOADER_VERSION})
    return out


async def _expirations(client: ThetaDataClient, root: str, cache: dict) -> list[dt.date]:
    if root not in cache:
        rows = await client.get_rows("/v3/option/list/expirations", {"symbol": root})
        cache[root] = sorted({dt.date.fromisoformat(r["expiration"]) for r in rows})
    return cache[root]


async def load_levels(
    client: ThetaDataClient, underlying: Underlying, day: dt.date, cache: dict, *, max_dte: int | None
) -> tuple[list[dict], dict]:
    """One session's levels, plus request and row counts."""
    d = f"{day:%Y%m%d}"
    sem = asyncio.Semaphore(_CONCURRENCY)
    stats = {"requests": 0, "rows": 0}

    async def get(path: str, params: dict) -> list[dict[str, str]]:
        async with sem:
            stats["requests"] += 1
            rows = await client.get_rows(path, params)
            stats["rows"] += len(rows)
            return rows

    greeks: list[tuple[str, dict[str, str]]] = []
    oi: list[tuple[str, dict[str, str]]] = []
    for root in vendor_roots(underlying):
        exps = [e for e in await _expirations(client, root, cache)
                if e >= day and (max_dte is None or (e - day).days <= max_dte)]
        results = await asyncio.gather(
            get("/v3/option/history/open_interest", {"symbol": root, "expiration": "*", "date": d}),
            *(get("/v3/option/history/greeks/first_order",
                  {"symbol": root, "expiration": f"{e:%Y%m%d}", "date": d, "interval": "15m"})
              for e in exps),
        )
        oi += [(root, r) for r in results[0]]
        for rows in results[1:]:
            greeks += [(root, r) for r in rows]
    if not greeks:
        return [], stats
    return await asyncio.to_thread(levels_by_stamp, underlying, greeks, oi), stats


async def load_bars(client: ThetaDataClient, underlying: Underlying, day: dt.date, cache: dict) -> list[dict]:
    """One session's 5-minute bars from the nearest expiry's 1-minute underlying price."""
    root = vendor_roots(underlying)[-1]  # SPXW for SPX: the dailies
    exp = next((e for e in await _expirations(client, root, cache) if e >= day), None)
    if exp is None:
        return []
    minutes = await client.get_rows("/v3/option/history/greeks/first_order", {
        "symbol": root, "expiration": f"{exp:%Y%m%d}", "strike_range": "0", "right": "call",
        "date": f"{day:%Y%m%d}", "interval": "1m"})
    return bars_from_minutes(underlying.value, minutes)


def _sessions(start: dt.date, end: dt.date) -> list[dt.date]:
    from app.modules.gex.jobs.calendar import is_trading_day

    days, d = [], start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += dt.timedelta(days=1)
    return days


async def _run(args: argparse.Namespace) -> None:
    out = Path(args.out)
    cache: dict = {}
    async with ThetaDataClient(timeout=600) as client:
        for sym in args.symbols.split(","):
            underlying = Underlying(sym.strip().upper())
            folder = out / underlying.value
            folder.mkdir(parents=True, exist_ok=True)
            for day in _sessions(args.start, args.end):
                lv_path, bar_path = folder / f"{day}.levels.parquet", folder / f"{day}.bars.parquet"
                if lv_path.exists() and bar_path.exists():
                    continue
                now = dt.datetime.now(_NY)
                if not args.ignore_hours and now.weekday() < 5 and _BLACKOUT[0] <= now.time() <= _BLACKOUT[1]:
                    raise SystemExit(f"stopping at {now:%H:%M} ET: inside capture hours; resume later")
                t0 = time.monotonic()
                if not bar_path.exists():
                    bars = await load_bars(client, underlying, day, cache)
                    pd.DataFrame(bars).to_parquet(bar_path)
                    logger.warning("intraday_history %s %s: %d bars", underlying.value, day, len(bars))
                if not lv_path.exists():
                    levels, stats = await load_levels(client, underlying, day, cache, max_dte=args.max_dte)
                    pd.DataFrame(levels).to_parquet(lv_path)
                    logger.warning(
                        "intraday_history %s %s: %d level rows, %d requests, %d rows, %.1fs",
                        underlying.value, day, len(levels), stats["requests"], stats["rows"],
                        time.monotonic() - t0,
                    )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--symbols", required=True)
    p.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--max-dte", type=int, default=None, help="skip expiries further out (pilot comparison)")
    p.add_argument("--ignore-hours", action="store_true", help="tests only")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
