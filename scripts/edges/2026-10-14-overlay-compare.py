"""index-hold-sma200 follow-up: SMA200 vs the utilities rotation (#387-389) as the drawdown overlay
on NAS100.fs and S&P.fs, over the span both exist (XLU/SPY from 2021-09). Ledger 533-534.
A choice between two overlays is a look, so both count. Max drawdown is on summed daily %."""
import sys

import _common
import pandas as pd
from _common import closes_for, eval_positions, xasset

data = sys.argv[1]
xlu, spy = xasset(data, "XLU"), xasset(data, "SPY")
risk_on = xlu.pct_change(20) < spy.pct_change(20)
fridays = risk_on[risk_on.index.weekday == 4]


def max_dd(pct: pd.Series) -> float:
    eq = pct.cumsum()
    return float((eq - eq.cummax()).min())


for k, sym in enumerate(["NAS100.fs", "S&P.fs"]):
    full = closes_for(data, sym)
    sma = (full["close"] > full["close"].rolling(200).mean()).astype(float)
    c = full[full.index.tz_localize(None) >= fridays.index[20]]
    _common._DATA_OF[id(c)] = data
    util = fridays.reindex(c.index.tz_localize(None)).ffill().fillna(False).astype(float)
    print(f"--- {sym} from {c.index[0].date()}")
    for name, pos in (("SMA200", sma.reindex(c.index)),
                      ("utilities", pd.Series(util.to_numpy(), index=c.index))):
        r = eval_positions(f"overlay {name}", sym, c, pos, 533 + k)
        print(f"   max DD {max_dd(r['pct']):+.1f}% vs hold {max_dd(r['bh_pct']):+.1f}%  | in market "
              f"{pos.mean() * 100:.0f}% of days")
