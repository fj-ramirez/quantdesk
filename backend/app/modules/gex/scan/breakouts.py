"""Breakout ledger -- pure module (T43, plans/continuation/01-breakout-ledger.md).

Answers the plan's question empirically, per symbol: how often has a breakout of the N-day
range actually continued, and which breakouts are open right now. No indicator interpretation
and no signal generation -- this module only *records outcomes* of a mechanical rule against
history that already happened. **Pure, exactly like `app.modules.gex.gex.engine`**: no HTTP, DB, filesystem
or logging. Data comes in as a `pd.DataFrame` of daily bars (the shape `app.modules.gex.storage
.bars_repository.read_bars` returns -- see that module and `app.modules.gex.scan.indicators` for the exact
column contract), results go out as frozen dataclasses. `app.modules.gex.api.scan` does all the I/O:
fetching bars, slicing the lookback window, and building the HTTP response.


Definitions (plan's "Design decisions" -- the deliverable's contract)
-----------------------------------------------------------------------

* **Breakout event** at bar `t`: up if `close[t] > max(high[t-N..t-1])`, down if
  `close[t] < min(low[t-N..t-1])`. The range is built from the **prior** `N` bars only --
  `t` itself never contributes to its own boundary. Skipping this exclusion is the plan's
  named first-contact failure: a close can never exceed its own high, so every count would
  silently collapse to zero.
* **Clustering.** After a recorded event, no new event of the *same* direction is recorded
  until `k` bars have passed; the first event owns the window, so a same-direction close that
  gets suppressed never resets or extends the timer. Opposite-direction events are always
  recorded, independent of any in-progress window.
* **Level** = the range boundary that was broken (`range_high`/`range_low` at `t`), not the
  close that broke it.
* **Outcome after `k` bars**: `continued` if `sign · (close[t+k] − level) > 0`, `failed`
  otherwise, `pending` while fewer than `k` bars exist yet after `t`.
* **Follow-through** = `sign · (close[t+k] − level) / ATR14[t]`, `None` once resolved only if
  `ATR14[t]` itself is unmeasurable (insufficient history -- see `app.modules.gex.scan.indicators`) or
  zero. `excursion_atr` is the same quantity evaluated at the *last available* bar instead of
  strictly at `t+k`; it equals `follow_through_atr` exactly once the event has resolved and is
  the only excursion a still-`pending` event has. `mfe_atr`/`mae_atr` are the running max/min of
  that same per-bar quantity over the bars elapsed so far (final and fixed once resolved,
  provisional while pending), for the detail view.
* **Minimum events to print a rate: 5.** `BreakoutSummary.rate` is `None` below that -- three
  events is noise, and this module does not print noise as a number.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from app.modules.gex.scan.indicators import ATR_PERIOD, atr

__all__ = [
    "DEFAULT_K",
    "DEFAULT_LOOKBACK",
    "DEFAULT_N",
    "MIN_EVENTS_FOR_RATE",
    "BreakoutEvent",
    "BreakoutSummary",
    "Direction",
    "Outcome",
    "detect_events",
    "summarize",
]

#: Plan default: N=20-day range, k=5-bar holding period, 126-bar (~6 month) summary lookback.
DEFAULT_N = 20
DEFAULT_K = 5
DEFAULT_LOOKBACK = 126

#: Below this many events, `summarize` reports `rate=None` -- see the module docstring.
MIN_EVENTS_FOR_RATE = 5


class Direction(StrEnum):
    """Which boundary a breakout event broke."""

    UP = "up"
    DOWN = "down"

    @property
    def sign(self) -> int:
        """+1 for `UP`, -1 for `DOWN` -- the sign the outcome/follow-through math multiplies
        by, mirroring `app.modules.gex.gex.engine`'s dealer-sign convention (+calls/-puts) in spirit: the
        sign is applied exactly once, here, rather than re-derived at every call site.
        """
        return 1 if self is Direction.UP else -1


class Outcome(StrEnum):
    """How a breakout event's `k`-bar evaluation window resolved."""

    CONTINUED = "continued"
    FAILED = "failed"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class BreakoutEvent:
    """One recorded breakout: the level that broke, when, and how it has played out so far.

    `follow_through_atr` is only ever set once the event has `resolved_at` a date (i.e. once
    `outcome` is `CONTINUED` or `FAILED`); a `PENDING` event's only excursion reading is
    `excursion_atr`, taken at the last bar available rather than at the not-yet-reached `t+k`.
    Both are `None` when `ATR14` at the event bar was itself unmeasurable (insufficient
    history) or exactly zero -- never a fabricated `0.0` masquerading as "no move yet".
    """

    date: dt.date
    direction: Direction
    level: float
    close: float
    outcome: Outcome
    resolved_at: dt.date | None
    bars_elapsed: int
    follow_through_atr: float | None
    excursion_atr: float | None
    mfe_atr: float | None
    mae_atr: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "direction": str(self.direction),
            "level": float(self.level),
            "close": float(self.close),
            "outcome": str(self.outcome),
            "resolved_at": None if self.resolved_at is None else self.resolved_at.isoformat(),
            "bars_elapsed": int(self.bars_elapsed),
            "follow_through_atr": self.follow_through_atr,
            "excursion_atr": self.excursion_atr,
            "mfe_atr": self.mfe_atr,
            "mae_atr": self.mae_atr,
        }


@dataclass(frozen=True, slots=True)
class BreakoutSummary:
    """Aggregate outcome record for one symbol's events over one lookback window.

    `rate` is `continued / (continued + failed)` -- computed over **resolved** events only,
    since a still-open event has no outcome to count either way -- but is reported as `None`
    whenever the **total** event count (`events`, including pending ones) is below
    `MIN_EVENTS_FOR_RATE`. A symbol that happens to have three events, all already resolved,
    still reports `rate=None`: three data points are noise regardless of how many of them
    happened to finish.
    """

    lookback: int
    events: int
    continued: int
    failed: int
    pending: int
    rate: float | None
    mean_follow_through_atr: float | None
    last_event: BreakoutEvent | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lookback": int(self.lookback),
            "events": int(self.events),
            "continued": int(self.continued),
            "failed": int(self.failed),
            "pending": int(self.pending),
            "rate": self.rate,
            "mean_follow_through_atr": self.mean_follow_through_atr,
            "last_event": None if self.last_event is None else self.last_event.to_dict(),
        }


def detect_events(
    bars: pd.DataFrame, n: int = DEFAULT_N, k: int = DEFAULT_K
) -> list[BreakoutEvent]:
    """Scan `bars` for N-day range breakouts and record each one's `k`-bar outcome.

    Args:
        bars: Ascending-by-date daily bars (`date, open, high, low, close, volume, source`,
            the `read_bars` shape). Not required to be pre-trimmed to any particular window --
            callers wanting a bounded lookback pass a slice; this function itself has no notion
            of "recent" and simply walks every row it is given. Must not contain a gap this
            module is unaware of: an omitted row is invisible to the rolling-window math and is
            silently treated as "this day didn't happen" rather than "data is missing here" --
            `missing_bar_fraction` is how a caller checks for that before trusting the output.
        n: Range length in bars (the N-day high/low window). Plan default 20; the design
            document also names 55, but this function accepts any `n >= 1` -- the API layer is
            where the {20, 55} restriction (or lack of one) is enforced, not this module.
        k: Holding/evaluation period in bars. Plan default 5; same story as `n` for {3, 5, 10}.

    Returns:
        One `BreakoutEvent` per recorded breakout, in bar order (ascending by `date`). Empty
        when `bars` has fewer than `n + 1` rows -- there is no completed N-bar range to break
        out of yet.

    Raises:
        ValueError: `n < 1` or `k < 1`.
    """
    if n < 1 or k < 1:
        raise ValueError(f"n and k must be >= 1, got n={n}, k={k}")
    if bars.empty or len(bars) < n + 1:
        return []

    bars = bars.reset_index(drop=True)
    close = bars["close"].to_numpy(dtype=float)
    dates = bars["date"].to_numpy(dtype=object)

    # The range at bar t is built from bars [t-n .. t-1] only: shift(1) moves the whole series
    # one bar later *before* the rolling extrema is taken, so the window ending at t's position
    # actually covers t-n..t-1. Skipping the shift is the plan's named first-contact failure --
    # a close can never exceed its own high, so every event count would collapse to zero.
    range_high = (
        bars["high"].shift(1).rolling(window=n, min_periods=n).max().to_numpy(dtype=float)
    )
    range_low = bars["low"].shift(1).rolling(window=n, min_periods=n).min().to_numpy(dtype=float)

    atr_values = atr(bars, ATR_PERIOD).to_numpy(dtype=float)

    n_bars = len(bars)
    events: list[BreakoutEvent] = []
    # Bar index of the last *recorded* event, per direction. A suppressed same-direction close
    # never updates this -- "the first event owns the window" (plan's clustering rule) -- so a
    # run of several consecutive qualifying closes clusters into exactly one event.
    last_recorded: dict[Direction, int] = {Direction.UP: -(10**9), Direction.DOWN: -(10**9)}

    for t in range(n_bars):
        if np.isnan(range_high[t]) or np.isnan(range_low[t]):
            continue  # fewer than n prior bars available yet

        if close[t] > range_high[t]:
            direction = Direction.UP
            level = float(range_high[t])
        elif close[t] < range_low[t]:
            direction = Direction.DOWN
            level = float(range_low[t])
        else:
            continue

        if t - last_recorded[direction] < k:
            continue  # inside a same-direction event's clustering window
        last_recorded[direction] = t

        sign = direction.sign
        atr_t = atr_values[t]
        has_atr = not np.isnan(atr_t) and atr_t > 0.0

        target = t + k
        window_end = min(target, n_bars - 1)
        window_closes = close[t + 1 : window_end + 1]

        mfe: float | None = None
        mae: float | None = None
        current_excursion: float | None = None
        if window_closes.size and has_atr:
            excursions = sign * (window_closes - level) / atr_t
            mfe = float(excursions.max())
            mae = float(excursions.min())
            current_excursion = float(excursions[-1])

        if target >= n_bars:
            outcome = Outcome.PENDING
            resolved_at = None
            follow_through: float | None = None
        else:
            outcome = (
                Outcome.CONTINUED if sign * (close[target] - level) > 0.0 else Outcome.FAILED
            )
            resolved_at = dates[target]
            # window_end == target whenever the event has resolved, so the last element of
            # `excursions` computed above is exactly sign*(close[target]-level)/atr_t.
            follow_through = current_excursion

        events.append(
            BreakoutEvent(
                date=dates[t],
                direction=direction,
                level=level,
                close=float(close[t]),
                outcome=outcome,
                resolved_at=resolved_at,
                bars_elapsed=window_end - t,
                follow_through_atr=follow_through,
                excursion_atr=current_excursion,
                mfe_atr=mfe,
                mae_atr=mae,
            )
        )

    return events


def summarize(
    events: Sequence[BreakoutEvent], lookback: int = DEFAULT_LOOKBACK
) -> BreakoutSummary:
    """Aggregate `events` into one `BreakoutSummary`.

    `events` is expected to already be the set the caller wants summarized -- typically the
    events `detect_events` found within the last `lookback` bars of a symbol's history, since
    this function does no date filtering of its own (it never receives the full `bars` frame,
    only the already-detected events, so it has no bar index to filter against). `lookback` is
    therefore carried through as **metadata** onto the result, not used to trim `events` here;
    trimming to a window is the caller's job before this is called.

    Args:
        events: Events to summarize, any order.
        lookback: Recorded on the result for the API response; see above.

    Returns:
        `BreakoutSummary`. `rate` and `mean_follow_through_atr` are `None` on an empty
        `events` (nothing to summarize) or on too few events (`rate` only -- see
        `BreakoutSummary`'s docstring for the exact five-event threshold).
    """
    events = list(events)
    resolved = [e for e in events if e.outcome is not Outcome.PENDING]
    continued = sum(1 for e in resolved if e.outcome is Outcome.CONTINUED)
    failed = len(resolved) - continued
    pending = len(events) - len(resolved)

    rate: float | None = None
    if len(events) >= MIN_EVENTS_FOR_RATE and resolved:
        rate = continued / len(resolved)

    follow_throughs = [e.follow_through_atr for e in resolved if e.follow_through_atr is not None]
    mean_follow_through = float(np.mean(follow_throughs)) if follow_throughs else None

    last_event = max(events, key=lambda e: e.date) if events else None

    return BreakoutSummary(
        lookback=lookback,
        events=len(events),
        continued=continued,
        failed=failed,
        pending=pending,
        rate=rate,
        mean_follow_through_atr=mean_follow_through,
        last_event=last_event,
    )


#: Deliberately **not** provided here: a "fraction of bars missing" helper. An early version of
#: this module had one, approximating "expected trading days" as plain Mon-Fri weekdays
#: (`pandas.bdate_range`) since this module cannot import `app.modules.gex.jobs.calendar` (the purity
#: contract forbids it, and that module logs at ERROR on years outside its 2026-2027 table).
#: Measured against the live, populated database (2026-09-09) that approximation was **useless
#: in practice**: an ordinary 126-bar (~6-month) lookback spans 4-5 real NYSE holidays, which
#: read as ~3.8% "missing" from weekday counting alone -- comfortably past the plan's 2%
#: exclusion threshold, so *every single symbol in the universe* was wrongly excluded, holidays
#: alone accounting for nearly double the noise a genuine gap was meant to be caught at. A
#: calendar-blind gap check cannot be calibrated to both catch a real outage and tolerate normal
#: holiday noise, because the two look the same size. `app.modules.gex.api.scan` (which does I/O already,
#: and is not bound by this module's purity contract) does this check itself instead, using
#: `app.modules.gex.jobs.calendar.is_trading_day` for an exchange-holiday-aware expected count -- see that
#: module's docstring for the reasoning and `docs/validation-scan.md` for the measurement.
