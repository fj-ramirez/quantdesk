"""Stage-1 test of docs/edges/rty-zscore-dip.md, the frozen rule, run once. Ledger 535.

US2000 M1 (Axi cash CFD, no rolls), 2019-07-22 -> 2024-04-26: the span before EdgeLab's RTY=F 1h
history starts (2024-04-28 22:00 UTC), so none of it was searched. Hourly bars on the UTC clock.
Signal: EdgeLab's own `FAMILIES["zscore_meanrev"].positions` at the grid centre (n=40, entry 2.0,
exit 0.5, long only, any regime). Fills at the first M1 open at or after the hourly close.
Disaster stop -4% on M1 lows; a stop never changes when the next entry can happen, because the
rule's own exit must fire before the latch can re-enter. Cost: the entry's recorded spread, doubled.
Swap: today's -7%/yr on the open price, /360, per weekday 17:00 NY crossing, Friday x3.

The full ZScoreMeanRev grid on the same span is printed as context: not gated, nothing picked.

    cd backend && uv run python ../scripts/edges/2026-10-15-rty-zscore-dip.py data/broker-export
"""
import itertools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.modules.research.backtest import run_backtest
from app.modules.research.strategies import FAMILIES, regime_mask

NY = "America/New_York"
START = pd.Timestamp("2019-07-22", tz="UTC")
END = pd.Timestamp("2024-04-27", tz="UTC")
HALF = pd.Timestamp("2022-01-01", tz="UTC")
RULE = {"n": 40, "entry_z": 2.0, "exit_z": 0.5, "long_short": False, "regime": "any"}
STOP = 0.04
SWAP_LONG = -0.07
TRIPLE_WEEKDAY = 4  # Friday (swap_rollover3days = 5 counts Sunday = 0)
USD_PER_POINT = 0.10  # 0.1 lot, the US2000 minimum: contract size 1, tick 0.01 = $0.01

strat = FAMILIES["zscore_meanrev"]

m1 = pd.read_parquet(Path(sys.argv[1]) / "us2000.parquet")
m1 = m1[(m1.ts >= START) & (m1.ts < END)].set_index("ts").sort_index()
m1["spread"] = m1["spread"] * 0.01
h1 = m1.resample("1h", label="left", closed="left").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
print(f"US2000 M1 {m1.index[0]} -> {m1.index[-1]}: {len(m1):,} M1 bars, {len(h1):,} hourly bars")

ts = m1.index
o, lo, sp = m1["open"].to_numpy(), m1["low"].to_numpy(), m1["spread"].to_numpy()


def nights(t0: pd.Timestamp, t1: pd.Timestamp) -> int:
    a, b = t0.tz_convert(NY), t1.tz_convert(NY)
    d = a.normalize() + pd.Timedelta(hours=17)
    if d < a:
        d += pd.Timedelta(days=1)
    n = 0
    while d < b:
        if d.weekday() < 5:
            n += 3 if d.weekday() == TRIPLE_WEEKDAY else 1
        d += pd.Timedelta(days=1)
    return n


def simulate(rule: dict) -> pd.DataFrame:
    pos = strat.positions(h1, rule).to_numpy()
    close_t = h1.index + pd.Timedelta(hours=1)
    vol_hi = regime_mask(h1, "high_vol").to_numpy()
    rows, k = [], 0
    while k < len(pos):
        if pos[k] == 0 or (k > 0 and pos[k - 1] != 0):
            k += 1
            continue
        k_exit = k + 1
        while k_exit < len(pos) and pos[k_exit] != 0:
            k_exit += 1
        i0 = ts.searchsorted(close_t[k])
        i1 = ts.searchsorted(close_t[k_exit]) if k_exit < len(pos) else len(ts)
        if i1 >= len(ts) or i0 >= i1:
            break  # still open at the window's end: dropped
        p0, s0 = o[i0], sp[i0]
        stop = p0 * (1 - STOP)
        hit = np.flatnonzero(lo[i0:i1] <= stop)
        if hit.size:
            j = i0 + hit[0]
            p1, t1, stopped = min(o[j], stop), ts[j], True
        else:
            p1, t1, stopped = o[i1], ts[i1], False
        t0 = ts[i0]
        swap = SWAP_LONG / 360 * p0 * nights(t0, t1)
        net = p1 - p0 - 2 * s0 + swap
        rows.append({"t0": t0, "t1": t1, "p0": p0, "points": net, "pct": net / p0 * 100,
                     "gross_pct": (p1 - p0) / p0 * 100, "cost_pct": 2 * s0 / p0 * 100,
                     "swap_pct": swap / p0 * 100, "hours": (t1 - t0).total_seconds() / 3600,
                     "stopped": stopped, "high_vol": bool(vol_hi[k])})
        k = k_exit
    return pd.DataFrame(rows)


def clustered(x: pd.Series, key: pd.Series) -> tuple[float, float]:
    """Mean and its session-clustered standard error."""
    n = len(x)
    e = (x - x.mean()).groupby(key.to_numpy()).sum()
    g = len(e)
    se = math.sqrt((e ** 2).sum()) / n * math.sqrt(g / (g - 1)) if g > 1 else float("nan")
    return float(x.mean()), se


tr = simulate(RULE)
tr["session"] = (tr["t0"].dt.tz_convert(NY) + pd.Timedelta(hours=6)).dt.date
hours_total = (h1.index[-1] - h1.index[0]).total_seconds() / 3600
drift_h = math.log(h1["close"].iloc[-1] / h1["close"].iloc[0]) / hours_total
tr["drift_pct"] = (np.exp(drift_h * tr["hours"]) - 1) * 100
tr["excess_pct"] = tr["pct"] - tr["drift_pct"]

mean, se = clustered(tr["pct"], tr["session"])
ex_mean, ex_se = clustered(tr["excess_pct"], tr["session"])
t = mean / se
n, n_sess = len(tr), tr["session"].nunique()
yrs = tr.groupby(tr["t0"].dt.year)["pct"].sum()
h1_ = tr[tr["t0"] < HALF]["pct"]
h2_ = tr[tr["t0"] >= HALF]["pct"]
months = (END - START).days / 30.44

print(f"\n== Frozen rule {RULE}, stop -{STOP:.0%} ==")
print(f"trades {n}  sessions {n_sess}  stops {int(tr['stopped'].sum())}  "
      f"median hold {tr['hours'].median():.1f} h  exposure {tr['hours'].sum() / hours_total:.1%}")
print(f"net mean {mean:+.4f}% per trade, clustered SE {se:.4f}, t {t:+.2f} "
      f"(gross {tr['gross_pct'].mean():+.4f}, cost {tr['cost_pct'].mean():.4f}, "
      f"swap {tr['swap_pct'].mean():+.4f})")
print(f"drift-adjusted mean {ex_mean:+.4f}%, t {ex_mean / ex_se:+.2f}")
print(f"halves: 2019-07..2021-12 {h1_.mean():+.4f}% (n {len(h1_)}), "
      f"2022-01..2024-04 {h2_.mean():+.4f}% (n {len(h2_)})")
print("years (sum %):", " ".join(f"{y}:{v:+.2f}" for y, v in yrs.items()))
for flag, g in tr.groupby("high_vol"):
    print(f"  {'high_vol' if flag else 'low_vol '}: n {len(g)}, mean {g['pct'].mean():+.4f}%")
print(f"$ per month at 0.1 lot: {tr['points'].sum() * USD_PER_POINT / months:+.2f}")

gates = {
    "1 >=100 trades, >=30 sessions": n >= 100 and n_sess >= 30,
    "2 mean > 0 net": mean > 0,
    "3 mean > 2 clustered SE": t > 2,
    "4 years + > half, both halves +": (yrs > 0).sum() > len(yrs) / 2 and h1_.mean() > 0 and h2_.mean() > 0,
    "5 beats drift": ex_mean > 0,
}
for k_, v in gates.items():
    print(f"  gate {k_}: {'PASS' if v else 'FAIL'}")
print(f"STAGE 1: {'PASS' if all(gates.values()) else 'FAIL'}   "
      f"(search-log bar t >= {math.sqrt(2 * math.log(535)):.2f}, for comparison only)")

# -- context: the whole grid on the same span, EdgeLab's own engine. Not gated. -------------
cost_side_bps = float((m1["spread"] / m1["close"]).median() * 1e4)
sharpes = []
space = strat.param_space
for combo in itertools.product(*space.values()):
    p = dict(zip(space.keys(), combo))
    if not strat.valid(p):
        continue
    m, _ = run_backtest(h1["close"], strat.positions(h1, p), 0.0, cost_side_bps)
    if m.n_fills >= 20:
        sharpes.append(m.sharpe)
s = pd.Series(sharpes)
print(f"\ncontext (not gated): {len(s)} grid variants with >= 20 fills, one spread per side "
      f"({cost_side_bps:.1f} bps), no swap: mean Sharpe {s.mean():+.2f}, median {s.median():+.2f}, "
      f"{(s > 0).mean():.0%} positive")
