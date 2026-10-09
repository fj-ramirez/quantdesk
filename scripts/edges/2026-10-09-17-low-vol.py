"""Backlog 17: low-volatility regime long (after Moreira and Muir 2017).

Long while 20-session realized vol of close-to-close returns < its trailing 252-session median,
otherwise flat. Position rule. Ledger tests 341-343.
"""
import sys

from _common import INDICES, closes_for, eval_positions

data = sys.argv[1]
for i, sym in enumerate(INDICES, start=1):
    c = closes_for(data, sym)
    vol = c["close"].pct_change().rolling(20).std()
    pos = (vol < vol.rolling(252).median()).astype(float)
    eval_positions("17 low-vol regime long", sym, c, pos, 340 + i)
