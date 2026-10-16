"""Strategies the executor may trade (T151) -- pure: no I/O, no clock.

A strategy here is the **frozen spec** of a candidate in `docs/edges/` turned into a schedule
of legs and a kill-rule check. Only a candidate that passed its stage-1 gate belongs here, and
the rule must match its spec character for character. A changed rule is a new candidate with
a new name, not an edit to this file.

Each strategy gives:

* `legs(ny_date)`: the legs for one New York session date, with the instants they are due and
  stop being valid. A fixed-time strategy gives its open and close legs. A strategy that decides
  from prices gives one `decide` leg, and the executor writes the open or close it calls for
  (`signal`).
* `stop_for(entry_price)`: the stop-loss every order carries.
* `kill(trades)`: the reason to pause, or `None`, from the closed round trips so far.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class Leg:
    key: str
    action: str  # open | close | decide
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


class NasMeanReversion:
    """[docs/edges/nas-mean-reversion.md](../../../../docs/edges/nas-mean-reversion.md), frozen
    2026-10-09, stage 1 passed on ^NDX 2000-19. At each 16:00 New York close of NAS100.fs:

    * flat, and the close > its 200-session SMA **and** any of RSI(2) (Wilder) < 10,
      IBS < 0.2, or the close is the lowest of 7 sessions -> buy;
    * long, and the close > its 5-session SMA -> sell. No target.

    The bars are the backtest's (`scripts/edges/_common.py`, `rth_daily`): a session is a New
    York date with a 15:59 M1 bar; its close is the open of the first M1 bar in [16:00, 16:10);
    high and low span the M1 bars in [09:30, 16:00). Prices are the CFD's **raw** quotes, roll
    jumps included, as in the 2019-26 broker-span backtest (the user's choice, 2026-10-15).
    On the exit day the rule does not re-enter, as in the backtest's latch.

    The disaster stop is the spec's -8%. The kill rules are the spec's four, as frozen.
    """

    name = "nas_mr"
    symbol = "NAS100.fs"
    side = "buy"
    magic = 260902
    disaster_stop = 0.08
    decide_at = dt.time(16, 0)
    #: The 16:00 bar reaches `broker.bars` a minute or two after it closes.
    decide_window = dt.timedelta(minutes=10)
    entry_window = dt.timedelta(minutes=10)
    exit_window = dt.timedelta(hours=6)
    #: Calendar days of M1 a decision reads: 200 sessions plus RSI warm-up.
    history = dt.timedelta(days=330)

    dd_limit = 15.0
    max_streak = 6
    decay_window = 30

    def legs(self, ny_date: dt.date) -> list[Leg]:
        if ny_date.weekday() > 4:
            return []
        due = _utc(ny_date, self.decide_at)
        return [Leg(f"{self.name}:{ny_date.isoformat()}:decide", "decide", due,
                    due + self.decide_window)]

    def stop_for(self, entry_price: float) -> float:
        return round(entry_price * (1 - self.disaster_stop), 2)

    def signal(self, daily: pd.DataFrame, holding: bool) -> tuple[str | None, str]:
        """(`open` | `close` | None, the values that decided it) on the last session of
        `daily` (columns close, high, low; one row per session, oldest first)."""
        c, h, lo = daily["close"], daily["high"], daily["low"]
        up = c.diff().clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
        dn = (-c.diff().clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
        rsi = 100 - 100 / (1 + up / dn)
        ibs = (c - lo) / (h - lo)
        sma200, sma5 = c.rolling(200).mean(), c.rolling(5).mean()
        low7 = c <= c.rolling(7).min()
        x = c.iloc[-1]
        why = (f"close {x:.2f}, SMA200 {sma200.iloc[-1]:.2f}, SMA5 {sma5.iloc[-1]:.2f}, "
               f"RSI2 {rsi.iloc[-1]:.1f}, IBS {ibs.iloc[-1]:.2f}, 7-day low {bool(low7.iloc[-1])}, "
               f"{len(c)} sessions")
        if holding:
            return ("close" if x > sma5.iloc[-1] else None), why
        entry = x > sma200.iloc[-1] and (rsi.iloc[-1] < 10 or ibs.iloc[-1] < 0.2 or low7.iloc[-1])
        return ("open" if entry else None), why

    def kill(self, trades: list[ClosedTrade]) -> str | None:
        """The first kill rule tripped by the closed trades, oldest first, or None."""
        if not trades:
            return None
        r = [t.pnl_pct for t in trades]
        dd = _max_drawdown(r)
        if dd > self.dd_limit:
            return f"drawdown {dd:.2f}% of notional > {self.dd_limit}%"
        streak = 0
        for x in reversed(r):
            if x >= 0:
                break
            streak += 1
        if streak >= self.max_streak:
            return f"{streak} losing trades in a row"
        if len(r) >= self.decay_window:
            w = r[-self.decay_window:]
            mean = sum(w) / len(w)
            sd = math.sqrt(sum((v - mean) ** 2 for v in w) / (len(w) - 1))
            if mean + sd / math.sqrt(len(w)) < 0:
                return f"expectancy decay: mean of last {len(w)} = {mean:.3f}% (+1 SE < 0)"
        return None


def rth_daily(m1: pd.DataFrame) -> pd.DataFrame:
    """Sessions from M1 bars (`ts` UTC bar opens as the index; open, high, low): one row per New
    York date that has a 15:59 bar, with `close` = the open of the first bar in [16:00, 16:10)
    and `high`/`low` over [09:30, 16:00). Rows missing any of the three are dropped."""
    if m1.empty:
        return pd.DataFrame(columns=["close", "high", "low"])
    ny = m1.index.tz_convert(NY)
    mins = ny.hour * 60 + ny.minute
    day = ny.tz_localize(None).normalize()
    rth = (mins >= 570) & (mins < 960)
    out = m1[rth].groupby(day[rth]).agg(high=("high", "max"), low=("low", "min"))
    at16 = (mins >= 960) & (mins < 970)
    out["close"] = m1[at16].groupby(day[at16])["open"].first()
    sessions = pd.Index(day[mins == 959]).unique()
    out = out[out.index.isin(sessions)].dropna()
    out.index = pd.Index([d.date() for d in out.index], name="session")
    return out[["close", "high", "low"]]


def _max_drawdown(r: list[float]) -> float:
    """Largest peak-to-trough fall of the cumulative sum, as a positive number."""
    peak = cum = worst = 0.0
    for x in r:
        cum += x
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


STRATEGIES = {s.name: s for s in (GoldAsiaDrift(), NasMeanReversion())}
