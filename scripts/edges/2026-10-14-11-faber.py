"""Backlog 11: Faber (2007) 10-month SMA on month-end closes, NAS100.fs, position rule.

Unblocked 2026-10-14: it is a position rule, so the *Position rules* bar applies (>= 100 days in
the market, >= 10 entries), not the per-trade 100-trade bar it was blocked on.
Rule: at each month's last 16:00 close, long for the next month if that close > the mean of the
last 10 month-end closes, else flat. Ledger test 531.
"""
import sys

import _common
from _common import closes_for, eval_positions

data = sys.argv[1]
c = closes_for(data, "NAS100.fs")
month_end = c["close"].groupby(c.index.to_period("M")).tail(1)
signal = (month_end > month_end.rolling(10).mean()).astype(float)
signal[month_end.rolling(10).mean().isna()] = float("nan")
pos = signal.reindex(c.index).ffill()
start = signal.first_valid_index()
c2 = c[c.index >= start]
_common._DATA_OF[id(c2)] = data
eval_positions("11 Faber 10-month SMA", "NAS100.fs", c2, pos[pos.index >= start].fillna(0.0), 531)
