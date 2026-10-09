"""Backlog 21: the Friday-close -> next-session-close hold (#370-372's observation) on
2000-01 -> 2019-06 Yahoo daily ^GSPC / ^NDX, data it was not found on. Ledger tests 373-374.
"""
import math
import sys

import numpy as np
import pandas as pd
import yfinance as yf

from _common import bar, closes_for

data = sys.argv[1]
for k, (yahoo, cfd) in enumerate([("^GSPC", "S&P.fs"), ("^NDX", "NAS100.fs")], start=1):
    c_cfd = closes_for(data, cfd)
    spread_pct = float((c_cfd["spread"] / c_cfd["close"]).median())
    px = yf.download(yahoo, start="2000-01-01", end="2019-07-01", auto_adjust=False,
                     progress=False)["Close"].squeeze().dropna()
    r = px.pct_change().shift(-1)  # close d -> close of the next session
    fri = r[px.index.weekday == 4].dropna() * 100 - 2 * spread_pct * 100
    other = r[px.index.weekday != 4].dropna() * 100 - 2 * spread_pct * 100
    t = fri.mean() / fri.std() * math.sqrt(len(fri))
    yrs = fri.groupby(fri.index.year).sum()
    halves = {h: fri.loc[a:b].mean() for h, (a, b) in {"2000-09": ("2000", "2009"),
                                                       "2010-19": ("2010", "2019")}.items()}
    print(f"21 weekend {yahoo}: n={len(fri)} mean={fri.mean():+.4f}% t={t:+.2f} bar={bar(372 + k):.2f} "
          f"| other days mean {other.mean():+.4f}% | halves {({h: round(v, 4) for h, v in halves.items()})} "
          f"| years+ {(yrs > 0).sum()}/{len(yrs)}")
