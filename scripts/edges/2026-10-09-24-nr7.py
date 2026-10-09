"""Backlog 24: NR7 breakout (Crabel 1990). Ledger tests 381-383.

After a day whose RTH range is the narrowest of the last 7, trade the next RTH day: first M1
bar trading above that day's high -> long at max(high, bar open); below its low -> short.
Stop at the opposite side of the NR7 day; exit at 15:55. Stop-first on an ambiguous bar; the
entry bar can stop but not pay (no target anyway). Cost: doubled spread at the entry bar.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, load, rth_daily, summarize

data = sys.argv[1]
for i, sym in enumerate(INDICES, start=1):
    b = rth_daily(data, sym)
    rng = b["high"] - b["low"]
    nr7 = (rng == rng.rolling(7).min()).to_numpy()
    df = load(data, sym)
    ts, op, hi, lo, cl, sp = (df.index, df["open"].to_numpy(), df["high"].to_numpy(),
                              df["low"].to_numpy(), df["close"].to_numpy(), df["spread"].to_numpy())
    rows = []
    for k in np.flatnonzero(nr7[:-1]):
        H, L = b["high"].iloc[k], b["low"].iloc[k]
        day = b.index[k + 1]
        a = ts.searchsorted(day + pd.Timedelta(hours=9, minutes=30))
        z = ts.searchsorted(day + pd.Timedelta(hours=15, minutes=55))
        if z - a < 10:
            continue
        up, dn = np.flatnonzero(hi[a:z] > H), np.flatnonzero(lo[a:z] < L)
        if not len(up) and not len(dn):
            continue
        if len(up) and (not len(dn) or up[0] < dn[0]):
            j, side, entry, stop = a + up[0], 1, max(H, op[a + up[0]]), L
        elif len(dn) and len(up) and up[0] == dn[0]:
            continue  # both sides in one minute: ambiguous, no trade
        else:
            j, side, entry, stop = a + dn[0], -1, min(L, op[a + dn[0]]), H
        seg_hit = (lo[j:z] <= stop) if side > 0 else (hi[j:z] >= stop)
        if seg_hit.any():
            e = j + int(seg_hit.argmax())
            px = op[e] if (op[e] - stop) * side < 0 and e > j else stop
        else:
            px = cl[z - 1]
        move = side * (px - entry)
        rows.append({"t0": ts[j], "pct": (move - 2 * sp[j]) / entry * 100, "gross_pct": move / entry * 100,
                     "cost_pct": 2 * sp[j] / entry * 100, "swap_pct": 0.0, "usd_per_001": 0.0})
    summarize("24 NR7 breakout", sym, pd.DataFrame(rows), 380 + i)
