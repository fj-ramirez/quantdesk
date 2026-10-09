"""Backlog 19: the RSI(2) pullback (#334-336), rule unchanged, on 2000-01 -> 2019-06 Yahoo daily
^GSPC and ^NDX. That history predates the broker export, so all of it is out-of-sample for the
rule. Cost per position change = the matching CFD's median recorded spread as % of price.
Ledger tests 356-357. Downloads public Yahoo data (read-only).
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf

from _common import closes_for, eval_positions

data = sys.argv[1]
for k, (yahoo, cfd) in enumerate([("^GSPC", "S&P.fs"), ("^NDX", "NAS100.fs")], start=1):
    c_cfd = closes_for(data, cfd)
    spread_pct = float((c_cfd["spread"] / c_cfd["close"]).median())
    raw = yf.download(yahoo, start="2000-01-01", end="2019-07-01", auto_adjust=False, progress=False)
    px = raw["Close"].squeeze().dropna()
    idx = pd.DatetimeIndex(px.index).tz_localize("America/New_York")
    closes = pd.DataFrame({"close": px.to_numpy(), "spread": px.to_numpy() * spread_pct}, index=idx)
    rsi_up = px.diff().clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    rsi_dn = (-px.diff().clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi = 100 - 100 / (1 + rsi_up / rsi_dn)
    entry = ((rsi < 10) & (px > px.rolling(200).mean())).to_numpy()
    exit_ = (px > px.rolling(5).mean()).to_numpy()
    pos, held = np.zeros(len(px)), 0.0
    for i in range(len(px)):
        if held and exit_[i]:
            held = 0.0
        elif not held and entry[i]:
            held = 1.0
        pos[i] = held
    print(f"{yahoo}: spread {spread_pct * 100:.4f}% of price per change")
    res = eval_positions("19 RSI(2) independent", yahoo, closes, pd.Series(pos, index=idx), 355 + k)
    pct_by_half = {}
    p = pd.Series(pos, index=idx)
    for lo, hi in (("2000", "2009"), ("2010", "2019")):
        sub = closes.loc[lo:hi]
        r = eval_positions(f"   {lo}-{hi}", yahoo, sub, p.loc[lo:hi], 355 + k)
        pct_by_half[lo] = r["mean"]
