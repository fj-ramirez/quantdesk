"""Opportunity outcomes -- pure module (T61).

Replays the daily bars that came *after* an opportunity was recorded against its entry, stop
and target, and says what happened: did the entry trigger, and did the trade then reach the
target, the stop, or neither before the holding window ran out. Every result is expressed in
**R** -- multiples of the planned risk `|entry - stop|` -- so a fade on GLD and a continuation
on SPX are comparable, and so the track record `summarize_outcomes` builds can be read by setup
and grade without any per-symbol scaling.

**Pure**, on the same contract as every other `app.scan` module: no HTTP, no database, no
filesystem, no logging, no clock. The caller (`app.jobs.decisions`) fetches the bars and
persists the result; this module only reads the frame it is handed.

Fill and exit rules -- deliberately conservative, so the track record understates rather than
flatters the engine:

* A **fade** entry is a resting limit at the wall. It triggers on the first bar whose range
  touches the entry (high >= entry for a SHORT, low <= entry for a LONG) within
  :data:`TRIGGER_WINDOW_BARS`; a bar that *gaps through* the level fills at its open (which is
  the better price, and the realistic one). No touch inside the window -> ``untriggered``.
* A **continuation** entry is at market, so it fills at the **next bar's open**, never at the
  recorded spot -- the spot was an end-of-day print nobody could trade at.
* From the trigger bar on, each bar is checked for the stop *before* the target. A bar that
  touches both is scored as a **stop** and says so in `note`: daily bars carry no intrabar
  order, and the honest reading of an ambiguous bar is the losing one.
* A gap through the stop exits at the open (worse than the stop; recorded as such). A gap
  through the target exits at the open (better; recorded as such).
* After :data:`MAX_HOLD_BARS` bars without resolution the trade is ``expired`` and marked at
  that bar's close. While bars remain and nothing has resolved it is ``pending`` with `mark_r`
  carrying the unrealized R at the last close.

`mfe_r` / `mae_r` (maximum favourable / adverse excursion, in R) are tracked from the trigger
bar through resolution, so a later calibration pass can ask "would a wider stop or a nearer
target have done better" without re-running anything.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd

__all__ = [
    "MAX_HOLD_BARS",
    "TRIGGER_WINDOW_BARS",
    "GroupStats",
    "Outcome",
    "TrackRecord",
    "TradeSpec",
    "evaluate",
    "summarize_outcomes",
]

#: Bars a fade's resting limit stays working before the opportunity is scored `untriggered`.
#: A week: the chain is recaptured daily and the wall will have moved or been re-suggested by
#: then, so an older resting order is a stale one.
TRIGGER_WINDOW_BARS = 5

#: Bars a triggered trade is held before it is scored `expired` at the close. Two weeks -- long
#: enough for a wall-to-wall move on the ETFs this app covers, short enough that the regime the
#: trade was built on is still the regime being tested.
MAX_HOLD_BARS = 10

_RESOLVED = frozenset({"target", "stop", "expired"})


def _f(x: Any) -> float | None:
    if x is None:
        return None
    value = float(x)
    return value if math.isfinite(value) else None


@dataclass(frozen=True, slots=True)
class TradeSpec:
    """The four numbers an evaluation needs; a projection of `app.scan.decisions.Opportunity`."""

    setup: str
    side: str
    entry: float
    stop: float
    target: float


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the bars said. `outcome` is one of `pending`, `untriggered`, `target`, `stop`,
    `expired`, or `invalid` (a spec whose stop equals its entry -- cannot be scored in R)."""

    outcome: str
    fill: float | None
    triggered_on: dt.date | None
    resolved_on: dt.date | None
    bars_held: int | None
    mfe_r: float | None
    mae_r: float | None
    result_r: float | None
    mark_r: float | None
    evaluated_through: dt.date | None
    note: str

    @property
    def resolved(self) -> bool:
        return self.outcome in _RESOLVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "fill": _f(self.fill),
            "triggered_on": None if self.triggered_on is None else self.triggered_on.isoformat(),
            "resolved_on": None if self.resolved_on is None else self.resolved_on.isoformat(),
            "bars_held": self.bars_held,
            "mfe_r": _f(self.mfe_r),
            "mae_r": _f(self.mae_r),
            "result_r": _f(self.result_r),
            "mark_r": _f(self.mark_r),
            "evaluated_through": (
                None if self.evaluated_through is None else self.evaluated_through.isoformat()
            ),
            "note": self.note,
        }


def _pending(
    *,
    bars: pd.DataFrame,
    fill: float | None = None,
    triggered_on: dt.date | None = None,
    bars_held: int | None = None,
    mfe: float | None = None,
    mae: float | None = None,
    mark: float | None = None,
    note: str,
) -> Outcome:
    through = None if bars.empty else bars["date"].iloc[-1]
    return Outcome(
        outcome="pending",
        fill=fill,
        triggered_on=triggered_on,
        resolved_on=None,
        bars_held=bars_held,
        mfe_r=mfe,
        mae_r=mae,
        result_r=None,
        mark_r=mark,
        evaluated_through=through,
        note=note,
    )


def evaluate(
    spec: TradeSpec,
    bars: pd.DataFrame,
    *,
    trigger_window: int = TRIGGER_WINDOW_BARS,
    max_hold: int = MAX_HOLD_BARS,
) -> Outcome:
    """Score `spec` against `bars`, the daily bars strictly after the decision date, ascending.

    Never raises on the bars' *content*: an empty frame is `pending`, a frame short of the
    trigger window is `pending`. Raises only if `bars` lacks the OHLC columns.
    """
    direction = 1 if spec.side == "LONG" else -1
    risk = abs(spec.entry - spec.stop)
    if risk <= 0 or not math.isfinite(risk):
        return Outcome(
            outcome="invalid",
            fill=None,
            triggered_on=None,
            resolved_on=None,
            bars_held=None,
            mfe_r=None,
            mae_r=None,
            result_r=None,
            mark_r=None,
            evaluated_through=None,
            note="entry and stop coincide: no risk unit to score in",
        )
    if bars.empty:
        return _pending(bars=bars, note="no bars after the decision date yet")

    opens = bars["open"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    dates = list(bars["date"])
    n = len(bars)

    # --- trigger -------------------------------------------------------------------------
    fill: float | None = None
    trigger_index: int | None = None
    if spec.setup == "continuation":
        fill = float(opens[0])
        trigger_index = 0
        trigger_note = "filled at the next session's open"
    else:
        for i in range(min(n, trigger_window)):
            touched = highs[i] >= spec.entry if direction < 0 else lows[i] <= spec.entry
            if touched:
                # A gap through the level fills at the open -- the better price for a limit.
                fill = float(max(opens[i], spec.entry) if direction < 0 else min(opens[i], spec.entry))
                trigger_index = i
                break
        if trigger_index is None:
            if n >= trigger_window:
                return Outcome(
                    outcome="untriggered",
                    fill=None,
                    triggered_on=None,
                    resolved_on=None,
                    bars_held=None,
                    mfe_r=None,
                    mae_r=None,
                    result_r=None,
                    mark_r=None,
                    evaluated_through=dates[trigger_window - 1],
                    note=f"the wall was not touched within {trigger_window} bars",
                )
            return _pending(bars=bars, note=f"resting limit not yet touched ({n} of {trigger_window} bars)")
        gapped = fill != spec.entry
        trigger_note = "filled at the open (gapped through the wall)" if gapped else "filled at the wall"

    assert fill is not None and trigger_index is not None

    def r_of(price: float) -> float:
        return (price - fill) * direction / risk

    # --- hold ----------------------------------------------------------------------------
    mfe = -math.inf
    mae = math.inf
    held = 0
    for j in range(trigger_index, n):
        held = j - trigger_index + 1
        favourable = highs[j] if direction > 0 else lows[j]
        adverse = lows[j] if direction > 0 else highs[j]
        mfe = max(mfe, r_of(float(favourable)))
        mae = min(mae, r_of(float(adverse)))

        stop_hit = lows[j] <= spec.stop if direction > 0 else highs[j] >= spec.stop
        target_hit = highs[j] >= spec.target if direction > 0 else lows[j] <= spec.target

        if stop_hit:
            exit_price = float(min(spec.stop, opens[j]) if direction > 0 else max(spec.stop, opens[j]))
            note = f"{trigger_note}; stop hit"
            if target_hit:
                note += " (bar also touched the target: scored as a stop, since daily bars carry no intrabar order)"
            elif exit_price != spec.stop:
                note += " (gapped through the stop: exited at the open)"
            return Outcome(
                outcome="stop",
                fill=fill,
                triggered_on=dates[trigger_index],
                resolved_on=dates[j],
                bars_held=held,
                mfe_r=mfe,
                mae_r=mae,
                result_r=r_of(exit_price),
                mark_r=None,
                evaluated_through=dates[j],
                note=note,
            )
        if target_hit:
            exit_price = float(max(spec.target, opens[j]) if direction > 0 else min(spec.target, opens[j]))
            note = f"{trigger_note}; target hit"
            if exit_price != spec.target:
                note += " (gapped through the target: exited at the open)"
            return Outcome(
                outcome="target",
                fill=fill,
                triggered_on=dates[trigger_index],
                resolved_on=dates[j],
                bars_held=held,
                mfe_r=mfe,
                mae_r=mae,
                result_r=r_of(exit_price),
                mark_r=None,
                evaluated_through=dates[j],
                note=note,
            )
        if held >= max_hold:
            return Outcome(
                outcome="expired",
                fill=fill,
                triggered_on=dates[trigger_index],
                resolved_on=dates[j],
                bars_held=held,
                mfe_r=mfe,
                mae_r=mae,
                result_r=r_of(float(closes[j])),
                mark_r=None,
                evaluated_through=dates[j],
                note=f"{trigger_note}; neither level reached in {max_hold} bars, marked at the close",
            )

    return _pending(
        bars=bars,
        fill=fill,
        triggered_on=dates[trigger_index],
        bars_held=held,
        mfe=mfe,
        mae=mae,
        mark=r_of(float(closes[-1])),
        note=f"{trigger_note}; open, {held} of {max_hold} bars held",
    )


# --------------------------------------------------------------------------------------
# Track record
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroupStats:
    """Aggregate for one bucket of recorded opportunities (all, one setup, or one grade).

    `hit_rate` is targets over *resolved* trades (target + stop + expired); `win_rate` is
    trades with `result_r > 0` over resolved -- an expired trade marked in profit counts as a
    win there but not as a hit. Both are `None` below :data:`MIN_RESOLVED_FOR_RATE` resolved
    trades, for the same reason `app.scan.breakouts.summarize` withholds a rate on fewer
    than five events: three data points are noise whatever they say.
    """

    n: int
    pending: int
    untriggered: int
    resolved: int
    targets: int
    stops: int
    expired: int
    hit_rate: float | None
    win_rate: float | None
    avg_r: float | None
    total_r: float | None
    best_r: float | None
    worst_r: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "pending": self.pending,
            "untriggered": self.untriggered,
            "resolved": self.resolved,
            "targets": self.targets,
            "stops": self.stops,
            "expired": self.expired,
            "hit_rate": _f(self.hit_rate),
            "win_rate": _f(self.win_rate),
            "avg_r": _f(self.avg_r),
            "total_r": _f(self.total_r),
            "best_r": _f(self.best_r),
            "worst_r": _f(self.worst_r),
        }


#: Below this many resolved trades a bucket reports `hit_rate`/`win_rate` as `None`.
MIN_RESOLVED_FOR_RATE = 5


@dataclass(frozen=True, slots=True)
class TrackRecord:
    overall: GroupStats
    by_setup: dict[str, GroupStats]
    by_grade: dict[str, GroupStats]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall.to_dict(),
            "by_setup": {k: v.to_dict() for k, v in self.by_setup.items()},
            "by_grade": {k: v.to_dict() for k, v in self.by_grade.items()},
        }


def _stats(items: list[tuple[str, float | None]]) -> GroupStats:
    resolved_r = [r for outcome, r in items if outcome in _RESOLVED and r is not None]
    targets = sum(1 for outcome, _ in items if outcome == "target")
    stops = sum(1 for outcome, _ in items if outcome == "stop")
    expired = sum(1 for outcome, _ in items if outcome == "expired")
    resolved = targets + stops + expired
    enough = resolved >= MIN_RESOLVED_FOR_RATE
    return GroupStats(
        n=len(items),
        pending=sum(1 for outcome, _ in items if outcome == "pending"),
        untriggered=sum(1 for outcome, _ in items if outcome == "untriggered"),
        resolved=resolved,
        targets=targets,
        stops=stops,
        expired=expired,
        hit_rate=targets / resolved if enough else None,
        win_rate=sum(1 for r in resolved_r if r > 0) / resolved if enough else None,
        avg_r=sum(resolved_r) / len(resolved_r) if resolved_r else None,
        total_r=sum(resolved_r) if resolved_r else None,
        best_r=max(resolved_r) if resolved_r else None,
        worst_r=min(resolved_r) if resolved_r else None,
    )


def summarize_outcomes(
    records: Iterable[tuple[str, str, str, float | None]],
) -> TrackRecord:
    """Aggregate `(setup, grade, outcome, result_r)` tuples into a `TrackRecord`."""
    rows = list(records)
    by_setup: dict[str, list[tuple[str, float | None]]] = {}
    by_grade: dict[str, list[tuple[str, float | None]]] = {}
    for setup, grade, outcome, result_r in rows:
        by_setup.setdefault(setup, []).append((outcome, result_r))
        by_grade.setdefault(grade, []).append((outcome, result_r))
    return TrackRecord(
        overall=_stats([(o, r) for _, _, o, r in rows]),
        by_setup={k: _stats(v) for k, v in sorted(by_setup.items())},
        by_grade={k: _stats(v) for k, v in sorted(by_grade.items())},
    )
