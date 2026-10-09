"""Backlog 9: gold weekend effect (Blose and Gondhalekar 2013).

Long XAUUSD from Friday 16:00 to Monday 03:00 New York, swap charged (one rollover, Friday 17:00).
Ledger test 321. Overlaps gold-asia-drift's Sunday-evening leg.
"""
import sys

import pandas as pd

from _common import sessions, summarize, trades

data = sys.argv[1]
d = sessions(data, "XAUUSD")
fri = d[d.weekday == 4]
mon = fri + pd.Timedelta(days=3)
keep = mon.isin(d)  # Monday must be a session
t0 = fri[keep] + pd.Timedelta(hours=16)
t1 = mon[keep] + pd.Timedelta(hours=3)
tr = trades(data, "XAUUSD", t0, t1, 1)
summarize("09 gold weekend long", "XAUUSD", tr, 321)
print("   mean swap % per trade:", round(float(tr["swap_pct"].mean()), 4))
