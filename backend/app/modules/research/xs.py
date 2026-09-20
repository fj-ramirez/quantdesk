"""Cross-sectional momentum: rank a market's universe by trailing return,
hold the top-k equal-weight (optionally short the bottom-k), rebalance every
N bars. A structurally different edge from per-instrument rules — it bets on
relative strength, not direction.

Trials appear in the registry with symbol "XS:<market>". Caveats: the universe
is today's constituents (survivorship bias — treat results as shortlist-grade),
and vol-target sizing is not applied to XS candidates (weights are already
diversified and capped at gross exposure 1).
"""

from __future__ import annotations

import statistics

import numpy as np
import pandas as pd

from .backtest import Metrics, metrics_from_net
from .data import load_ohlcv
from .validation import MIN_WINDOW_BARS, MIN_WINDOW_EXPOSURE

MIN_SYMBOLS = 5
MIN_PANEL_BARS = 500
MIN_SYMBOL_BARS = 300

XS_NAME = "xs_momentum"
PARAM_SPACE: dict[str, list] = {
    "lookback": [20, 60, 120, 250],
    "top_k": [1, 2, 3],
    "rebalance": [5, 10, 20, 40],
    "long_short": [False, True],
}


def describe(market: str, p: dict) -> str:
    s = (f"Every {p['rebalance']} bars, rank the {market} universe by trailing "
         f"{p['lookback']}-bar return and hold the top {p['top_k']} equal-weight")
    return s + ("; short the bottom equally." if p["long_short"] else ".")


def xs_symbol(market: str) -> str:
    return f"XS:{market}"


def is_xs(symbol: str) -> bool:
    return symbol.startswith("XS:")


def load_panel(market: str, mcfg: dict, timeframe: str) -> pd.DataFrame | None:
    closes = {}
    for symbol in mcfg["symbols"]:
        df = load_ohlcv(market, mcfg, symbol, timeframe)
        if df is not None and len(df) >= MIN_SYMBOL_BARS:
            closes[symbol] = df["close"]
    if len(closes) < MIN_SYMBOLS:
        return None
    panel = pd.DataFrame(closes).sort_index()
    return panel if len(panel) >= MIN_PANEL_BARS else None


def weights(panel: pd.DataFrame, p: dict) -> pd.DataFrame:
    mom = panel.pct_change(p["lookback"])
    ranks_hi = mom.rank(axis=1, ascending=False)
    w = (ranks_hi <= p["top_k"]).astype(float)
    if p["long_short"]:
        ranks_lo = mom.rank(axis=1, ascending=True)
        w = (w - (ranks_lo <= p["top_k"]).astype(float)) / (2 * p["top_k"])
    else:
        w = w / p["top_k"]
    w[mom.isna()] = 0.0
    # Stay flat when too few symbols have enough history to rank meaningfully.
    thin = mom.notna().sum(axis=1) < max(MIN_SYMBOLS, 2 * p["top_k"])
    w.loc[thin] = 0.0
    # Hold weights between rebalances.
    keep = np.arange(len(panel)) % p["rebalance"] == 0
    w.loc[~keep] = np.nan
    return w.ffill().fillna(0.0)


def xs_backtest(panel: pd.DataFrame, w: pd.DataFrame,
                fee_bps: float, slippage_bps: float) -> tuple[Metrics, pd.Series]:
    """Portfolio backtest: same one-bar delay and cost model as the engine."""
    wl = w.shift(1).fillna(0.0)
    rets = panel.pct_change().fillna(0.0)
    turnover = wl.diff().abs().sum(axis=1)
    turnover.iloc[0] = wl.iloc[0].abs().sum()
    net = (wl * rets).sum(axis=1) - turnover * (fee_bps + slippage_bps) / 10_000.0

    n_fills = int((turnover > 1e-12).sum())
    exposure = float((wl.abs().sum(axis=1) > 1e-12).mean())
    return metrics_from_net(net, n_fills, exposure), net


def oos_net(panel: pd.DataFrame, params: dict, is_frac: float,
            fee_bps: float, slippage_bps: float) -> pd.Series:
    w = weights(panel, params)
    split = int(len(panel) * is_frac)
    _, net = xs_backtest(panel.iloc[split:], w.iloc[split:], fee_bps, slippage_bps)
    return net


def gates(panel: pd.DataFrame, params: dict, is_frac: float, fee_bps: float,
          slippage_bps: float, cost_mult: float,
          n_windows: int) -> tuple[float, float, int, int, float]:
    """XS versions of the promotion gates.
    Returns (sharpe_2x, neighbor_med, wf_pos, wf_active, wf_med)."""
    w = weights(panel, params)
    split = int(len(panel) * is_frac)

    m2x, _ = xs_backtest(panel.iloc[split:], w.iloc[split:],
                         fee_bps * cost_mult, slippage_bps * cost_mult)

    neighbor_sharpes = []
    for key, val in params.items():
        grid = PARAM_SPACE.get(key, [])
        if isinstance(val, (bool, str)) or len(grid) < 2 or val not in grid:
            continue
        i = grid.index(val)
        for j in (i - 1, i + 1):
            if 0 <= j < len(grid):
                q = {**params, key: grid[j]}
                nw = weights(panel, q)
                m, _ = xs_backtest(panel.iloc[split:], nw.iloc[split:],
                                   fee_bps, slippage_bps)
                neighbor_sharpes.append(m.sharpe)
    neighbor_med = statistics.median(neighbor_sharpes) if neighbor_sharpes else 0.0

    edges = np.linspace(0, len(panel), n_windows + 1, dtype=int)
    active = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b - a < MIN_WINDOW_BARS:
            continue
        m, _ = xs_backtest(panel.iloc[a:b], w.iloc[a:b], fee_bps, slippage_bps)
        if m.exposure >= MIN_WINDOW_EXPOSURE:
            active.append(m.sharpe)
    if active:
        wf_pos, wf_active, wf_med = (sum(1 for s in active if s > 0),
                                     len(active), statistics.median(active))
    else:
        wf_pos = wf_active = 0
        wf_med = 0.0
    return m2x.sharpe, neighbor_med, wf_pos, wf_active, wf_med
