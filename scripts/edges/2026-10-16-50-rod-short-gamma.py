"""Backlog 50: rest-of-day momentum only when dealers are short gamma (Baltussen, Da, Lammers and
Martens 2021, JFE, Table 7).

On session t with SqueezeMetrics `gex` for the previous session < 0: ROD = previous 16:00 -> 15:30,
trade 15:30 -> 16:00 in ROD's direction, no |ROD| floor. Declared in hypothesis-backlog.md pass 13
(commit 1cde5c8) before this ran. Ledger 545-546.

Usage: python 2026-10-16-50-rod-short-gamma.py <m1 export dir> <DIX.csv>
"""
import sys

import numpy as np
import pandas as pd
from _common import at, load, sessions, summarize, trades

data, dix = sys.argv[1], sys.argv[2]
N0 = 544

g = pd.read_csv(dix, parse_dates=["date"])[["date", "gex"]].sort_values("date")
for i, sym in enumerate(["S&P.fs", "NAS100.fs"], start=1):
    d = sessions(data, sym)
    df = load(data, sym)
    prev_close, _, ok_a = at(df, d[:-1] + pd.Timedelta(hours=16))
    p1530, _, ok_b = at(df, d[1:] + pd.Timedelta(hours=15, minutes=30))
    rod = np.where(ok_a & ok_b, p1530 / prev_close - 1, 0.0)
    # gamma known before the session: the last SqueezeMetrics row strictly before t
    s = pd.DataFrame({"date": d[1:].tz_localize(None)})
    gex = pd.merge_asof(s, g, on="date", allow_exact_matches=False)["gex"].to_numpy()
    t0, t1 = d[1:] + pd.Timedelta(hours=15, minutes=30), d[1:] + pd.Timedelta(hours=16)
    short_gamma = gex < 0
    tr = trades(data, sym, t0, t1, np.where(short_gamma, np.sign(rod), 0))
    summarize("50 ROD momentum, gex(t-1) < 0", sym, tr, N0 + i)
    ctx = trades(data, sym, t0, t1, np.where(gex >= 0, np.sign(rod), 0))
    summarize("   context: gex(t-1) >= 0", sym, ctx, N0 + i)
    print(f"   sessions {len(d) - 1}, gex(t-1) < 0 on {int(short_gamma.sum())}, unknown {int(np.isnan(gex).sum())}")
