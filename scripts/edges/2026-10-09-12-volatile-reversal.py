"""Backlog 12: last-half-hour reversal on volatile mornings (Baltussen, Da, Lammers, Martens 2021).

If the 09:30-10:00 range is > 2x the median of the previous 20 sessions' 09:30-10:00 ranges,
trade 15:30 -> 16:00 against the sign of the prior 16:00 -> 10:00 return. Ledger tests 323-325.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, at, load, sessions, summarize, trades

data = sys.argv[1]
for i, sym in enumerate(INDICES, start=1):
    df = load(data, sym)
    d = sessions(data, sym)
    ny = df.index
    m = (ny.hour == 9) & (ny.minute >= 30)
    first = df[m]
    rng = (first.groupby(first.index.normalize())["high"].max()
           - first.groupby(first.index.normalize())["low"].min()).reindex(d)
    base = rng.rolling(20, min_periods=20).median().shift(1)
    volatile = (rng > 2 * base).to_numpy()[1:]
    prev_close, _, ok_a = at(df, d[:-1] + pd.Timedelta(hours=16))
    ten, _, ok_b = at(df, d[1:] + pd.Timedelta(hours=10))
    side = np.where(ok_a & ok_b & volatile, -np.sign(ten - prev_close), 0)
    tr = trades(data, sym, d[1:] + pd.Timedelta(hours=15, minutes=30), d[1:] + pd.Timedelta(hours=16), side)
    summarize("12 volatile-morning reversal", sym, tr, 322 + i)
