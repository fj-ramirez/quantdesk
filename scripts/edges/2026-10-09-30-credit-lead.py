"""Backlog 30: credit leads equities (Gilchrist and Zakrajsek 2012). Each Friday close: HYG 20-session
return > IEF's -> long the index CFD until the next Friday close, else flat. Ledger 397-399."""
import sys

import pandas as pd

import _common

from _common import INDICES, closes_for, eval_positions, xasset

data = sys.argv[1]
hyg, ief = xasset(data, "HYG"), xasset(data, "IEF")
risk_on = (hyg.pct_change(20) > ief.pct_change(20))
fridays = risk_on[risk_on.index.weekday == 4]
for i, sym in enumerate(INDICES, start=1):
    c = closes_for(data, sym)
    naive = c.index.tz_localize(None)
    c = c[(naive >= fridays.index[0])]
    _common._DATA_OF[id(c)] = data  # a slice is a new frame; register it for the roll lookup
    sig = fridays.reindex(c.index.tz_localize(None)).ffill().fillna(False).astype(float)
    eval_positions("30 credit lead", sym, c, pd.Series(sig.to_numpy(), index=c.index), 396 + i)
