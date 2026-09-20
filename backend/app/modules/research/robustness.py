"""Robustness checks — gates a candidate must pass before paper promotion.

Two cheap, high-signal tests on the OOS segment:

1. **Cost doubling**: rerun with fees and slippage multiplied. An edge that
   only exists at optimistic costs is not an edge.
2. **Parameter neighborhood**: rerun each adjacent parameter value (one grid
   step in either direction, non-boolean params only). A real effect is a
   plateau — neighbors should also make money. A lone spike whose neighbors
   lose is the signature of overfitting to noise.
"""

from __future__ import annotations

import statistics

import pandas as pd

from .backtest import run_backtest
from .strategies import Strategy


def neighbor_params(strat: Strategy, params: dict) -> list[dict]:
    out = []
    for key, val in params.items():
        grid = strat.param_space.get(key, [])
        # Only numeric params have meaningful adjacency; bools and categorical
        # params like the regime are structural choices, not perturbations.
        if isinstance(val, (bool, str)) or len(grid) < 2 or val not in grid:
            continue
        i = grid.index(val)
        for j in (i - 1, i + 1):
            if 0 <= j < len(grid):
                q = {**params, key: grid[j]}
                if strat.valid(q):
                    out.append(q)
    return out


def check(df: pd.DataFrame, strat: Strategy, params: dict, is_frac: float,
          fee_bps: float, slippage_bps: float, cost_mult: float) -> tuple[float, float]:
    """Returns (OOS Sharpe at multiplied costs, median OOS Sharpe of neighbors)."""
    split = int(len(df) * is_frac)
    close_oos = df["close"].iloc[split:]

    pos = strat.positions(df, params)
    m2x, _ = run_backtest(close_oos, pos.iloc[split:],
                          fee_bps * cost_mult, slippage_bps * cost_mult)

    neighbor_sharpes = []
    for q in neighbor_params(strat, params):
        npos = strat.positions(df, q)
        m, _ = run_backtest(close_oos, npos.iloc[split:], fee_bps, slippage_bps)
        neighbor_sharpes.append(m.sharpe)
    med = statistics.median(neighbor_sharpes) if neighbor_sharpes else 0.0
    return m2x.sharpe, med
