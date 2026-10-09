"""Backlog 23: IBS mean reversion (Pagonidis 2013). RTH IBS < 0.2 -> long 16:00 -> next 16:00.
Ledger tests 378-380."""
import sys

import pandas as pd

from _common import INDICES, rth_daily, summarize, trades

data = sys.argv[1]
for i, sym in enumerate(INDICES, start=1):
    b = rth_daily(data, sym)
    ibs = (b["close"] - b["low"]) / (b["high"] - b["low"])
    d = b.index
    m = (ibs < 0.2).to_numpy()[:-1]
    summarize("23 IBS < 0.2", sym, trades(data, sym, (d[:-1] + pd.Timedelta(hours=16))[m],
                                          (d[1:] + pd.Timedelta(hours=16))[m], 1), 377 + i)
