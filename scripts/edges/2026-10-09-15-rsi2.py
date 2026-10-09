"""Backlog 15: RSI(2) pullback in an uptrend (Connors and Alvarez 2009).

Enter long at the 16:00 close when RSI(2) < 10 and close > SMA200; exit at the first close above
SMA5. Position rule. Ledger tests 334-336.
"""
import sys

import numpy as np
import pandas as pd

from _common import INDICES, closes_for, eval_positions

data = sys.argv[1]


def rsi(c: pd.Series, n: int = 2) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


for i, sym in enumerate(INDICES, start=1):
    c = closes_for(data, sym)
    px = c["close"]
    entry = (rsi(px) < 10) & (px > px.rolling(200).mean())
    exit_ = px > px.rolling(5).mean()
    pos = np.zeros(len(px))
    held = 0.0
    for k, (e, x) in enumerate(zip(entry.to_numpy(), exit_.to_numpy())):
        if held and x:
            held = 0.0
        elif not held and e:
            held = 1.0
        pos[k] = held
    eval_positions("15 RSI(2) pullback", sym, c, pd.Series(pos, index=px.index), 333 + i)
