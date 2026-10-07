"""Live price signals for demo forward-testing (T135, plans/signal-alerts/).

Two families, each named exactly like its NinjaTrader backtest class in
`meta-trader-strategies/strategies/ninjatrader/edgelab/`, so an alert maps to the file that
backtested it:

* ``ZScoreDip_N{n}_E{entry}_X{exit}_{Regime}``: EdgeLab's long-only ``zscore_meanrev`` over a
  pre-registered grid, on hourly futures. The positions come from
  `research.strategies.FAMILIES["zscore_meanrev"].positions` -- the code EdgeLab scores --
  never from a second implementation, so the forward test tests what was backtested.
* ``ContinuationProxy_{Short,Long}``: the decision engine's CONTINUATION setup without its
  gamma condition, on daily bars. Short when the 5-session return is negative (long when
  positive), entry at the next open, stop 1x ATR and target 2x ATR from the signal close,
  10-session hold. The trade itself is scored by `gex.scan.outcomes.evaluate`, so a proxy exit
  follows exactly the rules the desk's own track record uses (T115's fill-bar rule included).

**Pure**: frames in, events out. No database, network, clock or logging.

**Every run re-reads a window, not one bar.** A worker that was down for two hours must not
lose the transitions it slept through, so each evaluation reports every transition in the last
`window` bars. The store's key makes the repeats harmless, and `late` marks an event whose bar
is not the newest one -- recorded as evidence, not worth an alert.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.modules.gex.scan.indicators import atr as daily_atr
from app.modules.gex.scan.outcomes import TradeSpec, evaluate
from app.modules.research.strategies import FAMILIES, regime_mask

__all__ = [
    "CONTINUATION_SIDES",
    "FUTURES_SYMBOLS",
    "SignalEvent",
    "continuation_events",
    "zscore_events",
    "zscore_variants",
]

# --- The pre-registered grid. Must match meta-trader-strategies' generator exactly. -----------
LOOKBACKS = (80, 120, 160, 240)
ENTRY_ZS = (1.0, 1.5, 2.0, 2.5, 3.0)
EXIT_ZS = (0.0, 0.5)
REGIMES = ("any", "high_vol", "trend_down")
_REGIME_LABEL = {"any": "Any", "high_vol": "HighVol", "trend_down": "TrendDown"}

#: Yahoo's continuous front-month tickers, research market "futures", 1h.
FUTURES_SYMBOLS = ("ES=F", "NQ=F", "YM=F", "RTY=F")

# --- The continuation proxy: the decision engine's fallbacks (gex/scan/decisions.py). ---------
RETURN_DAYS = 5
STOP_ATR = 1.0      # VOLATILITY_STOP_ATR
TARGET_ATR = 2.0    # TARGET_FALLBACK_ATR
CONTINUATION_SIDES = ("SHORT", "LONG")
#: Bars simulated before the window. Trades last at most 10 sessions, so the state at the
#: window's start no longer depends on where the simulation began.
SIMULATION_BARS = 250

#: Bars re-examined each run, so a missed run loses nothing.
DEFAULT_WINDOW = 24


@dataclass(frozen=True, slots=True)
class SignalEvent:
    """One transition of one signal on one symbol -- a row of `research.signal_events`.

    `bar_ts` is the bar the decision was taken on (the timestamp the source gives that bar).
    For an ENTER the order goes in at the *next* bar's open; `price` is the signal close, the
    reference the stop and target were computed from.
    """

    signal: str
    family: str
    symbol: str
    timeframe: str
    bar_ts: dt.datetime
    action: str          # ENTER | EXIT
    side: str            # LONG | SHORT
    price: float
    reason: str
    late: bool
    params: dict[str, Any] = field(default_factory=dict)
    stop: float | None = None
    target: float | None = None

    @property
    def key(self) -> tuple[str, str, dt.datetime, str]:
        return (self.signal, self.symbol, self.bar_ts, self.action)


def _num(x: float) -> str:
    return f"{x:g}".replace(".", "p")


def zscore_variants() -> list[tuple[str, dict[str, Any]]]:
    """(name, EdgeLab params) for the whole grid, in a stable order -- 120 entries."""
    out = []
    for n, entry, exit_, regime in itertools.product(LOOKBACKS, ENTRY_ZS, EXIT_ZS, REGIMES):
        name = f"ZScoreDip_N{n}_E{_num(entry)}_X{_num(exit_)}_{_REGIME_LABEL[regime]}"
        out.append((name, {"n": n, "entry_z": entry, "exit_z": exit_, "long_short": False, "regime": regime}))
    return out


def _ts(value: Any) -> dt.datetime:
    ts = pd.Timestamp(value)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.to_pydatetime()


def zscore_events(
    df: pd.DataFrame, symbol: str, name: str, params: dict[str, Any], *, window: int = DEFAULT_WINDOW
) -> list[SignalEvent]:
    """Transitions of one z-score variant in the last `window` bars of `df` (1h OHLCV,
    ascending, the whole cached history so the latch and the regime warm up exactly as in the
    backtest)."""
    if len(df) < 2:
        return []
    strat = FAMILIES["zscore_meanrev"]
    pos = strat.positions(df, params)
    close = df["close"]
    n = params["n"]
    z = (close - close.rolling(n).mean()) / close.rolling(n).std()
    mask = regime_mask(df, params["regime"])
    last = len(df) - 1
    events = []
    for i in range(max(1, len(df) - window), len(df)):
        before, now = pos.iloc[i - 1], pos.iloc[i]
        if before == now:
            continue
        zi = z.iloc[i]
        regime_on = True if mask is None else bool(mask.iloc[i])
        if now > 0:
            action = "ENTER"
            reason = (
                f"z {zi:.2f} < -{params['entry_z']:g}" if zi < -params["entry_z"]
                else f"regime {params['regime']} back on with the signal still set (z {zi:.2f})"
            )
        else:
            action = "EXIT"
            reason = (
                f"regime {params['regime']} off (z {zi:.2f})" if not regime_on
                else f"z {zi:.2f} > -{params['exit_z']:g}"
            )
        events.append(SignalEvent(
            signal=name, family="zscore_meanrev", symbol=symbol, timeframe="1h",
            bar_ts=_ts(df.index[i]), action=action, side="LONG", price=float(close.iloc[i]),
            reason=reason, late=i != last, params=dict(params),
        ))
    return events


def continuation_events(
    bars: pd.DataFrame, symbol: str, side: str, *, window: int = DEFAULT_WINDOW
) -> list[SignalEvent]:
    """Transitions of `ContinuationProxy_{side}` on `symbol`'s daily bars (`date, open, high,
    low, close`, ascending) in the last `window` sessions."""
    name = f"ContinuationProxy_{'Short' if side == 'SHORT' else 'Long'}"
    params = {"return_days": RETURN_DAYS, "stop_atr": STOP_ATR, "target_atr": TARGET_ATR}
    if len(bars) <= RETURN_DAYS + 1:
        return []
    bars = bars.reset_index(drop=True)
    close = bars["close"].astype(float)
    ret = close / close.shift(RETURN_DAYS) - 1.0
    atr_s = daily_atr(bars)
    dates = list(bars["date"])
    last = len(bars) - 1
    first_reported = max(0, len(bars) - window)
    sign = -1 if side == "SHORT" else 1

    def ts(i: int) -> dt.datetime:
        return dt.datetime.combine(dates[i], dt.time(), tzinfo=dt.UTC)

    events: list[SignalEvent] = []
    i = max(0, len(bars) - window - SIMULATION_BARS)
    while i <= last:
        a, r = float(atr_s.iloc[i]), float(ret.iloc[i])
        if not (math.isfinite(a) and a > 0 and math.isfinite(r) and r * sign > 0):
            i += 1
            continue
        entry = float(close.iloc[i])
        spec = TradeSpec(
            setup="continuation", side=side, entry=entry,
            stop=entry - sign * STOP_ATR * a, target=entry + sign * TARGET_ATR * a,
        )
        if i >= first_reported:
            events.append(SignalEvent(
                signal=name, family="continuation_proxy", symbol=symbol, timeframe="1d",
                bar_ts=ts(i), action="ENTER", side=side, price=entry,
                reason=f"{RETURN_DAYS}-session return {r:+.2%}; enter at the next open",
                late=i != last, params=params, stop=spec.stop, target=spec.target,
            ))
        out = evaluate(spec, bars.iloc[i + 1:])
        if not out.resolved:
            break  # still open (or not yet filled): no further entries while it is
        j = dates.index(out.resolved_on)
        if j >= first_reported:
            risk = abs(spec.entry - spec.stop)
            exit_price = float(out.fill + out.result_r * risk * sign)
            events.append(SignalEvent(
                signal=name, family="continuation_proxy", symbol=symbol, timeframe="1d",
                bar_ts=ts(j), action="EXIT", side=side, price=exit_price,
                reason=f"{out.outcome} ({out.result_r:+.2f}R)", late=j != last, params=params,
                stop=spec.stop, target=spec.target,
            ))
        i = j  # flat again at that session's close, which may itself signal
    return events
