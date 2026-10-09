"""Backlog 7: VIX term-structure filter (Simon and Campasano 2014).

Long index CFD from day d's 16:00 to day d+1's 16:00 when VIX < VIX3M at d's close (contango).
Ledger tests 315-317. The unconditional long is printed as the buy-and-hold baseline and is not
counted as a test. VIX data: gex.daily_bars cboe_index, exported to vix_term.csv.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from _common import INDICES, sessions, summarize, trades

data = sys.argv[1]
vix = pd.read_csv(Path(data) / "vix_term.csv", parse_dates=["date"]).set_index("date")
contango = (vix["vix"] < vix["vix3m"])
for i, sym in enumerate(INDICES, start=1):
    d = sessions(data, sym)
    d = d[(d.tz_localize(None) >= vix.index[0]) & (d.tz_localize(None) <= vix.index[-1])]
    close_t = d + pd.Timedelta(hours=16)
    on = contango.reindex(d.tz_localize(None)).fillna(False).to_numpy()[:-1]
    summarize("07 VIX contango long", sym, trades(data, sym, close_t[:-1][on], close_t[1:][on], 1), 314 + i)
    summarize("   baseline: always long", sym, trades(data, sym, close_t[:-1], close_t[1:], 1), 314 + i)
    off = ~on
    summarize("   context: backwardation long", sym, trades(data, sym, close_t[:-1][off], close_t[1:][off], 1), 314 + i)
    print("   share of days in contango:", round(float(np.mean(on)), 3))
