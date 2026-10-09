"""Strategies the executor may trade (T151) -- pure: no I/O, no clock.

A strategy here is the **frozen spec** of a candidate in `docs/edges/` turned into a schedule
of legs and a kill-rule check. Only a candidate that passed its stage-1 gate belongs here, and
the rule must match its spec character for character. A changed rule is a new candidate with
a new name, not an edit to this file.

Each strategy gives:

* `legs(ny_date)`: the open and close legs for one New York session date, with the instants
  they are due and stop being valid.
* `stop_for(entry_price)`: the stop-loss every order carries.
* `kill(trades)`: the reason to pause, or `None`, from the closed round trips so far.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class Leg:
    key: str
    action: str  # open | close
    due_at: dt.datetime  # UTC
    expires_at: dt.datetime  # UTC


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    pnl_pct: float  # % of the open fill, after both fills' spread
    spread: float | None  # ask - bid at the open, in price


def _utc(day: dt.date, at: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, at, tzinfo=NY).astimezone(dt.UTC)


class GoldAsiaDrift:
    """[docs/edges/gold-asia-drift.md](../../../../docs/edges/gold-asia-drift.md), frozen
    2026-10-09: buy XAUUSD at 18:00 New York, sell at 03:00 the next morning, every session that
    has a next one. That is Monday-Thursday entries, because the backtest's Friday 18:00 has no
    bar (the market is shut).

    T151 adds a **disaster stop at -6%**, which the executor's terms require on every order. The
    worst adverse excursion in the 1,434 backtested nights was -5.07%, so it never triggers on
    that history and the tested rule is unchanged.
    """

    name = "gold_asia"
    symbol = "XAUUSD"
    side = "buy"
    magic = 260901  # identifies this strategy's positions in MT5
    disaster_stop = 0.06
    entry = dt.time(18, 0)
    exit = dt.time(3, 0)
    #: An entry later than this is not the tested rule; the leg expires instead of chasing.
    entry_window = dt.timedelta(minutes=10)
    #: A close is retried for this long, then the executor alerts and keeps the row pending.
    exit_window = dt.timedelta(hours=6)

    # Kill rules from the candidate file's bootstrap (units: % of price per oz).
    dd_limits = ((30, 6.4), (60, 8.1))
    dd_rolling = (250, 14.2)
    max_streak = 9
    decay_window = 60
    max_spread = 0.48

    def legs(self, ny_date: dt.date) -> list[Leg]:
        if ny_date.weekday() > 3:  # Friday-Sunday: no entry
            return []
        nxt = ny_date + dt.timedelta(days=1)
        opened = _utc(ny_date, self.entry)
        closed = _utc(nxt, self.exit)
        base = f"{self.name}:{ny_date.isoformat()}"
        return [
            Leg(f"{base}:open", "open", opened, opened + self.entry_window),
            Leg(f"{base}:close", "close", closed, closed + self.exit_window),
        ]

    def stop_for(self, entry_price: float) -> float:
        return round(entry_price * (1 - self.disaster_stop), 2)

    def kill(self, trades: list[ClosedTrade]) -> str | None:
        """The first kill rule tripped by the closed trades, oldest first, or None."""
        if not trades:
            return None
        r = [t.pnl_pct for t in trades]
        n = len(r)
        dd = _max_drawdown(r)
        for limit_n, limit in self.dd_limits:
            if n <= limit_n and dd > limit:
                return f"drawdown {dd:.2f}% > {limit}% within the first {limit_n} trades"
        if n > self.dd_limits[-1][0]:
            win, limit = self.dd_rolling
            dd_recent = _max_drawdown(r[-win:])
            if dd_recent > limit:
                return f"drawdown {dd_recent:.2f}% > {limit}% over the last {win} trades"
        streak = 0
        for x in reversed(r):
            if x >= 0:
                break
            streak += 1
        if streak >= self.max_streak:
            return f"{streak} losing nights in a row"
        if n >= self.decay_window:
            w = r[-self.decay_window:]
            mean = sum(w) / len(w)
            sd = math.sqrt(sum((x - mean) ** 2 for x in w) / (len(w) - 1))
            if mean + sd / math.sqrt(len(w)) < 0:
                return f"expectancy decay: mean of last {len(w)} = {mean:.3f}% (+1 SE < 0)"
        spreads = [t.spread for t in trades[-20:] if t.spread is not None]
        if len(spreads) >= 20 and sum(spreads) / len(spreads) > self.max_spread:
            return f"cost drift: mean spread {sum(spreads) / len(spreads):.2f} > {self.max_spread}"
        return None


def _max_drawdown(r: list[float]) -> float:
    """Largest peak-to-trough fall of the cumulative sum, as a positive number."""
    peak = cum = worst = 0.0
    for x in r:
        cum += x
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


STRATEGIES = {s.name: s for s in (GoldAsiaDrift(),)}
