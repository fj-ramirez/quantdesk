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

Run only outside 09:15-17:00 ET on weekdays: the homeserver's terminal also serves the live capture, and
serves four requests at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.modules.gex.gex.engine import ExpiryFilter, compute_all, to_frame
from app.modules.gex.providers.base import Underlying, UpstreamUnavailable
from app.modules.gex.providers.thetadata import ThetaDataClient, _assemble, _snapshot, vendor_roots

__all__ = [
    "FILTERS", "LOADER_VERSION", "Fetched", "bars_from_minutes", "compact", "fetch_session", "levels_by_stamp",
    "load_bars", "main", "seconds_until_window",
]

logger = logging.getLogger("app.modules.gex.gex.intraday_history")

_NY = ZoneInfo("America/New_York")
LOADER_VERSION = 1
FILTERS: tuple[ExpiryFilter, ...] = (ExpiryFilter.ALL, ExpiryFilter.ZERO_DTE)
#: Weekday hours the live work owns the terminal (see `seconds_until_window`).
_BLACKOUT = (dt.time(9, 15), dt.time(17, 0))
#: Terminal concurrency (the probe reports 4 max concurrent requests).
_CONCURRENCY = 4
_SOURCE = "thetadata-history"

#: The greeks columns `_assemble` reads. Everything else in the response is dropped on arrival.
_TEXT = ("root", "symbol", "expiration", "right", "timestamp")
_NUM = ("strike", "bid", "ask", "delta", "theta", "vega", "implied_vol", "underlying_price")


def _peak_rss_mb() -> int | None:
    """This process's peak resident memory, or None where `resource` does not exist (Windows)."""
    try:
        import resource
    except ImportError:
        return None
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024  # KiB on Linux


def _stamp(raw: str) -> dt.datetime:
    return dt.datetime.fromisoformat(raw).replace(tzinfo=_NY).astimezone(dt.UTC)


def compact(root: str, rows: list[dict[str, str]]) -> pd.DataFrame:
    """One response as columns: floats and categoricals instead of a dict of strings per row.

    An SPX session is ~800k greeks rows; held as `csv.DictReader` dicts it overran the 1 GiB
    container in the pilot. A value that does not parse becomes NaN, which :func:`_rows` turns
    back into `""`, the empty cell `_assemble` already reads as `None`.
    """
    df = pd.DataFrame.from_records(rows, columns=[c for c in _TEXT if c != "root"] + list(_NUM))
    df.insert(0, "root", root)
    for c in _NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in _TEXT:
        df[c] = df[c].astype("category")
    return df


def _rows(df: pd.DataFrame) -> list[tuple[str, dict[str, str]]]:
    """One stamp's rows back in the `(root, csv_row)` shape `_assemble` takes."""
    out: list[tuple[str, dict[str, str]]] = []
    for rec in df.to_dict("records"):
        row = {c: ("" if pd.isna(rec[c]) else str(rec[c])) for c in _TEXT[1:]}
        row.update({c: ("" if pd.isna(rec[c]) else repr(float(rec[c]))) for c in _NUM})
        out.append((str(rec["root"]), row))
    return out


def levels_by_stamp(
    underlying: Underlying,
    greeks: pd.DataFrame,
    oi: list[tuple[str, dict[str, str]]],
) -> list[dict]:
    """One row per (15-minute stamp, filter). Only one stamp's chain is materialised at a time."""
    out: list[dict] = []
    for raw, g in greeks.groupby("timestamp", observed=True, sort=True):
        raw = str(raw)
        captured_at = _stamp(raw)
        try:
            contracts, spot = _assemble(underlying, raw, _rows(g), oi)
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


@dataclass
class Fetched:
    """One session's raw inputs, held until its levels are computed."""

    day: dt.date
    greeks: pd.DataFrame | None
    oi: list[tuple[str, dict[str, str]]]
    bars: list[dict]
    stats: dict = field(default_factory=dict)


async def fetch_session(
    client: ThetaDataClient, underlying: Underlying, day: dt.date, cache: dict, *,
    max_dte: int | None, need_levels: bool = True, need_bars: bool = True,
) -> Fetched:
    """Everything the terminal has to serve for one session: greeks per expiry, OI, 1-minute bars."""
    d = f"{day:%Y%m%d}"
    sem = asyncio.Semaphore(_CONCURRENCY)
    stats = {"requests": 0, "rows": 0}
    frames: list[pd.DataFrame] = []

    async def greeks_for(root: str, expiry: dt.date) -> None:
        async with sem:
            stats["requests"] += 1
            rows = await client.get_rows("/v3/option/history/greeks/first_order",
                                         {"symbol": root, "expiration": f"{expiry:%Y%m%d}", "date": d,
                                          "interval": "15m"})
        stats["rows"] += len(rows)
        if rows:
            frames.append(compact(root, rows))  # the dicts are dropped here, one response at a time

    t0 = time.monotonic()
    bars = await load_bars(client, underlying, day, cache) if need_bars else []
    oi: list[tuple[str, dict[str, str]]] = []
    if need_levels:
        for root in vendor_roots(underlying):
            exps = [e for e in await _expirations(client, root, cache)
                    if e >= day and (max_dte is None or (e - day).days <= max_dte)]
            stats["requests"] += 1
            oi += [(root, r) for r in await client.get_rows(
                "/v3/option/history/open_interest", {"symbol": root, "expiration": "*", "date": d})]
            await asyncio.gather(*(greeks_for(root, e) for e in exps))
    stats["fetch_s"] = time.monotonic() - t0
    greeks = None
    if frames:
        greeks = pd.concat(frames, ignore_index=True)
        for c in _TEXT:  # concat of differing categories falls back to object; re-compact
            greeks[c] = greeks[c].astype("category")
        frames.clear()
    return Fetched(day, greeks, oi, bars, stats)


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


def seconds_until_window(now: dt.datetime) -> float:
    """0 when the terminal is free for the backfill; else the seconds until 17:00 ET.

    Weekdays 09:15-17:00 ET belong to the live work on the same terminal: the 15-minute
    captures, the 16:20 EOD capture and the 16:45 sector-ETF capture. Holidays are treated as
    weekdays, which only costs idle hours.
    """
    t = now.astimezone(_NY)
    if t.weekday() >= 5 or not _BLACKOUT[0] <= t.time() < _BLACKOUT[1]:
        return 0.0
    return (dt.datetime.combine(t.date(), _BLACKOUT[1], tzinfo=_NY) - t).total_seconds()


async def _wait_for_window(not_before: dt.datetime | None) -> None:
    while True:
        now = dt.datetime.now(dt.UTC)
        wait = max(seconds_until_window(now), (not_before - now).total_seconds() if not_before else 0.0)
        if wait <= 0:
            return
        logger.warning("intraday_history: waiting %.0f min (capture hours or --not-before)", wait / 60)
        await asyncio.sleep(min(wait, 3600) + 1)


def _write(folder: Path, underlying: Underlying, f: Fetched, t_start: float) -> None:
    """Compute the levels and write whichever file was missing. Runs in a worker thread."""
    lv_path, bar_path = folder / f"{f.day}.levels.parquet", folder / f"{f.day}.bars.parquet"
    if not bar_path.exists():
        pd.DataFrame(f.bars).to_parquet(bar_path)
    if lv_path.exists():
        return
    t1 = time.monotonic()
    levels = [] if f.greeks is None else levels_by_stamp(underlying, f.greeks, f.oi)
    pd.DataFrame(levels).to_parquet(lv_path)
    logger.warning(
        "intraday_history %s %s: %d level rows, %d bars, %d requests, %d rows, %.1fs "
        "(fetch %.1fs, compute %.1fs), peak RSS %s MB",
        underlying.value, f.day, len(levels), len(f.bars), f.stats["requests"], f.stats["rows"],
        time.monotonic() - t_start, f.stats.get("fetch_s", 0), time.monotonic() - t1, _peak_rss_mb(),
    )


async def _run(args: argparse.Namespace) -> int:
    """Load every missing (symbol, session), fetching the next session while computing this one.

    A session that fails is logged and left without files, so the next run retries it; the run
    goes on. Returns the number of failed sessions.
    """
    if args.end >= dt.datetime.now(_NY).date():
        raise SystemExit(f"--end {args.end} must be before today: today's OI is not in history yet")
    out = Path(args.out)
    cache: dict = {}
    failed = 0
    async with ThetaDataClient(timeout=600) as client:
        for sym in args.symbols.split(","):
            underlying = Underlying(sym.strip().upper())
            folder = out / underlying.value
            folder.mkdir(parents=True, exist_ok=True)

            def missing(day: dt.date, folder: Path = folder) -> tuple[bool, bool]:
                return (not (folder / f"{day}.levels.parquet").exists(),
                        not (folder / f"{day}.bars.parquet").exists())

            todo = [d for d in _sessions(args.start, args.end) if any(missing(d))]
            logger.warning("intraday_history %s: %d sessions to load", underlying.value, len(todo))

            def start(day: dt.date, underlying: Underlying = underlying, missing=missing) -> asyncio.Task:
                need_levels, need_bars = missing(day)
                return asyncio.create_task(fetch_session(
                    client, underlying, day, cache, max_dte=args.max_dte,
                    need_levels=need_levels, need_bars=need_bars))

            pending: asyncio.Task | None = None
            for i, day in enumerate(todo):
                if pending is None:
                    if not args.ignore_hours:
                        await _wait_for_window(args.not_before)
                    pending = start(day)
                t0 = time.monotonic()
                try:
                    fetched = await pending
                except Exception:
                    logger.exception("intraday_history %s %s: fetch failed; left for the next run",
                                     underlying.value, day)
                    failed += 1
                    pending = None
                    continue
                # the next session's requests run while this one computes, but never into capture hours
                nxt = todo[i + 1] if i + 1 < len(todo) else None
                in_window = args.ignore_hours or seconds_until_window(dt.datetime.now(dt.UTC)) == 0
                pending = start(nxt) if nxt is not None and in_window else None
                try:
                    await asyncio.to_thread(_write, folder, underlying, fetched, t0)
                except Exception:
                    logger.exception("intraday_history %s %s: compute failed; left for the next run",
                                     underlying.value, day)
                    failed += 1
    logger.warning("intraday_history: done, %d sessions failed", failed)
    return failed


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--symbols", required=True, help="in load order, e.g. SPX,SPY,QQQ,GLD,IWM,DIA")
    p.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--max-dte", type=int, default=None, help="skip expiries further out (pilot comparison)")
    p.add_argument("--not-before", type=dt.datetime.fromisoformat, default=None,
                   help="ISO datetime with offset; sleep until then before the first request")
    p.add_argument("--ignore-hours", action="store_true", help="tests only")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    failed = asyncio.run(_run(args))
    # Failed sessions have no files; a restart (`restart: on-failure`) retries exactly those.
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
