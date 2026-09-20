"""Vectorized backtest engine.

Convention: a strategy emits the *target position* (-1, 0, +1) decided at each
bar's close. The engine shifts positions by one bar before applying returns,
so fills happen on the next bar — no lookahead by construction.

Annualization is derived from the series' own index (bars per calendar year),
so 24/7 crypto, 24/5 forex, and 6.5h-day stocks are all handled correctly
without per-market constants.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

SECONDS_PER_YEAR = 31_557_600  # Julian year


@dataclass
class Metrics:
    sharpe: float
    cagr: float
    max_drawdown: float
    n_fills: int
    exposure: float
    n_bars: int
    years: float

    def as_dict(self) -> dict:
        return {
            "sharpe": self.sharpe,
            "cagr": self.cagr,
            "max_drawdown": self.max_drawdown,
            "n_fills": self.n_fills,
            "exposure": self.exposure,
            "n_bars": self.n_bars,
            "years": self.years,
        }


def metrics_from_net(net: pd.Series, n_fills: int, exposure: float) -> Metrics:
    """Performance metrics from a net-return series (any position structure)."""
    equity = (1.0 + net).cumprod()
    peak = equity.cummax()
    max_dd = float((equity / peak - 1.0).min()) if len(equity) else 0.0

    span = (net.index[-1] - net.index[0]).total_seconds() if len(net) > 1 else 0.0
    years = span / SECONDS_PER_YEAR
    bars_per_year = len(net) / years if years > 0 else 0.0

    std = float(net.std())
    sharpe = (
        float(net.mean()) / std * math.sqrt(bars_per_year) if std > 0 and bars_per_year > 0 else 0.0
    )

    final = float(equity.iloc[-1]) if len(equity) else 1.0
    cagr = final ** (1.0 / years) - 1.0 if years > 0 and final > 0 else -1.0

    return Metrics(sharpe, cagr, max_dd, n_fills, exposure, len(net), years)


def run_backtest(
    close: pd.Series,
    target_pos: pd.Series,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[Metrics, pd.Series]:
    """Returns (metrics, net-return series)."""
    pos = target_pos.shift(1).fillna(0.0)
    rets = close.pct_change().fillna(0.0)

    turnover = pos.diff().abs()
    turnover.iloc[0] = abs(pos.iloc[0])
    cost_rate = (fee_bps + slippage_bps) / 10_000.0
    net = pos * rets - turnover * cost_rate

    n_fills = int((turnover > 1e-12).sum())
    exposure = float((pos.abs() > 1e-12).mean())

    return metrics_from_net(net, n_fills, exposure), net


def vol_targeted(close: pd.Series, target_pos: pd.Series, target_annual: float,
                 vol_n: int, max_leverage: float) -> pd.Series:
    """Scale target positions so realized risk approximates a constant annual
    volatility. Uses trailing realized vol only (ex-ante); leverage is capped,
    so quiet markets are sized up to the cap and violent ones sized down.
    NaN warmup (first vol_n bars) sizes to zero."""
    if len(close) < 2:
        return target_pos * 0.0
    years = (close.index[-1] - close.index[0]).total_seconds() / SECONDS_PER_YEAR
    bars_per_year = len(close) / years if years > 0 else 0.0
    if bars_per_year <= 0:
        return target_pos * 0.0
    vol = close.pct_change().rolling(vol_n).std() * math.sqrt(bars_per_year)
    scale = (target_annual / vol).clip(upper=max_leverage)
    return (target_pos * scale).fillna(0.0)


def noise_ceiling(n_trials: int, oos_years: float) -> float:
    """Expected best annualized OOS Sharpe from pure noise across n_trials.

    The standard error of an annualized Sharpe estimated over T years is
    ~1/sqrt(T) (for SR near 0), independent of bar frequency; the expected max
    of N standard normals is ~sqrt(2 ln N). Candidates below this line are
    indistinguishable from luck.
    """
    if n_trials < 1 or oos_years <= 0:
        return 0.0
    return math.sqrt(2.0 * math.log(max(n_trials, 2))) / math.sqrt(oos_years)
