"""Backlog 29: intraday periodicity (Heston, Korajczyk, Sadka 2010). Trade 15:30 -> 16:00 in the
direction of the previous session's 15:30 -> 16:00 return. Ledger tests 394-396."""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, at, load, sessions, summarize, trades

data = sys.argv[1]
H = pd.Timedelta(hours=1)
for i, sym in enumerate(INDICES, start=1):
    df = load(data, sym)
    d = sessions(data, sym)
    a, _, ok_a = at(df, d[:-1] + 15.5 * H)
    b, _, ok_b = at(df, d[:-1] + 16 * H)
    side = np.where(ok_a & ok_b, np.sign(b - a), 0)
    summarize("29 last-half-hour periodicity", sym,
              trades(data, sym, d[1:] + 15.5 * H, d[1:] + 16 * H, side), 393 + i)
