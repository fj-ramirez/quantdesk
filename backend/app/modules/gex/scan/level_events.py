"""Intraday events at the desk's gamma levels, and what price did afterwards (T142, T153).

Three frozen rules, in code exactly as specified. **Do not change either.** A variant is a new
key with its own start date; editing a rule would quietly mix two rules' evidence in one
forward record.

**Flip cross** (`docs/edges/flip-cross.md`, frozen 2026-10-08):

* The flip at a bar is `gex_levels.flip_point` (filter `ALL`) from the **latest in-session
  capture with a flip and `captured_at <= the bar's close`**. Never the previous EOD flip, so
  bars before the session's first capture have no flip and cannot be part of a cross.
* A cross is **two consecutive 5-minute closes on the new side, with the close two bars
  earlier on the old side**. "Consecutive" means consecutive bars that *have* a flip, which is
  what the reference SQL's `lag()` over those rows did. A close exactly on the flip has no side
  and breaks a run.
* `FLIP_CROSS_DOWN` and `FLIP_CROSS_UP` are both recorded: the up-cross reversal is half the
  hypothesis.

**Wall bounce** (`docs/edges/wall-bounce.md`, frozen 2026-10-12):

* The wall at a bar is `put_wall` / `call_wall` (filter `ALL`) from the latest in-session
  capture with that wall and `captured_at <= the bar's close`.
* A **touch** of the put wall W is a bar whose previous close was above W, whose low comes
  within `TOUCH_TOL` x W of it (or through it), and which closes back above W. Then **two
  consecutive closes each higher than the one before** confirm it, and the event is the second
  of those closes: `PUT_WALL_BOUNCE`, long. The call wall is the mirror image:
  `CALL_WALL_BOUNCE`, short.
* W is the wall known at the touch bar's close. After an event, the next touch of the same key
  must come after the confirming bar, so one touch sequence is one event.

**Wall break** (`docs/edges/wall-break.md`, frozen 2026-10-14, T164):

* The flip cross's structure on a wall: **two consecutive 5-minute closes beyond the wall, with
  the close two bars earlier on the near side**. Through the put wall (downward) is
  `PUT_WALL_BREAK`, pointing down; through the call wall (upward) is `CALL_WALL_BREAK`, pointing
  up. "Consecutive" means consecutive bars that have that wall, as for the flip.
* The wall known at **all three** closes must be the same strike. A wall that moves to another
  strike between captures is not price breaking through it, so that run is no event.
* A close exactly on the wall has no side and breaks a run.
* Logged, scored and alerted like the others, but not traded: it is a forward record of the
  bounce's failure mode, with no executor class.

**0DTE variants** (T154, frozen 2026-10-12): both rules run unchanged on the `ZERO_DTE` levels
too, as separate keys suffixed `_0DTE` (:func:`variant_key`). 0DTE walls sit close to price and
move during the session, so these fire far more often; their records never mix with the
all-expiries keys.

Two kinds of follow-through are measured, both signed **in the event's direction** (`+1` for
`FLIP_CROSS_UP`, `PUT_WALL_BOUNCE` and `CALL_WALL_BREAK`, `-1` for the other three), so a positive number means price
kept going the way the event pointed:

* fixed-horizon returns from the desk symbol's own 5-minute closes -- 15, 30 and 60 minutes and
  the session close -- each `None` when its bar does not exist (null is not zero);
* the **excursion path** on the CFD's 1-minute bars, the sequence of swings after entry with no
  stop or target: "10 against, then 20 in favour, then 50 against". A swing counts once price
  turns back from its extreme by at least `unit`. Within a 1-minute bar the order of high and
  low is unknown; an up bar is taken as low-then-high and a down bar as high-then-low.

Pure: no I/O, no clock, no logging -- the same contract as the other `scan/` modules. The job
in `app.modules.gex.jobs.level_events` reads the rows and writes the results.
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
    "KEYS",
    "KEY_CALL_BOUNCE",
    "KEY_CALL_BREAK",
    "KEY_DOWN",
    "KEY_PUT_BOUNCE",
    "KEY_PUT_BREAK",
    "KEY_UP",
    "TOUCH_TOL",
    "ZERO_DTE_SUFFIX",
    "Capture",
    "Excursions",
    "LevelEvent",
    "OhlcBar",
    "base_key",
    "detect_crosses",
    "detect_wall_bounces",
    "detect_wall_breaks",
    "direction_of",
    "excursion_path",
    "horizon_returns",
    "minute_points",
    "path_unit",
    "side_at",
    "variant_key",
]

KEY_DOWN = "FLIP_CROSS_DOWN"
KEY_UP = "FLIP_CROSS_UP"
KEY_PUT_BOUNCE = "PUT_WALL_BOUNCE"
KEY_CALL_BOUNCE = "CALL_WALL_BOUNCE"
#: T164, frozen 2026-10-14: two closes through a wall that stood still. Alerted, not traded.
KEY_PUT_BREAK = "PUT_WALL_BREAK"
KEY_CALL_BREAK = "CALL_WALL_BREAK"
#: T154: the same two rules on the **0DTE** levels (`gex_levels` filter `ZERO_DTE`) are separate
#: keys with this suffix, frozen 2026-10-12. Same detectors, different inputs, separate records:
#: mixing them into the all-expiries keys would change those keys' evidence.
ZERO_DTE_SUFFIX = "_0DTE"

KEYS: tuple[str, ...] = tuple(
    k + s
    for s in ("", ZERO_DTE_SUFFIX)
    for k in (KEY_DOWN, KEY_UP, KEY_PUT_BOUNCE, KEY_CALL_BOUNCE, KEY_PUT_BREAK, KEY_CALL_BREAK)
)


def variant_key(key: str, level_filter: str) -> str:
    """The key an event carries for a `gex_levels` filter: unchanged for `ALL`, suffixed for 0DTE."""
    if level_filter == "ALL":
        return key
    if level_filter == "ZERO_DTE":
        return key + ZERO_DTE_SUFFIX
    raise ValueError(f"no level-event keys for filter {level_filter!r}")


def base_key(key: str) -> str:
    """`key` without its filter suffix."""
    return key.removesuffix(ZERO_DTE_SUFFIX)

#: The desk's intraday bar length. A bar's `ts` is its opening instant; it closes at `ts + BAR`.
BAR = dt.timedelta(minutes=5)

#: The fixed horizons, in minutes after entry (3, 6 and 12 bars).
HORIZONS_MIN: tuple[int, ...] = (15, 30, 60)

#: How close to a wall a bar's extreme must come to count as a touch, as a fraction of the wall.
TOUCH_TOL = 0.0005


class OhlcBar(Protocol):
    """A bar as the detectors and the path need it. `ts` is its opening instant."""

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
class Capture:
    """An in-session capture's levels. Any level may be unknown."""

    captured_at: dt.datetime
    flip: float | None
    call_wall: float | None = None
    put_wall: float | None = None
    net_gex: float | None = None


@dataclass(frozen=True, slots=True)
class LevelEvent:
    """One event, as it was knowable at the bar that confirmed it."""

    symbol: str
    #: The confirming bar's opening instant. With `symbol`, the event's identity.
    bar_ts: dt.datetime
    key: str
    #: The confirming bar's close: the entry reference.
    spot: float
    #: The flip or wall the event is about.
    level: float
    #: `flip`, `put_wall` or `call_wall`.
    level_kind: str
    net_gex: float | None
    #: When the capture that supplied `level` was taken.
    capture_at: dt.datetime
    #: For a bounce, the touching bar's opening instant; `None` for a cross.
    touch_ts: dt.datetime | None = None

    @property
    def entry_at(self) -> dt.datetime:
        return self.bar_ts + BAR


def direction_of(key: str) -> int:
    """+1 for an event that points up, -1 for one that points down."""
    return 1 if base_key(key) in (KEY_UP, KEY_PUT_BOUNCE, KEY_CALL_BREAK) else -1


def _latest(close_at: dt.datetime, captures: Sequence[Capture]) -> Capture | None:
    known = None
    for c in captures:  # sorted ascending; the lists are a few dozen rows
        if c.captured_at <= close_at:
            known = c
        else:
            break
    return known


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def side_at(bar: OhlcBar, captures: Sequence[Capture]) -> int | None:
    """+1 above the flip known at `bar`'s close, -1 below, 0 on it, `None` with no flip yet."""
    caps = sorted((c for c in captures if c.flip is not None), key=lambda c: c.captured_at)
    cap = _latest(bar.ts + BAR, caps)
    return None if cap is None else _sign(bar.close - cap.flip)


def detect_crosses(
    symbol: str, bars: Sequence[OhlcBar], captures: Sequence[Capture]
) -> list[LevelEvent]:
    """Every flip cross in one symbol's session, oldest first.

    `bars` are one session's 5-minute bars and `captures` that session's in-session captures.
    Pass only bars that have **closed**: the caller owns the clock.
    """
    bars = sorted(bars, key=lambda b: b.ts)
    caps = sorted((c for c in captures if c.flip is not None), key=lambda c: c.captured_at)
    rows: list[tuple[int, int, Capture]] = []  # (bar index, side, capture) for bars with a flip
    for i, b in enumerate(bars):
        cap = _latest(b.ts + BAR, caps)
        if cap is not None:
            rows.append((i, _sign(b.close - cap.flip), cap))

    out: list[LevelEvent] = []
    for j in range(2, len(rows)):
        i, side, cap = rows[j]
        if side != 0 and rows[j - 1][1] == side and rows[j - 2][1] == -side:
            b = bars[i]
            out.append(LevelEvent(
                symbol=symbol, bar_ts=b.ts, key=KEY_UP if side > 0 else KEY_DOWN,
                spot=b.close, level=cap.flip, level_kind="flip", net_gex=cap.net_gex,
                capture_at=cap.captured_at,
            ))
    return out


def detect_wall_bounces(
    symbol: str, bars: Sequence[OhlcBar], captures: Sequence[Capture]
) -> list[LevelEvent]:
    """Every put- and call-wall bounce in one symbol's session, oldest first. Closed bars only."""
    bars = sorted(bars, key=lambda b: b.ts)
    out: list[LevelEvent] = []
    for key, kind, sign in ((KEY_PUT_BOUNCE, "put_wall", 1), (KEY_CALL_BOUNCE, "call_wall", -1)):
        caps = sorted(
            (c for c in captures if getattr(c, kind) is not None), key=lambda c: c.captured_at
        )
        t = 1
        while t + 2 < len(bars):
            cap = _latest(bars[t].ts + BAR, caps)
            if cap is None:
                t += 1
                continue
            w = getattr(cap, kind)
            prev, touch, c1, c2 = bars[t - 1], bars[t], bars[t + 1], bars[t + 2]
            # sign = +1 (put wall, from above) or -1 (call wall, from below), so one test does both
            extreme = touch.low if sign > 0 else touch.high
            touched = (
                sign * (prev.close - w) > 0
                and sign * (extreme - w) <= TOUCH_TOL * w
                and sign * (touch.close - w) > 0
            )
            if touched and sign * (c1.close - touch.close) > 0 and sign * (c2.close - c1.close) > 0:
                out.append(LevelEvent(
                    symbol=symbol, bar_ts=c2.ts, key=key, spot=c2.close, level=w,
                    level_kind=kind, net_gex=cap.net_gex, capture_at=cap.captured_at,
                    touch_ts=touch.ts,
                ))
                t += 3  # the next touch must come after the confirming bar
            else:
                t += 1
    return sorted(out, key=lambda e: (e.bar_ts, e.key))


def detect_wall_breaks(
    symbol: str, bars: Sequence[OhlcBar], captures: Sequence[Capture]
) -> list[LevelEvent]:
    """Every put- and call-wall break in one symbol's session, oldest first. Closed bars only."""
    bars = sorted(bars, key=lambda b: b.ts)
    out: list[LevelEvent] = []
    # sign = the side price breaks *to*: below the put wall, above the call wall
    for key, kind, sign in ((KEY_PUT_BREAK, "put_wall", -1), (KEY_CALL_BREAK, "call_wall", 1)):
        caps = sorted(
            (c for c in captures if getattr(c, kind) is not None), key=lambda c: c.captured_at
        )
        rows: list[tuple[int, float, int, Capture]] = []  # (bar index, wall, side, capture)
        for i, b in enumerate(bars):
            cap = _latest(b.ts + BAR, caps)
            if cap is not None:
                w = getattr(cap, kind)
                rows.append((i, w, _sign(b.close - w), cap))
        for j in range(2, len(rows)):
            i, w, side, cap = rows[j]
            if (
                side == sign
                and rows[j - 1][2] == sign
                and rows[j - 2][2] == -sign
                and rows[j - 1][1] == w
                and rows[j - 2][1] == w
            ):
                b = bars[i]
                out.append(LevelEvent(
                    symbol=symbol, bar_ts=b.ts, key=key, spot=b.close, level=w,
                    level_kind=kind, net_gex=cap.net_gex, capture_at=cap.captured_at,
                ))
    return sorted(out, key=lambda e: (e.bar_ts, e.key))


def horizon_returns(
    event: LevelEvent, bars: Sequence[OhlcBar]
) -> tuple[dict[int, float | None], float | None]:
    """Returns in bps after `event`, signed in its direction: `({15: r, 30: r, 60: r}, r_close)`.

    `bars` must be the same session's bars. A horizon whose bar does not exist is `None`. The
    close return is from the session's last bar, so it is only final once the session is.
    """
    bars = sorted(bars, key=lambda b: b.ts)
    d = direction_of(event.key)
    by_ts = {b.ts: b.close for b in bars}

    def ret(close: float) -> float:
        return d * (close / event.spot - 1.0) * 1e4

    out: dict[int, float | None] = {}
    for minutes in HORIZONS_MIN:
        c = by_ts.get(event.bar_ts + dt.timedelta(minutes=minutes))
        out[minutes] = None if c is None else ret(c)
    last = bars[-1] if bars else None
    r_close = ret(last.close) if last is not None and last.ts >= event.bar_ts else None
    return out, r_close


def minute_points(
    bars: Sequence[OhlcBar], entry: float, direction: int, entry_at: dt.datetime
) -> list[tuple[float, float]]:
    """1-minute bars as an ordered excursion series: `(minutes after entry, excursion)`.

    Each bar contributes two points, its two extremes, ordered by the bar's colour (up bar: low
    then high). Excursion is `direction * (price - entry)`, so with `direction` taken from the
    event, positive means it followed through.
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
    """The swing path after one entry, in price points, signed in the event's direction.

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


def path_unit(bars: Sequence[OhlcBar], *, share: float = 0.5) -> float | None:
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
