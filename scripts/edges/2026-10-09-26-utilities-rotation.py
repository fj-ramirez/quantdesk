"""Backlog 26: utilities beta rotation (Gayed and Bilello 2014). Each Friday close: XLU 20-session
return < SPY's -> long the index CFD until the next Friday close, else flat. Ledger 387-389."""
import sys

import pandas as pd

import _common

from _common import INDICES, closes_for, eval_positions, xasset

data = sys.argv[1]
xlu, spy = xasset(data, "XLU"), xasset(data, "SPY")
risk_on = (xlu.pct_change(20) < spy.pct_change(20))
fridays = risk_on[risk_on.index.weekday == 4]
for i, sym in enumerate(INDICES, start=1):
    c = closes_for(data, sym)
    naive = c.index.tz_localize(None)
    c = c[(naive >= fridays.index[0])]
    _common._DATA_OF[id(c)] = data  # a slice is a new frame; register it for the roll lookup
    sig = fridays.reindex(c.index.tz_localize(None)).ffill().fillna(False).astype(float)
    eval_positions("26 utilities rotation", sym, c, pd.Series(sig.to_numpy(), index=c.index), 386 + i)
