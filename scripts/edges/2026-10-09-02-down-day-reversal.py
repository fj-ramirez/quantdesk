"""Backlog 2: short-term reversal after a large down day (Connors/Alvarez 2008).

Close = price at 16:00 NY. If today's close-to-close return <= -2 x stdev of the previous 20
sessions' returns, buy at today's 16:00 and sell at the next session's 16:00.
Ledger tests 310-312, one per index CFD.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, at, load, sessions, summarize, trades

data = sys.argv[1]
N0 = 309
for i, sym in enumerate(INDICES, start=1):
    d = sessions(data, sym)
    close_t = d + pd.Timedelta(hours=16)
    c, _, ok = at(load(data, sym), close_t)
    s = pd.Series(np.where(ok, c, np.nan), index=d)
    r = s.pct_change()
    sd = r.rolling(20).std().shift(1)
    sig = (r <= -2 * sd).to_numpy()[:-1]
    tr = trades(data, sym, close_t[:-1][sig], close_t[1:][sig], 1)
    summarize("02 down-day reversal", sym, tr, N0 + i)
