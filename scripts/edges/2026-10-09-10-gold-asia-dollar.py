"""Backlog 10: gold-asia-drift only after a down day in the dollar (UUP close < previous close).

Variant of ledger #301. Ledger test 322. The same window unconditional, and on dollar-up days,
are printed as context over the same 2021-09+ span and are not counted.
"""
import sys
from pathlib import Path

import pandas as pd

from _common import sessions, summarize, trades

data = sys.argv[1]
uup = pd.read_csv(Path(data) / "uup.csv", parse_dates=["date"]).set_index("date")["close"]
down = (uup < uup.shift(1))
d = sessions(data, "XAUUSD")
d = d[(d.tz_localize(None) > uup.index[0]) & (d.tz_localize(None) <= uup.index[-1])]
t0 = d[:-1] + pd.Timedelta(hours=18)
t1 = d[1:] + pd.Timedelta(hours=3)
cond = down.reindex(d[:-1].tz_localize(None))
on = cond.fillna(False).to_numpy(bool)
known = cond.notna().to_numpy()
summarize("10 gold Asia | dollar down", "XAUUSD", trades(data, "XAUUSD", t0[on], t1[on], 1), 322)
summarize("   context: unconditional", "XAUUSD", trades(data, "XAUUSD", t0[known], t1[known], 1), 322)
summarize("   context: dollar up", "XAUUSD", trades(data, "XAUUSD", t0[known & ~on], t1[known & ~on], 1), 322)
