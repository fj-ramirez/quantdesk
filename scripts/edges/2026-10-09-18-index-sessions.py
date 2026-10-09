"""Backlog 18: index session decomposition (the gold #301 analysis, on the index CFDs).

Long each window separately on every session date d that has a previous session:
Asia 18:00 (d-1) -> 03:00 d, London 03:00 -> 09:30, NY morning 09:30 -> 12:00,
NY afternoon 12:00 -> 16:00. Ledger tests 344-355 (window-major, index-minor).
"""
import sys

import pandas as pd

from _common import INDICES, sessions, summarize, trades

data = sys.argv[1]
H = pd.Timedelta(hours=1)
WINDOWS = [("Asia 18-03", -6 * H, 3 * H), ("London 03-0930", 3 * H, 9.5 * H),
           ("NY am 0930-12", 9.5 * H, 12 * H), ("NY pm 12-16", 12 * H, 16 * H)]
n = 343
for wname, a, b in WINDOWS:
    for sym in INDICES:
        n += 1
        d = sessions(data, sym)[1:]
        summarize(f"18 {wname}", sym, trades(data, sym, d + a, d + b, 1), n)
