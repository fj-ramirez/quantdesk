"""Backlog 13: time-series momentum (Moskowitz, Ooi, Pedersen 2012).

Long while the 16:00 close is above the close 252 sessions earlier, otherwise flat.
Position rule (search-log.md). Ledger tests 326-329.
"""
import sys

from _common import INDICES, closes_for, eval_positions

data = sys.argv[1]
for i, sym in enumerate(INDICES + ["XAUUSD"], start=1):
    c = closes_for(data, sym)
    pos = (c["close"] > c["close"].shift(252)).astype(float)
    eval_positions("13 TS momentum 252d", sym, c, pos, 325 + i)
