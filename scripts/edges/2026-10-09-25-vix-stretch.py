"""Backlog 25: VIX stretch (Connors and Alvarez 2009). VIX close > 1.10 x SMA10 -> long the index
16:00 -> 16:00 five sessions later; no overlapping trades. Ledger tests 384-386."""
import sys

import pandas as pd

from _common import INDICES, sessions, summarize, trades, xasset

data = sys.argv[1]
vix = xasset(data, "^VIX")
sig = (vix > 1.10 * vix.rolling(10).mean())
for i, sym in enumerate(INDICES, start=1):
    d = sessions(data, sym)
    on = sig.reindex(d.tz_localize(None)).fillna(False).to_numpy()
    t0, t1, k = [], [], 0
    while k < len(d) - 5:
        if on[k]:
            t0.append(d[k] + pd.Timedelta(hours=16))
            t1.append(d[k + 5] + pd.Timedelta(hours=16))
            k += 5
        else:
            k += 1
    summarize("25 VIX stretch", sym, trades(data, sym, t0, t1, 1), 383 + i)
