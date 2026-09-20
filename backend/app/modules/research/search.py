"""Random search over market × strategy × params × symbol × timeframe.

Each trial: sample an untested combination, backtest it on the in-sample
segment and the held-out out-of-sample segment, record both to the registry.
Selection happens at report time using OOS results with honesty filters —
the search itself never peeks at OOS to decide what to try next.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import date

import pandas as pd

from . import xs
from .backtest import run_backtest
from .data import load_ohlcv
from .registry import Registry, trial_hash
from .strategies import FAMILIES

log = logging.getLogger(__name__)

MIN_BARS = 500  # skip datasets with too little history
MAX_DUPE_STREAK = 300  # stop early if the param space looks exhausted


def _sample_params(strat, rng: random.Random) -> dict | None:
    for _ in range(50):
        p = {k: rng.choice(v) for k, v in strat.param_space.items()}
        if strat.valid(p):
            return p
    return None


def run_search(cfg: dict, registry: Registry, trials: int | None = None) -> dict:
    rng = random.Random(cfg["search"].get("seed"))
    n_trials = trials if trials is not None else cfg["search"]["trials_per_cycle"]
    deadline = time.monotonic() + cfg["search"]["max_minutes"] * 60
    is_frac = cfg["split"]["is_fraction"]
    today = date.today().isoformat()

    # Preload every dataset once; trials then run entirely in memory.
    datasets: dict[tuple[str, str, str], pd.DataFrame] = {}
    costs: dict[str, tuple[float, float]] = {}
    for market, mcfg in cfg["markets"].items():
        costs[market] = (mcfg["costs"]["fee_bps"], mcfg["costs"]["slippage_bps"])
        for symbol in mcfg["symbols"]:
            for tf in mcfg["timeframes"]:
                df = load_ohlcv(market, mcfg, symbol, tf)
                if df is not None and len(df) >= MIN_BARS:
                    datasets[(market, symbol, tf)] = df
    if not datasets:
        raise RuntimeError("no cached data found — run the data update first")

    # Cross-sectional panels: one pseudo-dataset per (market, timeframe).
    panels: dict[tuple[str, str], pd.DataFrame] = {}
    for market, mcfg in cfg["markets"].items():
        for tf in mcfg["timeframes"]:
            panel = xs.load_panel(market, mcfg, tf)
            if panel is not None:
                panels[(market, tf)] = panel
    log.info("loaded %d datasets + %d XS panels across %d markets",
             len(datasets), len(panels), len(cfg["markets"]))

    keys = list(datasets) + [("__xs__", market, tf) for market, tf in panels]
    fam_names = list(FAMILIES)
    done = skipped = dupes = 0
    dupe_streak = 0

    while done < n_trials and time.monotonic() < deadline and dupe_streak < MAX_DUPE_STREAK:
        key = rng.choice(keys)
        if key[0] == "__xs__":
            _, market, tf = key
            strat_name, symbol = xs.XS_NAME, xs.xs_symbol(market)
            params = {k: rng.choice(v) for k, v in xs.PARAM_SPACE.items()}
        else:
            market, symbol, tf = key
            strat = FAMILIES[rng.choice(fam_names)]
            strat_name = strat.name
            params = _sample_params(strat, rng)
            if params is None:
                skipped += 1
                continue

        h = trial_hash(market, strat_name, symbol, tf, params)
        if registry.seen(h):
            dupes += 1
            dupe_streak += 1
            continue
        dupe_streak = 0

        fee, slip = costs[market]
        try:
            if key[0] == "__xs__":
                panel = panels[(market, tf)]
                w = xs.weights(panel, params)
                split = int(len(panel) * is_frac)
                is_m, _ = xs.xs_backtest(panel.iloc[:split], w.iloc[:split], fee, slip)
                oos_m, _ = xs.xs_backtest(panel.iloc[split:], w.iloc[split:], fee, slip)
            else:
                df = datasets[(market, symbol, tf)]
                split = int(len(df) * is_frac)
                pos = strat.positions(df, params)
                is_m, _ = run_backtest(df["close"].iloc[:split], pos.iloc[:split], fee, slip)
                oos_m, _ = run_backtest(df["close"].iloc[split:], pos.iloc[split:], fee, slip)
        except Exception:
            log.exception("trial failed: %s %s %s %s %s", market, strat_name, symbol, tf, params)
            skipped += 1
            continue

        registry.record(h, today, market, strat_name, symbol, tf, params,
                        is_m.as_dict(), oos_m.as_dict())
        done += 1
        if done % 500 == 0:
            registry.commit()
            log.info("progress: %d/%d trials", done, n_trials)

    registry.commit()
    stats = {"completed": done, "duplicates": dupes, "skipped": skipped,
             "exhausted": dupe_streak >= MAX_DUPE_STREAK}
    log.info("search finished: %s", stats)
    return stats
