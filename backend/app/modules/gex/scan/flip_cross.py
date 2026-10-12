"""Intraday crosses of the gamma flip, and what price did afterwards (T142).

The frozen rule of `docs/edges/flip-cross.md`, in code, exactly as tested. **Do not change it.**
A variant is a new key with its own start date; editing this rule would quietly mix two rules'
evidence in one forward record.

* The flip at a bar is `gex_levels.flip_point` (filter `ALL`) from the **latest in-session
  capture with `captured_at <= the bar's close`**. Never the previous EOD flip, so bars before
  the session's first capture have no flip and cannot be part of a cross.
* A cross is **two consecutive 5-minute closes on the new side, with the close two bars
  earlier on the old side**. "Consecutive" means consecutive bars that *have* a flip, which is
  what the reference SQL's `lag()` over those rows did. A close exactly on the flip has no side
  and breaks a run.
* `FLIP_CROSS_DOWN` and `FLIP_CROSS_UP` are both recorded: the up-cross reversal is half the
  hypothesis.

Two kinds of follow-through are measured, both signed **in the direction of the cross** (a
positive number means price kept going the way it crossed):

* fixed-horizon returns from the desk symbol's own 5-minute closes -- 15, 30 and 60 minutes and
  the session close -- each `None` when its bar does not exist (null is not zero);
* the **excursion path** on the CFD's 1-minute bars, the sequence of swings after entry with no
  stop or target: "10 against, then 20 in favour, then 50 against". A swing counts once price
  turns back from its extreme by at least `unit`. Within a 1-minute bar the order of high and
  low is unknown; an up bar is taken as low-then-high and a down bar as high-then-low.

Pure: no I/O, no clock, no logging -- the same contract as the other `scan/` modules. The job
in `app.modules.gex.jobs.flip_crosses` reads the rows and writes the results.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "BAR",
    "HORIZONS_MIN",
    "KEY_DOWN",
    "KEY_UP",
    "CaptureLike",
    "CloseBar",
    "Excursions",
    "FlipCross",
    "MinuteBar",
    "detect_crosses",
    "direction_of",
    "excursion_path",
    "horizon_returns",
    "minute_points",
    "path_unit",
    "side_at",
]

KEY_DOWN = "FLIP_CROSS_DOWN"
KEY_UP = "FLIP_CROSS_UP"

#: The desk's intraday bar length. A bar's `ts` is its opening instant; it closes at `ts + BAR`.
BAR = dt.timedelta(minutes=5)

#: The fixed horizons, in minutes after entry (3, 6 and 12 bars).
HORIZONS_MIN: tuple[int, ...] = (15, 30, 60)


class CloseBar(Protocol):
    """A 5-minute bar as the detector needs it: its opening instant and its close."""

    @property
    def ts(self) -> dt.datetime: ...

    @property
    def close(self) -> float: ...


class CaptureLike(Protocol):
    """An in-session capture's flip, as the detector needs it."""

    @property
    def captured_at(self) -> dt.datetime: ...

    @property
    def flip(self) -> float: ...

    @property
    def net_gex(self) -> float | None: ...


class MinuteBar(Protocol):
    @property
    def ts(self) -> dt.datetime: ...

    @property
    def open(self) -> float: ...

    @property
    def high(self) -> float: ...

    @property
    def low(self) -> float: ...

    @property
    def close(self) -> float: ...


@dataclass(frozen=True, slots=True)
class FlipCross:
    """One cross, as it was knowable at the confirming bar's close."""

    symbol: str
    #: The confirming (second) bar's opening instant. With `symbol`, the event's identity.
    bar_ts: dt.datetime
    key: str
    #: The confirming bar's close: the entry reference.
    spot: float
    flip: float
    net_gex: float | None
    #: When the capture that supplied `flip` was taken.
    capture_at: dt.datetime
    #: Index of the confirming bar in the bar list the detector was given.
    index: int

    @property
    def entry_at(self) -> dt.datetime:
        return self.bar_ts + BAR


def direction_of(key: str) -> int:
    """+1 for an up-cross, -1 for a down-cross: the sign that makes a continuation positive."""
    return 1 if key == KEY_UP else -1


def _flip_at(close_at: dt.datetime, captures: Sequence[CaptureLike]) -> CaptureLike | None:
    known = None
    for c in captures:  # sorted ascending; the lists are a few dozen rows
        if c.captured_at <= close_at:
            known = c
        else:
            break
    return known


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def side_at(bar: CloseBar, captures: Sequence[CaptureLike]) -> int | None:
    """+1 above the flip known at `bar`'s close, -1 below, 0 on it, `None` with no capture yet."""
    cap = _flip_at(bar.ts + BAR, sorted(captures, key=lambda c: c.captured_at))
    return None if cap is None else _sign(bar.close - cap.flip)


def detect_crosses(
    symbol: str, bars: Sequence[CloseBar], captures: Sequence[CaptureLike]
) -> list[FlipCross]:
    """Every cross in one symbol's session, oldest first.

    `bars` are one session's 5-minute bars and `captures` that session's in-session captures.
    Pass only bars that have **closed**: the caller owns the clock.
    """
    bars = sorted(bars, key=lambda b: b.ts)
    caps = sorted(captures, key=lambda c: c.captured_at)
    rows: list[tuple[int, int, CaptureLike]] = []  # (bar index, side, capture) for bars with a flip
    for i, b in enumerate(bars):
        cap = _flip_at(b.ts + BAR, caps)
        if cap is not None:
            rows.append((i, _sign(b.close - cap.flip), cap))

    out: list[FlipCross] = []
    for j in range(2, len(rows)):
        i, side, cap = rows[j]
        if side != 0 and rows[j - 1][1] == side and rows[j - 2][1] == -side:
            b = bars[i]
            out.append(FlipCross(
                symbol=symbol, bar_ts=b.ts, key=KEY_UP if side > 0 else KEY_DOWN,
                spot=b.close, flip=cap.flip, net_gex=cap.net_gex, capture_at=cap.captured_at,
                index=i,
            ))
    return out


def horizon_returns(
    cross: FlipCross, bars: Sequence[CloseBar]
) -> tuple[dict[int, float | None], float | None]:
    """Returns in bps after `cross`, signed in its direction: `({15: r, 30: r, 60: r}, r_close)`.

    `bars` must be the same session's bars, in the order the detector saw them. A horizon whose
    bar does not exist is `None`. The close return is from the session's last bar, so it is only
    final once the session is.
    """
    bars = sorted(bars, key=lambda b: b.ts)
    d = direction_of(cross.key)
    by_ts = {b.ts: b.close for b in bars}

    def ret(close: float) -> float:
        return d * (close / cross.spot - 1.0) * 1e4

    out: dict[int, float | None] = {}
    for minutes in HORIZONS_MIN:
        c = by_ts.get(cross.bar_ts + dt.timedelta(minutes=minutes))
        out[minutes] = None if c is None else ret(c)
    last = bars[-1] if bars else None
    r_close = ret(last.close) if last is not None and last.ts >= cross.bar_ts else None
    return out, r_close


def minute_points(
    bars: Sequence[MinuteBar], entry: float, direction: int, entry_at: dt.datetime
) -> list[tuple[float, float]]:
    """1-minute bars as an ordered excursion series: `(minutes after entry, excursion)`.

    Each bar contributes two points, its two extremes, ordered by the bar's colour (up bar: low
    then high). Excursion is `direction * (price - entry)`, so with `direction` taken from the
    cross, positive means the cross followed through.
    """
    pts: list[tuple[float, float]] = []
    for b in sorted(bars, key=lambda b: b.ts):
        first, second = (b.low, b.high) if b.close >= b.open else (b.high, b.low)
        t = (b.ts - entry_at).total_seconds() / 60.0
        pts.append((t, direction * (first - entry)))
        pts.append((t + 0.5, direction * (second - entry)))
    return pts


@dataclass(frozen=True, slots=True)
class Excursions:
    """The swing path after one entry, in price points, signed in the cross's direction.

    `swings` alternate between troughs and peaks: each is `(minutes after entry, excursion)` at
    a turn confirmed by a reversal of at least `unit`, and the last element is the extreme of
    the leg still open at the end of the path. `mae` and `mfe` are the worst and best excursions
    anywhere on the path; `close` is the excursion at its end.
    """

    swings: tuple[tuple[float, float], ...]
    mae: float
    mfe: float
    close: float


def excursion_path(points: Sequence[tuple[float, float]], unit: float) -> Excursions | None:
    """Zig-zag `points` (from :func:`minute_points`) into swings of at least `unit`.

    The entry is the first pivot. Until the first swing is confirmed the path tracks both its
    worst and best excursion; whichever one price then turns back from by `unit` is the first
    swing -- reported only if it is itself at least `unit` from the entry, so a one-tick dip
    before a run is not a swing. Returns `None` for an empty path.
    """
    if not points or unit <= 0:
        return None
    swings: list[tuple[float, float]] = []
    lo = hi = 0.0
    lo_t = hi_t = 0.0
    direction = 0  # 0 unset, +1 rising leg, -1 falling leg
    ext, ext_t = 0.0, 0.0
    for t, x in points:
        if direction == 0:
            if x < lo:
                lo, lo_t = x, t
            if x > hi:
                hi, hi_t = x, t
            if x - lo >= unit:
                if lo <= -unit:
                    swings.append((lo_t, lo))
                direction, ext, ext_t = 1, x, t
            elif hi - x >= unit:
                if hi >= unit:
                    swings.append((hi_t, hi))
                direction, ext, ext_t = -1, x, t
            continue
        if direction == 1:
            if x > ext:
                ext, ext_t = x, t
            elif ext - x >= unit:
                swings.append((ext_t, ext))
                direction, ext, ext_t = -1, x, t
        else:
            if x < ext:
                ext, ext_t = x, t
            elif x - ext >= unit:
                swings.append((ext_t, ext))
                direction, ext, ext_t = 1, x, t
        lo, hi = min(lo, x), max(hi, x)
    if direction != 0:
        swings.append((ext_t, ext))
    values = [x for _, x in points]
    return Excursions(
        swings=tuple(swings), mae=min(0.0, *values), mfe=max(0.0, *values), close=values[-1]
    )


def path_unit(bars: Sequence[MinuteBar], *, share: float = 0.5) -> float | None:
    """`share` of the median 5-minute high-low range of the given 1-minute bars.

    The caller chooses the bars (the CFD's regular-session minutes over a trailing window). Bars
    are bucketed on the 5-minute grid of their own timestamps. `None` with no bars.
    """
    buckets: dict[dt.datetime, tuple[float, float]] = {}
    for b in bars:
        key = b.ts.replace(minute=b.ts.minute - b.ts.minute % 5, second=0, microsecond=0)
        lo, hi = buckets.get(key, (b.low, b.high))
        buckets[key] = (min(lo, b.low), max(hi, b.high))
    if not buckets:
        return None
    return share * statistics.median(hi - lo for lo, hi in buckets.values())
