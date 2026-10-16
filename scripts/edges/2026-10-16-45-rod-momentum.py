"""Backlog 45: rest-of-day momentum into the close (Baltussen, Da, Lammers and Martens 2021, JFE).

ROD = return from the previous 16:00 to 15:30. If |ROD| >= 1.0%, trade 15:30 -> 16:00 in ROD's
direction. The 1% floor was declared in hypothesis-backlog.md before this ran. Ledger 536-538.
"""
import sys

import numpy as np
import pandas as pd
from _common import INDICES, at, load, sessions, summarize, trades

data = sys.argv[1]
N0 = 535
for i, sym in enumerate(INDICES, start=1):
    d = sessions(data, sym)
    df = load(data, sym)
    prev_close, _, ok_a = at(df, d[:-1] + pd.Timedelta(hours=16))
    p1530, _, ok_b = at(df, d[1:] + pd.Timedelta(hours=15, minutes=30))
    rod = np.where(ok_a & ok_b, p1530 / prev_close - 1, 0.0)
    side = np.where(np.abs(rod) >= 0.01, np.sign(rod), 0)
    tr = trades(data, sym, d[1:] + pd.Timedelta(hours=15, minutes=30), d[1:] + pd.Timedelta(hours=16), side)
    summarize("45 ROD momentum |ROD|>=1%", sym, tr, N0 + i)
    s = pd.Series(side, index=d[1:] + pd.Timedelta(hours=15, minutes=30))
    by = tr.groupby(s.reindex(pd.DatetimeIndex(tr["t0"])).to_numpy())["pct"].agg(["count", "mean"])
    print("   by side (-1 short, 1 long):", by.round(4).to_dict("index"))
