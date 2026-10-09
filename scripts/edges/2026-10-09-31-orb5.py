"""Backlog 31: 5-minute opening-range breakout (Zarattini and Aziz 2023). Ledger tests 400-402.

09:30-09:35 candle sets the side (doji skipped); enter at the 09:35 open; stop at the candle's
opposite extreme; target 10R; exit at 15:55. Stop-first; entry bar never pays; gaps at the open.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, load, sessions, summarize

data = sys.argv[1]
M = pd.Timedelta(minutes=1)
for i, sym in enumerate(INDICES, start=1):
    df = load(data, sym)
    ts, op, hi, lo, cl, sp = (df.index, df["open"].to_numpy(), df["high"].to_numpy(),
                              df["low"].to_numpy(), df["close"].to_numpy(), df["spread"].to_numpy())
    rows = []
    for day in sessions(data, sym):
        a = ts.searchsorted(day + 570 * M)  # 09:30
        e = ts.searchsorted(day + 575 * M)  # 09:35
        z = ts.searchsorted(day + 955 * M)  # 15:55
        if e - a < 3 or z - e < 10 or (ts[a] - (day + 570 * M)).total_seconds() > 120:
            continue
        o, c = op[a], cl[e - 1]
        if c == o:
            continue
        side = 1 if c > o else -1
        H, L = hi[a:e].max(), lo[a:e].min()
        entry = op[e]
        stop = L if side > 0 else H
        risk = (entry - stop) * side
        if risk <= 0:
            continue
        target = entry + side * 10 * risk
        s_hit = (lo[e:z] <= stop) if side > 0 else (hi[e:z] >= stop)
        t_hit = ((hi[e:z] >= target) if side > 0 else (lo[e:z] <= target)).copy()
        t_hit[0] = False
        si = int(s_hit.argmax()) if s_hit.any() else None
        ti = int(t_hit.argmax()) if t_hit.any() else None
        if si is not None and (ti is None or si <= ti):
            k = e + si
            px = op[k] if si > 0 and (op[k] - stop) * side < 0 else stop
        elif ti is not None:
            k = e + ti
            px = op[k] if (op[k] - target) * side > 0 else target
        else:
            px = cl[z - 1]
        move = side * (px - entry)
        rows.append({"t0": ts[e], "pct": (move - 2 * sp[e]) / entry * 100, "gross_pct": move / entry * 100,
                     "cost_pct": 2 * sp[e] / entry * 100, "swap_pct": 0.0, "usd_per_001": 0.0})
    summarize("31 ORB 5-min 10R", sym, pd.DataFrame(rows), 399 + i)
