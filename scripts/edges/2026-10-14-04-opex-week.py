"""Backlog 4: opex-week drift (Stivers and Sun 2013), scored as a position rule.

Unblocked 2026-10-14: it holds five sessions, so the *Position rules* bar applies (>= 100 days in
the market, >= 10 entries), which it meets on the broker span. The paper's week is the prior
Friday close -> the expiration Friday close, so: long from the close of the last session before
the opex week to the close of the third Friday (Thursday when Friday is a holiday).
Ledger tests 526-528.
"""
import sys

import _common
import pandas as pd
from _common import INDICES, closes_for, eval_positions

data = sys.argv[1]
for i, sym in enumerate(INDICES):
    c = closes_for(data, sym)
    idx = c.index
    pos = pd.Series(0.0, index=idx)
    months = sorted(set(idx.to_period("M")))
    for m in months:
        first = pd.Timestamp(m.start_time).tz_localize(_common.NY)
        fridays = pd.date_range(first, periods=31, freq="D")
        fridays = fridays[(fridays.weekday == 4) & (fridays.month == first.month)]
        if len(fridays) < 3:
            continue
        opex = fridays[2]
        expiry = idx[idx <= opex]
        if not len(expiry) or (opex - expiry[-1]).days > 1:
            continue
        expiry = expiry[-1]
        monday = opex - pd.Timedelta(days=4)
        before = idx[idx < monday]
        if not len(before):
            continue
        # position decided at each close is held to the next close: on from `before[-1]`
        # through the session before expiry, so P&L runs prior Friday close -> expiry close.
        pos[(idx >= before[-1]) & (idx < expiry)] = 1.0
    eval_positions("4 opex week", sym, c, pos, 526 + i)
