"""Backlog 35: day-2 continuation (Crabel 1990; Gao et al. 2018, conditioned on the prior day).

Previous 16:00 -> 16:00 return <= -1% (roll sessions skipped) and the 09:45 price below the 09:30
open -> short at 09:45, stop at the 09:30-09:45 high, exit 15:55. Stop-first; gaps at the open.
Ledger tests 408-409. Also prints the mirror (after a >= +1% day, long) as context, not counted.
"""
import sys

import numpy as np
import pandas as pd

from _common import load, rolls, sessions, summarize

data = sys.argv[1]
M = pd.Timedelta(minutes=1)
for k, sym in enumerate(["NAS100.fs", "S&P.fs"], start=1):
    df = load(data, sym)
    ts, op, hi, lo, cl, sp = (df.index, df["open"].to_numpy(), df["high"].to_numpy(),
                              df["low"].to_numpy(), df["close"].to_numpy(), df["spread"].to_numpy())
    d = sessions(data, sym)
    roll_days = {r.rolled_at.tz_convert("America/New_York").normalize()
                 for r in rolls(data)[lambda x: x.cfd_symbol == sym].itertuples()}
    for label, want in (("35 day-2 short after <=-1%", -1), ("   context: day-2 long after >=+1%", 1)):
        rows = []
        for i in range(1, len(d)):
            day, prev = d[i], d[i - 1]
            if day in roll_days or prev in roll_days:
                continue
            pc = ts.searchsorted(prev + 960 * M)
            pp = ts.searchsorted(d[i - 2] + 960 * M) if i >= 2 else None
            if pp is None or pc >= len(ts) or pp >= len(ts):
                continue
            r_prev = op[pc] / op[pp] - 1
            if (want < 0 and r_prev > -0.01) or (want > 0 and r_prev < 0.01):
                continue
            a = ts.searchsorted(day + 570 * M)
            e = ts.searchsorted(day + 585 * M)
            z = ts.searchsorted(day + 955 * M)
            if z - e < 10 or e - a < 5 or (ts[a] - (day + 570 * M)).total_seconds() > 120:
                continue
            side = want
            if (op[e] - op[a]) * side <= 0:   # 09:45 must be beyond the open in the prior day's direction
                continue
            entry = op[e]
            stop = hi[a:e].max() if side < 0 else lo[a:e].min()
            if (entry - stop) * side <= 0:  # the stop must sit on the losing side of the entry
                continue
            hit = (hi[e:z] >= stop) if side < 0 else (lo[e:z] <= stop)
            if hit.any():
                j = e + int(hit.argmax())
                px = op[j] if j > e and (op[j] - stop) * side < 0 else stop
            else:
                px = cl[z - 1]
            move = side * (px - entry)
            rows.append({"t0": ts[e], "pct": (move - 2 * sp[e]) / entry * 100, "gross_pct": move / entry * 100,
                         "cost_pct": 2 * sp[e] / entry * 100, "swap_pct": 0.0, "usd_per_001": 0.0})
        summarize(label, sym, pd.DataFrame(rows), 407 + k)
