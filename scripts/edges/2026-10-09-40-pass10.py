"""Pass 10, backlog 40-43, declared before running. Ledger tests 418-426.

40 intraday->overnight reversal (Branch and Ma 2012) x3; 41 Double 7s (Connors) x2 (position rule);
42 small-gap fill 0.1-0.3% (Plastun et al. 2019) x3; 43 gold Monday NY-session short x1.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, at, closes_for, eval_positions, load, sessions, summarize, trades

data = sys.argv[1]
H = pd.Timedelta(hours=1)
n = 417

for sym in INDICES:  # 40
    n += 1
    df, d = load(data, sym), sessions(data, sym)
    o, _, ok_o = at(df, d[:-1] + 9.5 * H)
    c, _, ok_c = at(df, d[:-1] + 16 * H)
    side = np.where(ok_o & ok_c, -np.sign(c - o), 0)
    summarize("40 intraday->overnight reversal", sym, trades(data, sym, d[:-1] + 16 * H, d[1:] + 9.5 * H, side), n)

for sym in ["S&P.fs", "NAS100.fs"]:  # 41
    n += 1
    cl = closes_for(data, sym)
    px = cl["close"]
    entry = ((px <= px.rolling(7).min()) & (px > px.rolling(200).mean())).to_numpy()
    exit_ = (px >= px.rolling(7).max()).to_numpy()
    pos, held = np.zeros(len(px)), 0.0
    for i in range(len(px)):
        if held and exit_[i]:
            held = 0.0
        elif not held and entry[i]:
            held = 1.0
        pos[i] = held
    eval_positions("41 Double 7s", sym, cl, pd.Series(pos, index=px.index), n)

for sym in INDICES:  # 42
    n += 1
    df, d = load(data, sym), sessions(data, sym)
    ts, op, hi, lo, sp = df.index, df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["spread"].to_numpy()
    prev, _, ok_p = at(df, d[:-1] + 16 * H)
    rows = []
    for k in np.flatnonzero(ok_p):
        day = d[k + 1]
        a, z = ts.searchsorted(day + 9.5 * H), ts.searchsorted(day + 12 * H)
        if a >= len(ts) or z >= len(ts) or (ts[a] - (day + 9.5 * H)).total_seconds() > 120:
            continue
        gap = op[a] / prev[k] - 1
        if not (0.001 <= abs(gap) <= 0.003):
            continue
        side = -np.sign(gap)
        hit = (hi[a + 1:z] >= prev[k]) if side > 0 else (lo[a + 1:z] <= prev[k])
        if hit.any():
            j = a + 1 + int(hit.argmax())
            px = op[j] if (op[j] - prev[k]) * side > 0 else prev[k]
        else:
            px = op[z]
        move = side * (px - op[a])
        rows.append({"t0": ts[a], "pct": (move - 2 * sp[a]) / op[a] * 100, "gross_pct": move / op[a] * 100,
                     "cost_pct": 2 * sp[a] / op[a] * 100, "swap_pct": 0.0, "usd_per_001": 0.0})
    summarize("42 small-gap fill", sym, pd.DataFrame(rows), n)

n += 1  # 43
d = sessions(data, "XAUUSD")
mon = d[d.weekday == 0]
summarize("43 gold Monday NY short", "XAUUSD",
          trades(data, "XAUUSD", mon + pd.Timedelta(hours=8, minutes=20), mon + 16 * H, -1), n)
