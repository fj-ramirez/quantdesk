"""Backlog 44: the mean-reversion composite on 2000-01 -> 2019-06 Yahoo daily ^GSPC / ^NDX
(independent of every broker-span test). Ledger tests 427-428.

Entry at the close: close > SMA200 and (RSI(2) < 10 or IBS < 0.2 or close = 7-day low).
Exit at the first close > SMA5. Cost per position change: the matching CFD's median spread %.
The same rule on the broker span (2019+) is printed as context and not counted (already seen).
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf

from _common import closes_for, eval_positions, rth_daily

data = sys.argv[1]


def signal(c, h, l):
    up = c.diff().clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-c.diff().clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn)
    ibs = (c - l) / (h - l)
    entry = (c > c.rolling(200).mean()) & ((rsi < 10) | (ibs < 0.2) | (c <= c.rolling(7).min()))
    exit_ = c > c.rolling(5).mean()
    pos, held = np.zeros(len(c)), 0.0
    for i, (e, x) in enumerate(zip(entry.to_numpy(), exit_.to_numpy())):
        if held and x:
            held = 0.0
        elif not held and e:
            held = 1.0
        pos[i] = held
    return pd.Series(pos, index=c.index)


for k, (yahoo, cfd) in enumerate([("^GSPC", "S&P.fs"), ("^NDX", "NAS100.fs")], start=1):
    cc = closes_for(data, cfd)
    spread_pct = float((cc["spread"] / cc["close"]).median())
    raw = yf.download(yahoo, start="2000-01-01", end="2019-07-01", auto_adjust=False, progress=False)
    c, h, l = (raw[f].squeeze().dropna() for f in ("Close", "High", "Low"))
    idx = pd.DatetimeIndex(c.index).tz_localize("America/New_York")
    closes = pd.DataFrame({"close": c.to_numpy(), "spread": c.to_numpy() * spread_pct}, index=idx)
    pos = pd.Series(signal(c, h, l).to_numpy(), index=idx)
    print(f"{yahoo}: CFD spread {spread_pct * 100:.4f}% per change")
    eval_positions("44 MR composite (2000-19)", yahoo, closes, pos, 426 + k)
    for lo, hi in (("2000", "2009"), ("2010", "2019")):
        eval_positions(f"   {lo}-{hi}", yahoo, closes.loc[lo:hi], pos.loc[lo:hi], 426 + k)
    # context: same rule on the broker span (RTH OHLC from M1), not counted
    rth = rth_daily(data, cfd)
    ctx = closes_for(data, cfd)
    p2 = signal(rth["close"], rth["high"], rth["low"]).reindex(ctx.index).fillna(0.0)
    eval_positions("   context: broker 2019+ (seen)", cfd, ctx, p2, 426 + k)
