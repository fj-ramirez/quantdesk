"""Walk-forward consistency check.

Parameters are sampled, never re-optimized, so walk-forward reduces to:
evaluate the fixed candidate on K disjoint chronological windows spanning the
full history and demand it made money in most of the windows it actually
traded in. Regime-gated strategies are deliberately flat outside their regime,
so windows with negligible exposure are excluded rather than counted as
failures — but a minimum number of *active* windows is still required, so the
evidence can't come from one lucky stretch.
"""

from __future__ import annotations

import statistics

import numpy as np
import pandas as pd

from .backtest import run_backtest
from .strategies import Strategy

MIN_WINDOW_BARS = 50
MIN_WINDOW_EXPOSURE = 0.01  # below this a window counts as "didn't trade"


def walkforward(df: pd.DataFrame, strat: Strategy, params: dict,
                fee_bps: float, slippage_bps: float,
                n_windows: int) -> tuple[int, int, float]:
    """Returns (positive active windows, active windows, median active Sharpe)."""
    pos = strat.positions(df, params)
    close = df["close"]
    edges = np.linspace(0, len(df), n_windows + 1, dtype=int)
    active = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b - a < MIN_WINDOW_BARS:
            continue
        m, _ = run_backtest(close.iloc[a:b], pos.iloc[a:b], fee_bps, slippage_bps)
        if m.exposure >= MIN_WINDOW_EXPOSURE:
            active.append(m.sharpe)
    if not active:
        return 0, 0, 0.0
    return sum(1 for s in active if s > 0), len(active), statistics.median(active)
