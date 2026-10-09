"""Backlog 22: Turnaround Tuesday. If Monday's 16:00 close < the previous session's (Friday's)
16:00 close, long Monday 16:00 -> Tuesday 16:00. Ledger tests 375-377."""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, at, load, sessions, summarize, trades

data = sys.argv[1]
for i, sym in enumerate(INDICES, start=1):
    d = sessions(data, sym)
    c, _, ok = at(load(data, sym), d + pd.Timedelta(hours=16))
    prev_fri = np.r_[False, (d[:-1].weekday == 4)]
    is_mon = d.weekday == 0
    nxt_tue = np.r_[(d[1:].weekday == 1), False]
    down = np.r_[False, c[1:] < c[:-1]] & ok & np.r_[False, ok[:-1]]
    m = (is_mon & prev_fri & down & nxt_tue)[:-1]
    summarize("22 Turnaround Tuesday", sym, trades(data, sym, (d[:-1] + pd.Timedelta(hours=16))[m],
                                                   (d[1:] + pd.Timedelta(hours=16))[m], 1), 374 + i)
