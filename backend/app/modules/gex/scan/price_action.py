"""Price action as code -- pure module (T146, plans/charter-mt5/README.md).

Swings, market structure, breaks of structure, support/resistance zones, single- and two-bar
patterns, and liquidity sweeps, computed from an OHLC frame of any timeframe.

**Pure**, on the same contract as every other `app.modules.gex.scan` module: no HTTP, no
database, no filesystem, no logging, no clock. Input is a `pd.DataFrame` with `open`, `high`,
`low`, `close` columns in ascending time order; nothing reads its index or a date column, so the
same functions serve the desk's daily bars, its 5-minute bars and the broker's M1 bars.

The one rule: ``occurred`` versus ``confirmed``
------------------------------------------------

Every :class:`Event` carries two bar positions. ``occurred`` is the bar the event is *about*
(the swing high's own bar); ``confirmed`` is the first bar at whose **close** the event is
knowable. A swing high at bar *i* with a right-hand window of *k* is ``occurred=i`` but
``confirmed=i+k``: until *k* lower bars have printed, it is not a swing high, it is just the
latest high. A consumer -- a backtest, a signal, a chart -- may act on an event only from
``confirmed`` on. Treating ``occurred`` as the actionable time is the classic way price-action
backtests lie (it is how zigzag indicators "repaint"), so the distinction is in the type
rather than left to discipline.

The guarantee each detector makes, and which ``tests/test_scan_price_action.py`` checks as a
property over random series: **running a detector on ``bars[: t + 1]`` returns exactly the
events the full-series run reports with ``confirmed <= t``.** Nothing a detector reports at bar
*t* depends on a bar after *t*.

Positions, not timestamps
-------------------------

Events hold integer bar positions (0-based, by row order). The caller maps them back to its own
timestamps (``bars.index[e.confirmed]`` or a ``ts`` column). Keeping this module timestamp-free
is what lets it be timeframe-agnostic, and it keeps the M1-vs-server-time question (the broker's
clock is not UTC) entirely outside the pure layer.

Ties
----

A swing high must be **strictly** higher than every bar in its left window and **at least as
high** as every bar in its right window (mirror for lows). The asymmetry makes a flat double top
produce one swing -- the first bar of the flat -- rather than none or two.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.modules.gex.scan.indicators import ATR_PERIOD, atr

__all__ = [
    "PIN_BODY_MAX",
    "PIN_WICK_MIN",
    "SWING_WINDOW",
    "ZONE_MIN_TOUCHES",
    "ZONE_TOLERANCE_ATR",
    "Event",
    "bar_patterns",
    "breaks",
    "structure",
    "sweeps",
    "swings",
    "to_frame",
    "zones",
]

#: Default left and right window for :func:`swings`. Three bars each side is the common
#: "fractal" reading on intraday bars; callers sweeping it in a study pass their own.
SWING_WINDOW = 3

#: A pin bar's rejecting wick is at least this share of its range...
PIN_WICK_MIN = 0.6
#: ...and its body at most this share.
PIN_BODY_MAX = 0.3

#: Swing prices within this many ATRs of a zone's mean join that zone.
ZONE_TOLERANCE_ATR = 0.25
#: A zone exists once this many swings have touched it.
ZONE_MIN_TOUCHES = 2

UP = "up"
DOWN = "down"

#: One shared NaN for the bar patterns' `price`, so two runs' events compare equal: a dataclass
#: compares field tuples, a tuple compares by identity before `==`, and `nan == nan` is False.
_NAN = float("nan")


@dataclass(frozen=True, slots=True)
class Event:
    """One price-action event.

    Attributes:
        kind: ``swing_high``, ``swing_low``, ``HH``, ``LH``, ``HL``, ``LL``, ``bos``, ``choch``,
            ``zone``, ``inside``, ``outside``, ``pin``, ``engulfing`` or ``sweep``.
        direction: ``up`` or ``down`` -- the side the event favours. A swing high is ``down``
            (it is resistance); a bullish engulfing bar is ``up``; a sweep of a high is ``down``.
            ``None`` for the two direction-less bar patterns, ``inside`` and ``outside``.
        occurred: Bar position the event is about.
        confirmed: First bar position at whose close the event is knowable. Never before
            ``occurred``.
        price: The level the event defines -- the swing's extreme, the broken swing's price,
            the zone's mean, the swept swing's price. ``NaN`` for bar patterns, which define no
            level of their own.
        ref: Position of the swing this event refers to (the swing a ``bos`` broke, the swing a
            ``sweep`` ran), or ``None``.
    """

    kind: str
    direction: str | None
    occurred: int
    confirmed: int
    price: float
    ref: int | None = None


def _ohlc(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        bars["open"].to_numpy(dtype=float),
        bars["high"].to_numpy(dtype=float),
        bars["low"].to_numpy(dtype=float),
        bars["close"].to_numpy(dtype=float),
    )


def swings(bars: pd.DataFrame, left: int = SWING_WINDOW, right: int = SWING_WINDOW) -> list[Event]:
    """Swing highs and lows, each confirmed ``right`` bars after it occurred.

    Returns:
        Events sorted by ``(confirmed, occurred, kind)``. A bar can be both a swing high and a
        swing low (a wide outside bar in a range); both are reported.

    Raises:
        ValueError: ``left < 1`` or ``right < 1``.
    """
    if left < 1 or right < 1:
        raise ValueError(f"left and right must be >= 1, got {left}, {right}")
    _, high, low, _ = _ohlc(bars)
    n = len(high)
    out: list[Event] = []
    for i in range(left, n - right):
        h, lo = high[i], low[i]
        if h > high[i - left : i].max() and h >= high[i + 1 : i + right + 1].max():
            out.append(Event("swing_high", DOWN, i, i + right, float(h)))
        if lo < low[i - left : i].min() and lo <= low[i + 1 : i + right + 1].min():
            out.append(Event("swing_low", UP, i, i + right, float(lo)))
    out.sort(key=lambda e: (e.confirmed, e.occurred, e.kind))
    return out


def structure(swing_events: list[Event]) -> list[Event]:
    """Label each swing against the previous swing of the same side: HH/LH for highs, HL/LL
    for lows. The first swing of each side has nothing to compare with and is not labelled.

    An equal high is ``LH`` and an equal low is ``HL``: a market that fails to exceed its last
    extreme has not made a higher high. Confirmed when the later swing is.
    """
    out: list[Event] = []
    last: dict[str, Event] = {}
    for e in swing_events:
        prev = last.get(e.kind)
        if prev is not None:
            if e.kind == "swing_high":
                kind, direction = ("HH", UP) if e.price > prev.price else ("LH", DOWN)
            else:
                kind, direction = ("LL", DOWN) if e.price < prev.price else ("HL", UP)
            out.append(Event(kind, direction, e.occurred, e.confirmed, e.price, prev.occurred))
        last[e.kind] = e
    return out


def breaks(bars: pd.DataFrame, swing_events: list[Event]) -> list[Event]:
    """Closes beyond the most recent confirmed swing: break of structure or change of character.

    At each bar *t*, the reference levels are the latest swing high and swing low whose
    ``confirmed <= t``. A close above the high is an upward break, below the low a downward
    one; each swing is broken at most once. The first break sets the trend and is a ``bos``.
    After that, a break in the trend's direction is a ``bos`` and one against it is a
    ``choch`` (change of character), which flips the trend.

    A close is used, not a wick: a wick through a swing that closes back inside is a
    :func:`sweeps` event, the opposite reading.
    """
    _, _, _, close = _ohlc(bars)
    out: list[Event] = []
    pending = sorted(swing_events, key=lambda e: e.confirmed)
    j = 0
    ref_high: Event | None = None
    ref_low: Event | None = None
    trend: str | None = None
    for t in range(len(close)):
        while j < len(pending) and pending[j].confirmed <= t:
            e = pending[j]
            if e.kind == "swing_high":
                ref_high = e
            elif e.kind == "swing_low":
                ref_low = e
            j += 1
        c = close[t]
        for ref, direction in ((ref_high, UP), (ref_low, DOWN)):
            if ref is None:
                continue
            crossed = c > ref.price if direction == UP else c < ref.price
            if not crossed:
                continue
            kind = "bos" if trend in (None, direction) else "choch"
            out.append(Event(kind, direction, t, t, ref.price, ref.occurred))
            trend = direction
            if direction == UP:
                ref_high = None
            else:
                ref_low = None
    return out


def zones(
    bars: pd.DataFrame,
    swing_events: list[Event],
    *,
    tolerance_atr: float = ZONE_TOLERANCE_ATR,
    min_touches: int = ZONE_MIN_TOUCHES,
    atr_period: int = ATR_PERIOD,
) -> list[Event]:
    """Support/resistance zones: clusters of swing prices within ``tolerance_atr`` ATRs.

    Swings are taken in confirmation order. Each joins the nearest existing cluster whose mean
    is within tolerance, measured with the ATR at the swing's ``confirmed`` bar, or starts a new
    one. A zone event is emitted when a cluster reaches ``min_touches``, at the confirmation of
    the swing that got it there, and again with each further touch, so a consumer can read a
    zone's strength at any bar. Highs and lows share clusters on purpose: old resistance
    becoming support is the same level.

    A swing whose ATR is still ``NaN`` (insufficient history) cannot be given a tolerance. It
    is skipped rather than clustered against an invented one.

    ``direction`` is that of the touching swing (``down`` for a high, ``up`` for a low), and
    ``ref`` is that swing's position.
    """
    if min_touches < 1:
        raise ValueError(f"min_touches must be >= 1, got {min_touches}")
    atr_values = atr(bars, atr_period).to_numpy(dtype=float)
    clusters: list[list[float]] = []
    out: list[Event] = []
    for e in sorted(swing_events, key=lambda e: (e.confirmed, e.occurred, e.kind)):
        if e.kind not in ("swing_high", "swing_low"):
            continue
        a = atr_values[e.confirmed]
        if math.isnan(a):
            continue
        tol = tolerance_atr * a
        best, best_dist = None, math.inf
        for k, members in enumerate(clusters):
            dist = abs(e.price - sum(members) / len(members))
            if dist <= tol and dist < best_dist:
                best, best_dist = k, dist
        if best is None:
            clusters.append([e.price])
            best = len(clusters) - 1
        else:
            clusters[best].append(e.price)
        members = clusters[best]
        if len(members) >= min_touches:
            mean = sum(members) / len(members)
            out.append(Event("zone", e.direction, e.occurred, e.confirmed, mean, e.occurred))
    return out


def bar_patterns(
    bars: pd.DataFrame,
    *,
    pin_wick_min: float = PIN_WICK_MIN,
    pin_body_max: float = PIN_BODY_MAX,
) -> list[Event]:
    """Inside, outside, pin and engulfing bars. Each is known at its own close.

    - **inside**: high below the previous high and low above the previous low.
    - **outside**: high above the previous high and low below the previous low.
    - **pin**: one wick is at least ``pin_wick_min`` of the range and the body at most
      ``pin_body_max``. A long lower wick is ``up`` (lows rejected); a long upper wick is
      ``down``. A zero-range bar is never a pin.
    - **engulfing**: the body is opposite in colour to the previous bar's and covers it
      (open-to-close range contains the previous one, and is strictly larger). A doji on
      either side is never engulfing.
    """
    open_, high, low, close = _ohlc(bars)
    out: list[Event] = []
    nan = _NAN
    for t in range(len(close)):
        rng = high[t] - low[t]
        if rng > 0:
            body = abs(close[t] - open_[t])
            upper = high[t] - max(open_[t], close[t])
            lower = min(open_[t], close[t]) - low[t]
            if body <= pin_body_max * rng:
                if lower >= pin_wick_min * rng:
                    out.append(Event("pin", UP, t, t, nan))
                elif upper >= pin_wick_min * rng:
                    out.append(Event("pin", DOWN, t, t, nan))
        if t == 0:
            continue
        if high[t] < high[t - 1] and low[t] > low[t - 1]:
            out.append(Event("inside", None, t, t, nan))
        elif high[t] > high[t - 1] and low[t] < low[t - 1]:
            out.append(Event("outside", None, t, t, nan))
        prev_body = close[t - 1] - open_[t - 1]
        body_signed = close[t] - open_[t]
        if prev_body != 0 and body_signed != 0 and (prev_body > 0) != (body_signed > 0):
            lo, hi = sorted((open_[t], close[t]))
            plo, phi = sorted((open_[t - 1], close[t - 1]))
            if lo <= plo and hi >= phi and (hi - lo) > (phi - plo):
                out.append(Event("engulfing", UP if body_signed > 0 else DOWN, t, t, nan))
    return out


def sweeps(bars: pd.DataFrame, swing_events: list[Event]) -> list[Event]:
    """Liquidity sweeps: a wick through the latest confirmed swing that closes back inside.

    At bar *t*, against the latest swing high with ``confirmed <= t``: ``high > swing`` and
    ``close < swing`` is a sweep, direction ``down``, because the stops above were run and
    rejected. The mirror for lows is ``up``. A swing is swept at most once. A *close* through
    it retires it unswept, because that is a :func:`breaks` event and the level is no longer
    resting liquidity.
    """
    _, high, low, close = _ohlc(bars)
    out: list[Event] = []
    pending = sorted(swing_events, key=lambda e: e.confirmed)
    j = 0
    ref_high: Event | None = None
    ref_low: Event | None = None
    for t in range(len(close)):
        while j < len(pending) and pending[j].confirmed <= t:
            e = pending[j]
            if e.kind == "swing_high":
                ref_high = e
            elif e.kind == "swing_low":
                ref_low = e
            j += 1
        if ref_high is not None:
            if close[t] > ref_high.price:
                ref_high = None
            elif high[t] > ref_high.price:
                out.append(Event("sweep", DOWN, t, t, ref_high.price, ref_high.occurred))
                ref_high = None
        if ref_low is not None:
            if close[t] < ref_low.price:
                ref_low = None
            elif low[t] < ref_low.price:
                out.append(Event("sweep", UP, t, t, ref_low.price, ref_low.occurred))
                ref_low = None
    return out


def to_frame(events: list[Event]) -> pd.DataFrame:
    """Events as a frame, one row each, columns in :class:`Event` field order."""
    cols = ["kind", "direction", "occurred", "confirmed", "price", "ref"]
    return pd.DataFrame([[getattr(e, c) for c in cols] for e in events], columns=cols)
