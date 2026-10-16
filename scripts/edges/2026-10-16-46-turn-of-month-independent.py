"""Backlog 46: turn of the month (Lakonishok and Smidt 1988) on independent history.

#295-297's rule unchanged: long from the close of the month's last trading day to the close of
the 3rd trading day of the next month. ^GSPC / ^NDX 1990-01 -> 2019-06, the CFD's median spread
doubled. Criterion 5: the mean must beat every 3-session close-to-close hold. Ledger 539-540.
"""
import math
import sys

import pandas as pd
from _independent import PAIRS, close_to_close, spread_pct, summarize, yahoo_closes

data = sys.argv[1]
for k, (ticker, cfd) in enumerate(PAIRS):
    c = yahoo_closes(ticker, "1989-12-01")
    c = c[c.index >= "1990-01-01"]
    idx = c.index
    last = pd.Series(range(len(idx)), index=idx).groupby(idx.to_period("M")).max()
    pairs = [(idx[i], idx[i + 3]) for i in last if i + 3 < len(idx)]
    cost = spread_pct(data, cfd)
    tr = close_to_close(c, [a for a, _ in pairs], [b for _, b in pairs], cost)
    res = summarize("46 turn of month (1990-2019)", ticker, tr, 539 + k)
    base = (c.shift(-3) / c - 1).dropna() * 100 - 2 * cost * 100
    diff = res["mean"] - base.mean()
    se = math.sqrt(tr["pct"].var(ddof=1) / len(tr) + base.var(ddof=1) / len(base))
    print(f"   criterion 5: every 3-session hold {base.mean():+.4f}% (n {len(base)}); "
          f"difference {diff:+.4f}%, t {diff / se:+.2f}")
