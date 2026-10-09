"""Backlog 8: gap fade (Plastun et al. 2019).

If the 09:30 open is >= 0.5% away from the prior session's 16:00 price, enter at 09:30 toward it.
Exit at the prior 16:00 price if a later M1 bar touches it (the entry minute never pays),
otherwise at the 10:30 open. No stop. Cost: doubled spread at entry. Ledger tests 318-320.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, SPLIT, at, bar, load, sessions, summarize

data = sys.argv[1]
for i, sym in enumerate(INDICES, start=1):
    df = load(data, sym)
    d = sessions(data, sym)
    prev, _, ok_p = at(df, d[:-1] + pd.Timedelta(hours=16))
    t0 = d[1:] + pd.Timedelta(hours=9, minutes=30)
    t1 = d[1:] + pd.Timedelta(hours=10, minutes=30)
    p0, s0, ok_0 = at(df, t0)
    i0 = df.index.searchsorted(t0)
    i1 = df.index.searchsorted(t1)
    hi, lo, op = df["high"].to_numpy(), df["low"].to_numpy(), df["open"].to_numpy()
    rows = []
    for k in np.flatnonzero(ok_p & ok_0):
        gap = p0[k] / prev[k] - 1
        if abs(gap) < 0.005 or i1[k] >= len(df):
            continue
        side = -np.sign(gap)
        a, b = i0[k] + 1, i1[k]
        touched = (hi[a:b] >= prev[k]) if side > 0 else (lo[a:b] <= prev[k])
        if touched.any():
            j = a + int(touched.argmax())
            exit_px = op[j] if (op[j] - prev[k]) * side > 0 else prev[k]
        else:
            exit_px = op[b]
        move = side * (exit_px - p0[k])
        rows.append({"t0": t0[k], "pct": (move - 2 * s0[k]) / p0[k] * 100,
                     "gross_pct": move / p0[k] * 100, "cost_pct": 2 * s0[k] / p0[k] * 100,
                     "swap_pct": 0.0, "usd_per_001": 0.0})
    summarize("08 gap fade", sym, pd.DataFrame(rows), 317 + i)
