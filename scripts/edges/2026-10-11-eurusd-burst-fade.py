"""eurusd-burst-fade stage 1 (ledger #429 A, #430 B). The frozen spec is in
docs/edges/eurusd-burst-fade.md, *Frozen spec*. Run once.

Signal: z of the M5 close against the last 144 M5 closes. A: sell z >= 1.5, buy z <= -1.5.
B: sell only. Fill at the first M1 open at or after the M5 close (<= 10 min). Stop 25 pips;
once the best excursion reaches 5 pips, exit on a 3-pip giveback; time stop 24 h. Stop checked
first in a bar, then the trail against the best known before the bar. Shorts exit at ask
(bid + spread). The gate figure pays one extra entry spread (doubled cost), plus swap.

    uv run --directory backend python ../scripts/edges/2026-10-11-eurusd-burst-fade.py data/broker-export
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP = 1e-4
PT = 1e-5
Z_IN, LOOKBACK = 1.5, 144
STOP, TRAIL_ON, TRAIL = 25 * PIP, 5 * PIP, 3 * PIP
HOLD = pd.Timedelta(hours=24)
MAX_SPREAD = 15
SWAP_PTS = {1: -6.38, -1: 3.42}  # broker.symbol_specs EURUSD, 2026-10-10, points per lot-night
WINDOWS = [("2019-01-01", "2026-04-27"), ("2026-07-11", "2026-10-06")]
NY = "America/New_York"

df = pd.read_parquet(Path(sys.argv[1]) / "eurusd.parquet").sort_values("ts").reset_index(drop=True)
ts = df["ts"].dt.tz_convert(None).to_numpy().astype("datetime64[ns]")
o, h, l, sp = (df[c].to_numpy(float) for c in ("open", "high", "low", "spread"))
spx = sp * PT

m5 = df.set_index("ts")["close"].resample("5min", label="left", closed="left").last().dropna()
z = (m5 - m5.rolling(LOOKBACK).mean()) / m5.rolling(LOOKBACK).std()
sig_t = (m5.index + pd.Timedelta(minutes=5)).tz_convert(None).to_numpy().astype("datetime64[ns]")  # the bar's close = when z is known
zv = z.to_numpy()
fill_idx = np.searchsorted(ts, sig_t)
in_win = np.zeros(len(sig_t), bool)
for a, b in WINDOWS:
    in_win |= (sig_t >= np.datetime64(a, "ns")) & (sig_t < np.datetime64(b, "ns"))


def rollovers(t0: pd.Timestamp, t1: pd.Timestamp) -> int:
    a, b = t0.tz_convert(NY), t1.tz_convert(NY)
    d = a.normalize() + pd.Timedelta(hours=17)
    if d <= a:
        d += pd.Timedelta(days=1)
    n = 0
    while d <= b:
        if d.weekday() < 5:
            n += 3 if d.weekday() == 2 else 1
        d += pd.Timedelta(days=1)
    return n


def simulate(allow_buy: bool) -> pd.DataFrame:
    rows, busy_until = [], np.datetime64("1970-01-01T00:00:00", "ns")
    for i in range(len(sig_t)):
        zi = zv[i]
        if not in_win[i] or np.isnan(zi):
            continue
        side = -1 if zi >= Z_IN else (1 if (allow_buy and zi <= -Z_IN) else 0)
        if side == 0 or sig_t[i] < busy_until:
            continue
        k = fill_idx[i]
        if k >= len(ts) or (ts[k] - sig_t[i]) > np.timedelta64(10, "m"):
            continue
        if sp[k] > MAX_SPREAD or sp[k] <= 0:
            continue
        t_end = ts[k] + np.timedelta64(int(HOLD.total_seconds()), "s")
        j = np.searchsorted(ts, t_end)  # time-stop bar
        if j >= len(ts):
            break
        seg = slice(k, j)
        if side == -1:
            entry = o[k]
            best = entry - (l[seg] + spx[seg])  # executable P&L at each bar's best/worst
            worst = entry - (h[seg] + spx[seg])
            at_open = entry - (o[seg] + spx[seg])
        else:
            entry = o[k] + spx[k]
            best = h[seg] - entry
            worst = l[seg] - entry
            at_open = o[seg] - entry
        best_before = np.concatenate([[0.0], np.maximum.accumulate(best)[:-1]])
        best_before = np.maximum(best_before, 0.0)
        stop_hit = worst <= -STOP
        trail_lvl = best_before - TRAIL
        trail_hit = (best_before >= TRAIL_ON) & (worst <= trail_lvl)
        first_stop = np.argmax(stop_hit) if stop_hit.any() else None
        first_trail = np.argmax(trail_hit) if trail_hit.any() else None
        cand = [(x, kind) for x, kind in ((first_stop, "stop"), (first_trail, "trail")) if x is not None]
        if cand:
            e, kind = min(cand, key=lambda c: (c[0], c[1] != "stop"))
            if kind == "stop":
                pnl = min(-STOP, at_open[e])
            else:
                pnl = min(trail_lvl[e], at_open[e])
            exit_i = k + e
        else:
            kind, exit_i = "time", j
            pnl = (o[j] - entry) if side == 1 else (entry - (o[j] + spx[j]))
        t0, t1 = pd.Timestamp(ts[k], tz="UTC"), pd.Timestamp(ts[exit_i], tz="UTC")
        swap_pips = rollovers(t0, t1) * SWAP_PTS[side] * PT / PIP
        rows.append({"t0": t0, "t1": t1, "side": side, "z": zi, "kind": kind,
                     "gross": pnl / PIP, "extra_cost": spx[k] / PIP, "swap": swap_pips})
        busy_until = ts[exit_i]
    tr = pd.DataFrame(rows)
    tr["net"] = tr["gross"] - tr["extra_cost"] + tr["swap"]
    return tr


def clustered_t(x: pd.Series, groups: pd.Series) -> float:
    dev = (x - x.mean()).groupby(groups.to_numpy()).sum()
    se = math.sqrt((dev ** 2).sum()) / len(x)
    return x.mean() / se


def report(name: str, tr: pd.DataFrame) -> None:
    day = tr["t0"].dt.tz_convert(NY).dt.date
    x = tr["net"]
    yrs = x.groupby(tr["t0"].dt.year).agg(["size", "mean", "sum"])
    months = tr["t0"].dt.tz_convert(None).dt.to_period("M").nunique()
    win = x > 0
    print(f"\n== {name}: n={len(x)} days={day.nunique()} trades/month={len(x) / months:.1f}")
    print(f"   net (2x cost + swap) mean {x.mean():+.3f} pips, clustered t {clustered_t(x, day):+.2f} | "
          f"gross {tr['gross'].mean():+.3f}, extra spread {tr['extra_cost'].mean():.3f}, swap {tr['swap'].mean():+.3f}")
    print(f"   1x-cost mean {(tr['gross'] + tr['swap']).mean():+.3f} pips | win {win.mean():.1%}, "
          f"avg win {x[win].mean():+.2f}, avg loss {x[~win].mean():+.2f}, worst {x.min():+.1f}")
    print(f"   exits: {tr['kind'].value_counts().to_dict()} | $/month at 0.01 lot: {x.sum() * 0.10 / months:+.2f}")
    print("   years (n, mean, sum pips):")
    for y, r in yrs.iterrows():
        print(f"     {y}: {int(r['size']):5d}  {r['mean']:+.3f}  {r['sum']:+8.1f}")
    print(f"   years positive: {(yrs['sum'] > 0).sum()}/{len(yrs)}")
    for s, g in tr.groupby("side"):
        print(f"   side {s:+d}: n={len(g)} mean {g['net'].mean():+.3f} t {clustered_t(g['net'], g['t0'].dt.tz_convert(NY).dt.date):+.2f}")
    late = tr[tr["t0"] >= pd.Timestamp("2026-07-11", tz="UTC")]
    if len(late):
        print(f"   2026-07-11 to 10-05 only: n={len(late)} mean {late['net'].mean():+.3f}")


A = simulate(allow_buy=True)
B = simulate(allow_buy=False)
report("#429 A: two-sided fade", A)
report("#430 B: short-only control", B)
out = Path(sys.argv[1]) / "eurusd-burst-fade-trades.csv"
pd.concat([A.assign(variant="A"), B.assign(variant="B")]).to_csv(out, index=False)
print(f"\ntrades written to {out}")
