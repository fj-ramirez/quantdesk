"""Strategy families — the hypothesis space the search explores.

Each family maps a parameter dict to a target-position series (-1/0/+1)
computed only from data available at each bar's close. To add a new family:
subclass Strategy, define `param_space`, implement `signals`, and add it to
FAMILIES. The search picks it up automatically.

Every family also carries a `regime` parameter: an ex-ante detectable market
state the strategy trades in (flat otherwise). Regime-specific edges are
first-class hypotheses here — the regime definition below IS the
documentation of when such a candidate trades.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --- Regimes: boolean masks computed only from trailing data (ex-ante). ---
# trend_up / trend_down: close above/below its 200-bar SMA.
# high_vol / low_vol: 30-bar return volatility above/below its trailing
# 200-bar median. NaN warmup compares False -> strategy stays flat.

TREND_N = 200
VOL_N = 30
VOL_BASE_N = 200


def regime_mask(df: pd.DataFrame, regime: str) -> pd.Series | None:
    if regime == "any":
        return None
    sma = df["close"].rolling(TREND_N).mean()
    if regime == "trend_up":
        return df["close"] > sma
    if regime == "trend_down":
        return df["close"] < sma
    vol = df["close"].pct_change().rolling(VOL_N).std()
    base = vol.rolling(VOL_BASE_N).median()
    if regime == "high_vol":
        return vol > base
    if regime == "low_vol":
        return vol < base
    raise ValueError(f"unknown regime: {regime}")


REGIMES = ["any", "trend_up", "trend_down", "high_vol", "low_vol"]

REGIME_DOCS = {
    "any": "Trades in all market conditions.",
    "trend_up": f"Trades only while the close is above its {TREND_N}-bar simple moving average.",
    "trend_down": f"Trades only while the close is below its {TREND_N}-bar simple moving average.",
    "high_vol": (f"Trades only while {VOL_N}-bar return volatility is above its "
                 f"trailing {VOL_BASE_N}-bar median."),
    "low_vol": (f"Trades only while {VOL_N}-bar return volatility is below its "
                f"trailing {VOL_BASE_N}-bar median."),
}

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _stateful(entry: pd.Series, exit_: pd.Series, side: float = 1.0) -> pd.Series:
    """Hold `side` from an entry signal until an exit signal (vectorized latch)."""
    s = pd.Series(np.nan, index=entry.index)
    s[entry] = side
    s[exit_] = 0.0
    return s.ffill().fillna(0.0)


class Strategy:
    name: str = ""
    param_space: dict[str, list] = {}

    def valid(self, p: dict) -> bool:
        return True

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        raise NotImplementedError

    def positions(self, df: pd.DataFrame, p: dict) -> pd.Series:
        """Final target positions: signals clipped and gated by the regime.
        All evaluation code must use this, not signals()."""
        pos = self.signals(df, p).clip(-1.0, 1.0)
        mask = regime_mask(df, p.get("regime", "any"))
        return pos.where(mask, 0.0) if mask is not None else pos

    def describe(self, p: dict) -> str:
        """Plain-English statement of the trading rule (excluding the regime)."""
        return f"{self.name} with parameters {p}"


class EmaCross(Strategy):
    """Trend following: long while fast EMA above slow EMA, optionally short below."""

    name = "ema_cross"
    param_space = {
        "fast": [3, 5, 8, 12, 20, 30, 50],
        "slow": [30, 50, 80, 120, 200, 300],
        "long_short": [False, True],
    }

    def valid(self, p: dict) -> bool:
        return p["fast"] < p["slow"]

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        fast = df["close"].ewm(span=p["fast"], adjust=False).mean()
        slow = df["close"].ewm(span=p["slow"], adjust=False).mean()
        pos = (fast > slow).astype(float)
        if p["long_short"]:
            pos = pos - (fast < slow).astype(float)
        pos.iloc[: p["slow"]] = 0.0
        return pos

    def describe(self, p: dict) -> str:
        s = f"Long while the {p['fast']}-bar EMA is above the {p['slow']}-bar EMA"
        return s + ("; short while below." if p["long_short"] else "; flat otherwise.")


class DonchianBreakout(Strategy):
    """Long when price breaks the N-bar high; exit on the M-bar low."""

    name = "donchian_breakout"
    param_space = {
        "entry_n": [10, 20, 30, 40, 55, 80, 120, 160],
        "exit_n": [5, 10, 20, 40, 80],
    }

    def valid(self, p: dict) -> bool:
        return p["exit_n"] < p["entry_n"]

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        hi = df["high"].rolling(p["entry_n"]).max().shift(1)
        lo = df["low"].rolling(p["exit_n"]).min().shift(1)
        return _stateful(df["close"] > hi, df["close"] < lo)

    def describe(self, p: dict) -> str:
        return (f"Enter long when the close breaks above the prior {p['entry_n']}-bar high; "
                f"exit when it breaks below the prior {p['exit_n']}-bar low.")


class ZScoreMeanRev(Strategy):
    """Buy when price is `entry_z` std-devs below its SMA; exit near the mean."""

    name = "zscore_meanrev"
    param_space = {
        "n": [10, 20, 40, 80, 160],
        "entry_z": [1.0, 1.5, 2.0, 2.5, 3.0],
        "exit_z": [0.0, 0.25, 0.5, 1.0],
        "long_short": [False, True],
    }

    def valid(self, p: dict) -> bool:
        return p["exit_z"] < p["entry_z"]

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        sma = df["close"].rolling(p["n"]).mean()
        std = df["close"].rolling(p["n"]).std()
        z = (df["close"] - sma) / std
        pos = _stateful(z < -p["entry_z"], z > -p["exit_z"])
        if p["long_short"]:
            pos = pos - _stateful(z > p["entry_z"], z < p["exit_z"])
        return pos

    def describe(self, p: dict) -> str:
        s = (f"Buy when price falls {p['entry_z']} standard deviations below its "
             f"{p['n']}-bar mean; exit when it recovers to {p['exit_z']} below")
        return s + ("; mirrored on the short side." if p["long_short"] else ".")


class RsiMeanRev(Strategy):
    """Short-period RSI dip buying (Connors-style), exit on RSI recovery."""

    name = "rsi_meanrev"
    param_space = {
        "n": [2, 3, 4, 5, 7, 10, 14],
        "buy_below": [5, 10, 15, 20, 25, 30],
        "exit_above": [50, 55, 60, 65, 70, 80],
        "trend_filter": [0, 50, 100, 200],  # only buy above this SMA; 0 = off
    }

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        delta = df["close"].diff()
        up = delta.clip(lower=0).ewm(alpha=1 / p["n"], adjust=False).mean()
        dn = (-delta.clip(upper=0)).ewm(alpha=1 / p["n"], adjust=False).mean()
        rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
        entry = rsi < p["buy_below"]
        if p["trend_filter"]:
            entry &= df["close"] > df["close"].rolling(p["trend_filter"]).mean()
        return _stateful(entry, rsi > p["exit_above"])

    def describe(self, p: dict) -> str:
        s = (f"Buy when the {p['n']}-bar RSI drops below {p['buy_below']}; "
             f"exit when it rises above {p['exit_above']}")
        if p["trend_filter"]:
            s += f" (only above the {p['trend_filter']}-bar SMA)"
        return s + "."


class TsMomentum(Strategy):
    """Time-series momentum: hold the sign of the trailing lookback return,
    optionally only when volatility is below its own rolling median."""

    name = "ts_momentum"
    param_space = {
        "lookback": [10, 20, 30, 60, 90, 120, 180, 270, 360],
        "long_short": [False, True],
        "vol_filter": [False, True],
    }

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        mom = df["close"].pct_change(p["lookback"])
        pos = (mom > 0).astype(float)
        if p["long_short"]:
            pos = pos - (mom < 0).astype(float)
        if p["vol_filter"]:
            vol = df["close"].pct_change().rolling(p["lookback"]).std()
            calm = vol < vol.rolling(p["lookback"] * 2).median()
            pos = pos.where(calm, 0.0)
        return pos.fillna(0.0)

    def describe(self, p: dict) -> str:
        s = f"Hold long while the trailing {p['lookback']}-bar return is positive"
        if p["long_short"]:
            s += "; short while negative"
        if p["vol_filter"]:
            s += " (only when volatility is below its own rolling median)"
        return s + "."


class TimeOfDay(Strategy):
    """Intraday seasonality: hold during a fixed clock window (UTC hours).
    Returns flat on non-intraday data. Note the engine's one-bar delay shifts
    the realized window by one bar — irrelevant, since start_hour spans all
    offsets and the window is still a fixed, ex-ante clock rule."""

    name = "time_of_day"
    param_space = {
        "start_hour": [0, 3, 6, 9, 12, 15, 18, 21],
        "hold_hours": [2, 4, 6, 8],
        "short": [False, True],
    }

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        idx = df.index
        if len(idx) < 2 or (idx[1:] - idx[:-1]).median() > pd.Timedelta(hours=2):
            return pd.Series(0.0, index=idx)
        hours_in = (idx.hour - p["start_hour"]) % 24
        pos = pd.Series((hours_in < p["hold_hours"]).astype(float), index=idx)
        return -pos if p["short"] else pos

    def describe(self, p: dict) -> str:
        side = "short" if p["short"] else "long"
        return (f"Hold {side} during the {p['hold_hours']}-hour window starting "
                f"{p['start_hour']:02d}:00 UTC, every day.")


class DayOfWeek(Strategy):
    """Weekday seasonality: hold bars falling on one weekday (0=Mon..6=Sun).
    The target is set on the prior bar using the calendar of the next bar —
    calendar knowledge, not price lookahead."""

    name = "day_of_week"
    param_space = {
        "weekday": [0, 1, 2, 3, 4, 5, 6],
        "short": [False, True],
    }

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        wd = pd.Series(df.index.weekday, index=df.index, dtype=float)
        pos = (wd.shift(-1) == p["weekday"]).astype(float)
        return -pos if p["short"] else pos

    def describe(self, p: dict) -> str:
        side = "short" if p["short"] else "long"
        return f"Hold {side} on {_WEEKDAYS[p['weekday']]}s, flat the rest of the week."


class ShockReversal(Strategy):
    """Fade outsized single-bar moves: when a bar's return exceeds entry_z
    trailing standard deviations, take the opposite side for hold_bars."""

    name = "shock_reversal"
    param_space = {
        "vol_n": [20, 60, 120],
        "entry_z": [1.5, 2.0, 2.5, 3.0],
        "hold_bars": [1, 2, 3, 5, 10],
        "short_side": [False, True],  # also fade up-shocks with shorts
    }

    def signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        r = df["close"].pct_change()
        z = r / r.rolling(p["vol_n"]).std()
        hold = lambda ev: ev.astype(float).rolling(p["hold_bars"], min_periods=1).max()
        pos = hold(z < -p["entry_z"])
        if p["short_side"]:
            pos = pos - hold(z > p["entry_z"])
        return pos.fillna(0.0)

    def describe(self, p: dict) -> str:
        s = (f"After a single bar falls {p['entry_z']} trailing standard deviations "
             f"({p['vol_n']}-bar vol), buy and hold {p['hold_bars']} bars")
        return s + ("; fade up-shocks with shorts symmetrically." if p["short_side"] else ".")


def _with_regime(s: Strategy) -> Strategy:
    s.param_space = {**s.param_space, "regime": REGIMES}
    return s


FAMILIES: dict[str, Strategy] = {
    s.name: _with_regime(s)
    for s in (EmaCross(), DonchianBreakout(), ZScoreMeanRev(), RsiMeanRev(), TsMomentum(),
              TimeOfDay(), DayOfWeek(), ShockReversal())
}
