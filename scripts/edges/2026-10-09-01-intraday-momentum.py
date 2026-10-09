"""Backlog 1: intraday momentum (Gao, Han, Li, Zhou 2018, JFE).

The first half-hour return (prior 16:00 close -> 10:00) predicts the last half hour. Trade
15:30 -> 16:00 in its direction. Ledger tests 307-309, one per index CFD.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, at, load, sessions, summarize, trades

data = sys.argv[1]
N0 = 306
for i, sym in enumerate(INDICES, start=1):
    d = sessions(data, sym)
    df = load(data, sym)
    prev_close, _, ok_a = at(df, d[:-1] + pd.Timedelta(hours=16))
    ten, _, ok_b = at(df, d[1:] + pd.Timedelta(hours=10))
    side = np.where(ok_a & ok_b, np.sign(ten - prev_close), 0)
    tr = trades(data, sym, d[1:] + pd.Timedelta(hours=15, minutes=30), d[1:] + pd.Timedelta(hours=16), side)
    summarize("01 intraday momentum", sym, tr, N0 + i)
