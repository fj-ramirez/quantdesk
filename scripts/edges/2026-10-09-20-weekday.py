"""Backlog 20: index weekday effect (French 1980; Gibbons and Hess 1981).

Long 16:00 -> next session's 16:00, separately for each entry weekday. Ledger tests 358-372
(weekday-major, index-minor).
"""
import sys

import pandas as pd

from _common import INDICES, sessions, summarize, trades

data = sys.argv[1]
n = 357
for wd, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
    for sym in INDICES:
        n += 1
        d = sessions(data, sym)
        t0, t1 = d[:-1], d[1:]
        m = t0.weekday == wd
        summarize(f"20 weekday {name}", sym,
                  trades(data, sym, t0[m] + pd.Timedelta(hours=16), t1[m] + pd.Timedelta(hours=16), 1), n)
