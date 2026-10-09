"""Backlog 27: gold-asia-drift (#301) only when the XAUUSD 16:00 close > its 200-session SMA.
Ledger test 390. The unfiltered rule and the filtered-out nights are printed as context, not counted."""
import sys

import pandas as pd

from _common import closes_for, sessions, summarize, trades

data = sys.argv[1]
c = closes_for(data, "XAUUSD")["close"]
up = (c > c.rolling(200).mean())
d = sessions(data, "XAUUSD")
t0, t1 = d[:-1] + pd.Timedelta(hours=18), d[1:] + pd.Timedelta(hours=3)
on = up.reindex(d[:-1]).fillna(False).to_numpy(bool)
known = c.rolling(200).mean().reindex(d[:-1]).notna().to_numpy()
summarize("27 gold Asia | uptrend", "XAUUSD", trades(data, "XAUUSD", t0[on], t1[on], 1), 390)
summarize("   context: unfiltered (same span)", "XAUUSD", trades(data, "XAUUSD", t0[known], t1[known], 1), 390)
summarize("   context: downtrend nights", "XAUUSD", trades(data, "XAUUSD", t0[known & ~on], t1[known & ~on], 1), 390)
