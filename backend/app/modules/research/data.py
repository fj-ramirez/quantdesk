"""OHLCV data across markets: download, cache as parquet, serve to the search.

T77 changed exactly one thing here: the cache root is `config.OHLCV_DIR`
(`DATA_DIR/research/ohlcv/`) instead of the standalone repo's `data/ohlcv/`. The parquet
layout under it -- `{market}/{symbol}_{timeframe}.parquet` -- is byte-identical, so the
existing 33 MB tree moves by copying the directory and nothing reindexes.

Two sources:
- ccxt (crypto): 1h base data fetched incrementally, resampled to 4h/1d.
- yahoo (stocks/forex/futures): each timeframe fetched natively. Daily history
  is decades; Yahoo caps 1h at 730 days. Refetched whole (it's tiny) with a
  staleness check so continuous mode doesn't hammer the API every cycle.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from .config import OHLCV_DIR

log = logging.getLogger(__name__)

CCXT_BASE_TF = "1h"
CCXT_BASE_MS = 3_600_000
FETCH_LIMIT = 1000

_RESAMPLE_RULE = {"1h": "1h", "4h": "4h", "1d": "1D"}
_BAR_DURATION = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
# Skip a Yahoo refetch if the cache is younger than this.
_YAHOO_STALENESS = {"1h": pd.Timedelta(hours=1), "1d": pd.Timedelta(hours=6)}


def cache_path(market: str, symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "-").replace("=", "_")
    return OHLCV_DIR / market / f"{safe}_{timeframe}.parquet"


def _drop_forming_bar(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Keep only bars whose period has fully elapsed."""
    if len(df) == 0:
        return df
    now = pd.Timestamp.now(tz="UTC")
    if df.index[-1] + _BAR_DURATION[timeframe] > now:
        df = df.iloc[:-1]
    return df


# ---------------------------------------------------------------- ccxt source

def _to_frame(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts").astype(float)


def _update_ccxt_symbol(client, market: str, symbol: str, start: str) -> None:
    path = cache_path(market, symbol, CCXT_BASE_TF)
    cached = pd.read_parquet(path) if path.exists() else None

    if cached is not None and len(cached) > 0:
        since = int(cached.index[-1].timestamp() * 1000) + CCXT_BASE_MS
    else:
        since = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)

    chunks: list[pd.DataFrame] = []
    while True:
        rows = client.fetch_ohlcv(symbol, CCXT_BASE_TF, since=since, limit=FETCH_LIMIT)
        if not rows:
            break
        chunks.append(_to_frame(rows))
        since = rows[-1][0] + CCXT_BASE_MS
        if len(rows) < FETCH_LIMIT:
            break
        time.sleep(client.rateLimit / 1000)

    if not chunks:
        log.info("%s/%s: cache up to date (%d bars)", market, symbol,
                 0 if cached is None else len(cached))
        return
    df = pd.concat([cached, *chunks]) if cached is not None else pd.concat(chunks)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = _drop_forming_bar(df, CCXT_BASE_TF)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    log.info("%s/%s: total %d bars", market, symbol, len(df))


def _update_ccxt(market: str, mcfg: dict) -> None:
    import ccxt

    client = getattr(ccxt, mcfg["exchange"])({"enableRateLimit": True})
    for symbol in mcfg["symbols"]:
        try:
            _update_ccxt_symbol(client, market, symbol, mcfg["history_start"])
        except Exception:
            log.exception("failed to update %s/%s; using cached data", market, symbol)


# --------------------------------------------------------------- yahoo source

def _update_yahoo(market: str, mcfg: dict) -> None:
    import yfinance as yf

    now = pd.Timestamp.now(tz="UTC")
    for symbol in mcfg["symbols"]:
        ticker = yf.Ticker(symbol)
        for tf in mcfg["timeframes"]:
            path = cache_path(market, symbol, tf)
            if path.exists():
                age = now - pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC")
                if age < _YAHOO_STALENESS[tf]:
                    continue
            try:
                if tf == "1h":
                    df = ticker.history(period="730d", interval="1h", auto_adjust=True)
                else:
                    df = ticker.history(start=mcfg["history_start"], interval="1d",
                                        auto_adjust=True)
                df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
                df = df[df["close"].notna() & (df["close"] > 0)]
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                else:
                    df.index = df.index.tz_convert("UTC")
                df = _drop_forming_bar(df.sort_index(), tf)
                if len(df) == 0:
                    log.warning("%s/%s %s: Yahoo returned no data", market, symbol, tf)
                    continue
                # Yahoo caps 1h history at 730 days, so each fetch is a sliding
                # window. Merge into the cache instead of overwriting so history
                # accumulates beyond the cap and backtests stay reproducible.
                # (Bars older than the window keep their adjustment as of when
                # they were fetched — a small, accepted inconsistency. Daily
                # data is always a full refetch, so it stays fully adjusted.)
                if tf == "1h" and path.exists():
                    cached = pd.read_parquet(path)
                    df = pd.concat([cached, df])
                    df = df[~df.index.duplicated(keep="last")].sort_index()
                path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(path)
                log.info("%s/%s %s: total %d bars", market, symbol, tf, len(df))
            except Exception:
                log.exception("failed to update %s/%s %s; using cached data",
                              market, symbol, tf)


# ------------------------------------------------------------------ interface

def update_all(cfg: dict) -> None:
    for market, mcfg in cfg["markets"].items():
        if mcfg["source"] == "ccxt":
            _update_ccxt(market, mcfg)
        elif mcfg["source"] == "yahoo":
            _update_yahoo(market, mcfg)
        else:
            raise ValueError(f"unknown data source: {mcfg['source']}")


def cache_ages(cfg: dict) -> dict[str, tuple[int, float | None]]:
    """Per market: (number of cached datasets, hours since the stalest write)."""
    now = time.time()
    out: dict[str, tuple[int, float | None]] = {}
    for market in cfg["markets"]:
        d = OHLCV_DIR / market
        files = list(d.glob("*.parquet")) if d.exists() else []
        oldest = max(((now - f.stat().st_mtime) / 3600 for f in files), default=None)
        out[market] = (len(files), oldest)
    return out


_OHLCV_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def load_ohlcv(market: str, mcfg: dict, symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Load cached data for one (market, symbol, timeframe). None if not cached."""
    if mcfg["source"] == "ccxt":
        path = cache_path(market, symbol, CCXT_BASE_TF)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        if timeframe == CCXT_BASE_TF:
            return df
        out = df.resample(_RESAMPLE_RULE[timeframe]).agg(_OHLCV_AGG).dropna()
        return out.iloc[:-1] if len(out) else out  # trailing bucket may be partial

    path = cache_path(market, symbol, timeframe)
    return pd.read_parquet(path) if path.exists() else None
