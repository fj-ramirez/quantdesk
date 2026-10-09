"""Backlog 6: gold drifts down into the London fixes (Caminschi and Heaney 2014).

Short XAUUSD 10:00 -> 10:30 London (AM fix) and 14:30 -> 15:00 London (PM fix), every weekday.
Ledger tests 313 (AM) and 314 (PM).
"""
import sys

import pandas as pd

from _common import load, summarize, trades

data = sys.argv[1]
df = load(data, "XAUUSD")
ldn = df.index.tz_convert("Europe/London")
days = pd.DatetimeIndex(sorted(set(ldn[ldn.weekday < 5].normalize())))
for k, (h0, m0, h1, m1) in enumerate([(10, 0, 10, 30), (14, 30, 15, 0)], start=1):
    t0 = (days + pd.Timedelta(hours=h0, minutes=m0)).tz_convert("America/New_York")
    t1 = (days + pd.Timedelta(hours=h1, minutes=m1)).tz_convert("America/New_York")
    summarize("06 gold fix " + ("AM" if k == 1 else "PM") + " short", "XAUUSD",
              trades(data, "XAUUSD", t0, t1, -1), 312 + k)
