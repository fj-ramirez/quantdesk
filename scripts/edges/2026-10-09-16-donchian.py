"""Backlog 16: Donchian breakout, long and short (Turtle rules, Faith 2003).

Long on a close above the prior 20-session high, short on a close below the prior 20-session low.
Exit long on a close below the prior 10-session low, short on a close above the prior 10-session
high. Highs and lows are of the 16:00 closes. Position rule. Ledger tests 337-340.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, closes_for, eval_positions

data = sys.argv[1]
for i, sym in enumerate(INDICES + ["XAUUSD"], start=1):
    c = closes_for(data, sym)
    px = c["close"]
    hi20, lo20 = px.rolling(20).max().shift(1), px.rolling(20).min().shift(1)
    hi10, lo10 = px.rolling(10).max().shift(1), px.rolling(10).min().shift(1)
    pos = np.zeros(len(px))
    held = 0.0
    for k in range(len(px)):
        p = px.iloc[k]
        if held > 0 and p < lo10.iloc[k]:
            held = 0.0
        elif held < 0 and p > hi10.iloc[k]:
            held = 0.0
        if held == 0:
            if p > hi20.iloc[k]:
                held = 1.0
            elif p < lo20.iloc[k]:
                held = -1.0
        pos[k] = held
    eval_positions("16 Donchian 20/10", sym, c, pd.Series(pos, index=px.index), 336 + i)
