"""Backlog 28: overnight/intraday tug of war (Lou, Polk, Skouras 2019). Trade 09:30 -> 16:00
against the sign of the 16:00 -> 09:30 overnight return. Ledger tests 391-393."""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, at, load, sessions, summarize, trades

data = sys.argv[1]
for i, sym in enumerate(INDICES, start=1):
    df = load(data, sym)
    d = sessions(data, sym)
    prev, _, ok_a = at(df, d[:-1] + pd.Timedelta(hours=16))
    opn, _, ok_b = at(df, d[1:] + pd.Timedelta(hours=9, minutes=30))
    side = np.where(ok_a & ok_b, -np.sign(opn - prev), 0)
    summarize("28 tug of war", sym, trades(data, sym, d[1:] + pd.Timedelta(hours=9, minutes=30),
                                           d[1:] + pd.Timedelta(hours=16), side), 390 + i)
