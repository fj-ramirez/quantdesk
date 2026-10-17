"""Backlog 56: the 4h desk strategy with exits that keep open profit, plus a random-entry control.

Exits be1 / atr3 / be1atr3 (see _desk4h.py) on the candle stop, and the row-53 pullback exit as
the baseline. Each exit is also run on RANDOM_DRAWS random-entry masks (any 4h close, Bernoulli
rate calibrated to the signal's trade count, same stop and exits). Ledger 560-565; family
4h-ema-macd-trend, k = 18, bar 2.40.

    python 2026-10-16-56-4h-desk-exits.py <data_dir>
Writes <data_dir>/4h-desk-exits-<exit>-<sym>.csv (trades with MAE/MFE) and 4h-desk-exits.json.
"""
import json
import math
import sys

import numpy as np
import pandas as pd
from _common import INDICES
from _desk4h import bars4h, score, signal_mask, simulate

data = sys.argv[1]
BAR = max(2.0, math.sqrt(2 * math.log(18)))
EXITS = ["pullback", "be1", "atr3", "be1atr3"]
RANDOM_DRAWS = 200


def random_runs(sym, exit, n_target):
    n_bars = len(bars4h(data, sym))
    q = n_target / n_bars * 3
    rng = np.random.default_rng(10_000)
    for _ in range(3):  # calibrate the rate to the signal's trade count
        n = len(simulate(data, sym, rng.random(n_bars) < q, exit))
        q *= n_target / max(n, 1)
    out = []
    for seed in range(RANDOM_DRAWS):
        tr = simulate(data, sym, np.random.default_rng(seed).random(n_bars) < q, exit)
        out.append((len(tr), tr.pct.mean(), tr.pct.sum()))
    return np.array(out)


summary = {}
for sym in INDICES + ["XAUUSD"]:
    sig = signal_mask(data, sym)
    for exit in EXITS:
        tr = simulate(data, sym, sig, exit, excursions=True)
        tr.to_csv(f"{data}/4h-desk-exits-{exit}-{sym.replace('&', '-').lower()}.csv", index=False)
        res = score(data, sym, tr, BAR)
        rnd = random_runs(sym, exit, len(tr))
        res["rand_trades_median"] = float(np.median(rnd[:, 0]))
        res["rand_mean_pct_p50"], res["rand_mean_pct_p95"] = (float(np.percentile(rnd[:, 1], p)) for p in (50, 95))
        res["rand_total_p50"], res["rand_total_p95"] = (float(np.percentile(rnd[:, 2], p)) for p in (50, 95))
        res["signal_pctile"] = float((rnd[:, 1] < res["mean_pct"]).mean() * 100)
        res["beats_random"] = res["mean_pct"] > res["rand_mean_pct_p95"]
        res["verdict"] = "pass" if res["gates"] == "pass" and res["beats_random"] else (
            "lead" if res["gates"] in ("pass", "lead") else "fail")
        mae_r, mfe_r = tr.mae / tr.risk, tr.mfe / tr.risk
        res["mfe_R_mean"], res["mae_R_median"] = float(mfe_r[tr.risk > 0].mean()), float(mae_r[tr.risk > 0].median())
        res["kept_of_mfe"] = float(tr.gross.sum() / tr.mfe.sum())
        summary[f"{sym}|{exit}"] = res
        print(f"{sym:9s} {exit:8s} n={res['trades']:4d} win {res['win']:.0%} R {res['mean_R']:+.3f}±{res['se_R']:.3f} "
              f"| {res['mean_pct']:+.4f}%/tr (random p50 {res['rand_mean_pct_p50']:+.4f} p95 {res['rand_mean_pct_p95']:+.4f}, "
              f"signal at p{res['signal_pctile']:.0f}) | daily t {res['t']:+.2f} IS {res['is_mean']:+.4f} OOS {res['oos_mean']:+.4f} "
              f"yrs {res['years_pos']}/{res['years_n']} | Sharpe {res['sharpe']:.2f} vs {res['bh_sharpe']:.2f} | "
              f"total {res['total']:+.1f}% (random p50 {res['rand_total_p50']:+.1f}, p95 {res['rand_total_p95']:+.1f}) "
              f"vs B&H {res['bh_total']:+.1f}% | ${res['usd']:+,.0f} @0.01 | kept {res['kept_of_mfe']:.0%} of MFE "
              f"| exits {res['exits']} -> {res['verdict']}", flush=True)

with open(f"{data}/4h-desk-exits.json", "w") as f:
    json.dump(summary, f, indent=1, default=str)
for exit in EXITS[1:]:
    a = all(summary[f"{s}|{exit}"]["verdict"] == "pass" for s in INDICES)
    print(f"{exit}: cell A {'pass' if a else 'fail'}; cell B {summary[f'XAUUSD|{exit}']['verdict']}")
