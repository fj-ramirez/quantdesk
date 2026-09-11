"""Five-minute intraday bar polling (TASKS.md T74, plans/continuous-feed/05-intraday-bars.md).

Sibling of `app/jobs/bars.py`, which owns the settled daily series. The two never write to each
other's table: `daily_bars` holds settled sessions only, because every scan module -- ATR,
realized vol, breakout levels, the RRG approximation -- reads it and a partial row would poison
all of them. The in-progress daily bar users actually want is *derived* from this table at read
time instead; see :func:`session_daily_bar`.

Scope, decided with the user 2026-09-11: six symbols, not the 125-symbol scan universe. Six
requests per poll is ~72/hour at a five-minute cadence; the universe would be ~1,500, which is
where an unofficial endpoint starts throttling -- and losing Yahoo would take the *daily* bars
pipeline down with it, since both run on the same provider.

Like every other job in this package, nothing here raises for an ordinary provider failure: one
symbol's bad poll must never stop the other five, and a poll missed entirely costs nothing
because the next one re-fetches the whole session.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.bars import IntradayBar, LiveQuote
from app.providers.bars import ProviderError
from app.providers.yahoo import YahooBarProvider
from app.storage.bars_repository import read_intraday_bars, upsert_intraday_bars

__all__ = [
    "IntradayBarsResult",
    "session_daily_bar",
    "update_intraday_bars",
    "update_one_symbol_intraday",
]

logger = logging.getLogger("app.jobs.intraday_bars")

#: Politeness gap between symbols, matching `app/jobs/bars.py`'s own pacing. Six symbols
#: therefore take ~3 s of wall clock, comfortably inside a five-minute slot.
_SYMBOL_DELAY_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class IntradayBarsResult:
    """Outcome of one symbol's poll -- also the shape of its JSON log line."""

    symbol: str
    ok: bool
    bars: int = 0
    inserted: int = 0
    updated: int = 0
    quote: float | None = None
    error: str | None = None


def _log(result: IntradayBarsResult) -> None:
    level = logging.INFO if result.ok else logging.ERROR
    logger.log(level, json.dumps({"event": "intraday_bars", **asdict(result)}, default=str))


async def update_one_symbol_intraday(
    symbol: str,
    *,
    provider: YahooBarProvider,
    interval: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> IntradayBarsResult:
    """Poll one symbol's current session and upsert its aligned buckets. Never raises.

    The whole session is re-fetched every time rather than only the part since the last stored
    bucket. That is deliberate and it is what makes this self-healing: a poll missed while the
    laptop was asleep leaves no hole, because the next poll carries every bucket the missed one
    would have written. It also lets the newest bucket converge -- at 15:14 the 15:10 bucket is
    partial, and the 15:19 poll overwrites it with its settled values.

    The vendor's trailing live-quote row is returned by the provider separately and is **not**
    stored; it is logged and handed back for the caller to surface. Persisting it would put a
    zero-volume bar at a ragged timestamp in the middle of the series.
    """
    interval = interval or settings.INTRADAY_BARS_INTERVAL
    try:
        bars, quote = await provider.fetch_intraday_bars(symbol, interval=interval)
    except ProviderError as exc:
        result = IntradayBarsResult(symbol=symbol, ok=False, error=str(exc))
        _log(result)
        return result
    except Exception as exc:
        logger.exception("intraday_bars: %s poll raised a non-ProviderError", symbol)
        result = IntradayBarsResult(
            symbol=symbol, ok=False, error=f"{type(exc).__name__}: {exc}"
        )
        _log(result)
        return result

    try:
        upsert = upsert_intraday_bars(bars, session_factory=session_factory)
    except Exception as exc:  # noqa: BLE001 - a storage failure must not stop the other symbols
        result = IntradayBarsResult(
            symbol=symbol, ok=False, bars=len(bars), error=f"{type(exc).__name__}: {exc}"
        )
        _log(result)
        return result

    result = IntradayBarsResult(
        symbol=symbol,
        ok=True,
        bars=len(bars),
        inserted=upsert.inserted,
        updated=upsert.updated,
        quote=quote.price if quote is not None else None,
    )
    _log(result)
    return result


async def update_intraday_bars(
    symbols: list[str] | None = None,
    *,
    interval: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> list[IntradayBarsResult]:
    """Poll every configured symbol, sequentially, sharing one provider connection."""
    targets = symbols if symbols is not None else settings.intraday_bars_symbols
    provider = YahooBarProvider()
    results: list[IntradayBarsResult] = []
    try:
        for i, symbol in enumerate(targets):
            if i:
                await asyncio.sleep(_SYMBOL_DELAY_SECONDS)
            results.append(
                await update_one_symbol_intraday(
                    symbol,
                    provider=provider,
                    interval=interval,
                    session_factory=session_factory,
                )
            )
    finally:
        await provider.close()

    ok = sum(1 for r in results if r.ok)
    logger.info(
        json.dumps(
            {
                "event": "intraday_bars_run",
                "symbols": len(results),
                "ok": ok,
                "failed": len(results) - ok,
            }
        )
    )
    return results


def session_daily_bar(
    symbol: str,
    *,
    day: dt.date | None = None,
    interval: str | None = None,
    session_factory: sessionmaker[Session] | None = None,
    live_quote: LiveQuote | None = None,
) -> IntradayBar | None:
    """The in-progress daily bar for `symbol`, aggregated from today's stored buckets.

    This is the "both halves" half of T74 that never touches `daily_bars`. Open is the first
    bucket's open, high/low the extremes across buckets, close the last bucket's close (or
    `live_quote.price` when one is supplied, which is fresher by up to one interval), and volume
    the sum -- `None` only when no bucket published any, so a symbol like `^VIX` that never
    reports volume reads back as unknown rather than as a session with no trading.

    Returned as an `IntradayBar` with `interval="1d-live"` rather than a `DailyBar`, on purpose:
    a caller must not be able to hand this to anything expecting a settled daily bar. The type
    carries the warning instead of a comment doing it.

    Returns:
        `None` when no buckets are stored for the day -- before the open, on a holiday, or with
        polling switched off. A caller should fall back to the last settled `daily_bars` row and
        say so, rather than showing an empty today.
    """
    interval = interval or settings.INTRADAY_BARS_INTERVAL
    day = day or dt.datetime.now(dt.UTC).date()
    # A generous UTC window around the exchange-local day: ^VIX buckets start 02:15 CT and ETF
    # buckets 09:30 ET, so anchoring on a single exchange's session bounds would clip one of
    # them. Bucket instants are unambiguous, so a wide window plus a date filter is both
    # simpler and more correct than reconstructing two different session calendars here.
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
    end = dt.datetime.combine(day, dt.time.max, tzinfo=dt.UTC)
    bars = read_intraday_bars(
        symbol, interval=interval, start=start, end=end, session_factory=session_factory
    )
    if not bars:
        return None

    volumes = [b.volume for b in bars if b.volume is not None]
    return IntradayBar(
        symbol=symbol,
        interval="1d-live",
        ts=bars[0].ts,
        open=bars[0].open,
        high=max(b.high for b in bars),
        low=min(b.low for b in bars),
        close=live_quote.price if live_quote is not None else bars[-1].close,
        volume=sum(volumes) if volumes else None,
        source=bars[-1].source,
    )
