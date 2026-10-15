"""The broker chart's arithmetic (T159). Pure: no I/O, no clock, no logging.

The chart endpoint (`api/chart.py`) reads M1 bars and GEX levels, and hands them here. Three
things are decided in this file, and each one is a way a chart could lie:

**Buckets are server wall-clock.** An H4 or D1 bar is cut where MT5 cuts it: on the broker's
server clock, which runs New York + 7 hours, so a D1 bar is one New York session (17:00 to
17:00) rather than a UTC day. Each M1 row carries the `offset_s` it was converted with, so a
row's server time is `ts + offset_s`. The offset only changes on the US DST Sundays, when
nothing trades, so no bucket straddles two offsets. A bucket's `time` is its open instant in
UTC.

**The forming bar is flagged, and no detector sees it.** A bucket is *forming* when its end is
after the horizon (`end`, or now). It is also forming when the data stopped short of its end
less than :data:`LIVE_LAG` before the horizon: that is ingest lag, and the bar's last minutes are
still on their way. When the data stopped longer ago than that, the market closed (a Friday
evening, a holiday), and the bucket is as complete as it will get. Only complete bars go to
`price_action`, so every event on screen was knowable at the chart's right edge, which is the
detectors' own `confirmed` guarantee carried through to the screen.

**Levels are as of each bar's close.** Each bar is paired with the newest GEX snapshot captured
at or before that bar's close (or the horizon, for the forming bar), never a later one. A level
older than :data:`LEVEL_MAX_AGE` is dropped rather than carried across a gap in capture.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.modules.gex.scan import price_action as pa
from app.modules.gex.scan.indicators import atr

__all__ = [
    "LEVEL_MAX_AGE",
    "LIVE_LAG",
    "MAX_M1_ROWS",
    "TIMEFRAMES",
    "ChartEvent",
    "Zone",
    "bars_cap",
    "detect",
    "levels_per_bar",
    "recent_zones",
    "resample",
]

#: Timeframe name -> minutes. The names are MT5's.
TIMEFRAMES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}

#: The most M1 rows one request reads. A bucket holds at most `minutes` M1 rows, so reading
#: `bars * minutes` rows always yields at least `bars` buckets; this caps that product.
MAX_M1_ROWS = 400_000

#: Data that stopped less than this before the horizon is ingest lag, not a closed market.
LIVE_LAG = dt.timedelta(minutes=15)

#: A GEX level older than this at a bar's close is not drawn on that bar.
LEVEL_MAX_AGE = dt.timedelta(days=4)

#: How many zones the chart draws, newest touch first.
ZONE_LIMIT = 6


def bars_cap(minutes: int) -> int:
    """The most buckets one request may ask for at this timeframe."""
    return max(1, MAX_M1_ROWS // minutes)


def resample(m1: pd.DataFrame, minutes: int, horizon: dt.datetime) -> pd.DataFrame:
    """Cut M1 rows into `minutes` buckets on the server clock.

    Args:
        m1: Ascending M1 rows with `ts` (tz-aware UTC, the bar's open), `open`, `high`, `low`,
            `close`, `tick_volume`, `spread` and `offset_s`.
        minutes: Bucket length.
        horizon: The instant the chart is drawn as of (`end`, or now), tz-aware.

    Returns:
        One row per bucket, ascending: `time` (UTC open), `open`, `high`, `low`, `close`,
        `tick_volume`, `spread` (the lowest in the bucket, in points), `end` (UTC) and
        `forming`. Empty in, empty out.
    """
    cols = ["time", "open", "high", "low", "close", "tick_volume", "spread", "end", "forming"]
    if m1.empty:
        return pd.DataFrame(columns=cols)

    ts = pd.to_datetime(m1["ts"], utc=True)
    offset = pd.to_timedelta(m1["offset_s"].to_numpy(), unit="s")
    server = ts + offset
    bucket = server.dt.floor(f"{minutes}min")
    frame = m1.assign(_bucket=bucket.to_numpy(), _offset=offset)

    grouped = frame.groupby("_bucket", sort=True)
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
        spread=("spread", "min"),
        _offset=("_offset", "first"),
    ).reset_index()

    width = pd.Timedelta(minutes=minutes)
    out["time"] = (out["_bucket"] - out["_offset"]).dt.tz_convert("UTC")
    out["end"] = out["time"] + width

    horizon_ts = pd.Timestamp(horizon).tz_convert("UTC")
    data_end = ts.iloc[-1] + pd.Timedelta(minutes=1)
    forming = out["end"] > horizon_ts
    if horizon_ts - data_end < pd.Timedelta(LIVE_LAG):
        forming = forming | (out["end"] > data_end)
    out["forming"] = forming.to_numpy()
    return out[cols]


@dataclass(frozen=True, slots=True)
class ChartEvent:
    """A price-action event placed on the chart. `time` is the bar the event is about;
    `confirmed_time` the bar at whose close it became knowable."""

    kind: str
    direction: str | None
    time: dt.datetime
    confirmed_time: dt.datetime
    price: float | None
    ref_time: dt.datetime | None


@dataclass(frozen=True, slots=True)
class Zone:
    price: float
    direction: str | None
    touches: int
    last_time: dt.datetime


def _complete(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.loc[~bars["forming"].astype(bool)].reset_index(drop=True)


def detect(bars: pd.DataFrame) -> tuple[list[ChartEvent], list[pa.Event]]:
    """Every `price_action` detector over the **complete** bars.

    Returns the events placed on bar times, and the raw zone events for :func:`recent_zones`.
    """
    done = _complete(bars)
    if done.empty:
        return [], []
    sw = pa.swings(done)
    zone_events = pa.zones(done, sw)
    raw = (
        sw
        + pa.structure(sw)
        + pa.breaks(done, sw)
        + pa.sweeps(done, sw)
        + pa.bar_patterns(done)
    )
    times = list(done["time"])

    def at(pos: int | None) -> dt.datetime | None:
        return None if pos is None else times[pos].to_pydatetime()

    out = [
        ChartEvent(
            kind=e.kind,
            direction=e.direction,
            time=times[e.occurred].to_pydatetime(),
            confirmed_time=times[e.confirmed].to_pydatetime(),
            price=None if np.isnan(e.price) else float(e.price),
            ref_time=at(e.ref),
        )
        for e in raw
    ]
    out.sort(key=lambda e: (e.confirmed_time, e.time, e.kind))
    return out, zone_events


def recent_zones(bars: pd.DataFrame, zone_events: list[pa.Event]) -> list[Zone]:
    """The zones worth drawing at the right edge, newest touch first.

    `price_action.zones` emits a cluster again at every touch, with its mean updated, so one
    level shows up as several events a little apart. Walking them newest first, an event within
    the detector's own tolerance (at the last complete bar's ATR) of a zone already kept is that
    zone's earlier state and only adds a touch.
    """
    done = _complete(bars)
    if done.empty or not zone_events:
        return []
    last_atr = atr(done).iloc[-1]
    tolerance = pa.ZONE_TOLERANCE_ATR * float(last_atr) if pd.notna(last_atr) else 0.0
    times = list(done["time"])

    kept: list[dict] = []
    for e in sorted(zone_events, key=lambda e: e.confirmed, reverse=True):
        match = next((z for z in kept if abs(z["price"] - e.price) <= tolerance), None)
        if match is not None:
            match["touches"] += 1
            continue
        kept.append({
            "price": float(e.price),
            "direction": e.direction,
            "touches": pa.ZONE_MIN_TOUCHES,
            "last_time": times[e.confirmed].to_pydatetime(),
        })
    return [Zone(**z) for z in kept[:ZONE_LIMIT]]


def levels_per_bar(bars: pd.DataFrame, levels: pd.DataFrame, horizon: dt.datetime) -> pd.DataFrame:
    """Pair each bar with the newest GEX snapshot captured at or before its close.

    Args:
        bars: Output of :func:`resample`.
        levels: Rows with `captured_at` (tz-aware UTC), `call_wall`, `put_wall`, `flip_point`
            and `spot`, already in CFD price, any order.
        horizon: Caps the forming bar's close, so it only sees what exists at the horizon.

    Returns:
        One row per bar that has a level: `time`, `captured_at`, `call_wall`, `put_wall`,
        `flip_point`, `spot`. Columns that are null in the snapshot stay null.
    """
    cols = ["time", "captured_at", "call_wall", "put_wall", "flip_point", "spot"]
    if bars.empty or levels.empty:
        return pd.DataFrame(columns=cols)
    horizon_ts = pd.Timestamp(horizon).tz_convert("UTC")
    left = pd.DataFrame({
        "time": pd.to_datetime(bars["time"], utc=True),
        "_close": pd.to_datetime(bars["end"], utc=True).clip(upper=horizon_ts),
    })
    right = levels.assign(captured_at=pd.to_datetime(levels["captured_at"], utc=True))
    right = right.sort_values("captured_at")[["captured_at", "call_wall", "put_wall",
                                              "flip_point", "spot"]]
    merged = pd.merge_asof(
        left.sort_values("_close"),
        right,
        left_on="_close",
        right_on="captured_at",
        direction="backward",
        allow_exact_matches=True,
    )
    fresh = merged["captured_at"].notna() & (
        merged["_close"] - merged["captured_at"] <= pd.Timedelta(LEVEL_MAX_AGE)
    )
    return merged.loc[fresh, cols].sort_values("time").reset_index(drop=True)
