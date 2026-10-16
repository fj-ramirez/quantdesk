"""Backlog 47: month-end pension rebalancing, the Calendar signal (Harvey, Mazzoleni and Melone
2025, NBER w33554, section 4), equity leg only.

60/40 ^GSPC / IEF (adjusted close), reset to 60% at each month's last trading-day close.
Cal_t = equity weight - 0.60 at close t. t in the month's last 5 trading days: hold -sign(Cal_t)
close t -> close t+1. t = first trading day of a month: hold sign(Cal on the previous month's
4th-last trading day). Otherwise flat. Position rule on the CFD's 16:00 closes. Ledger 541-542.
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf
from _common import closes_for, eval_positions

data = sys.argv[1]


def yclose(ticker: str, adjusted: bool) -> pd.Series:
    raw = yf.download(ticker, start="2019-01-01", auto_adjust=False, progress=False)
    s = raw["Adj Close" if adjusted else "Close"].squeeze().dropna()
    s.index = pd.DatetimeIndex(s.index).normalize()
    return s


both = pd.concat([yclose("^GSPC", False), yclose("IEF", True)], axis=1, keys=["eq", "bd"]).dropna()
r = both.pct_change()
idx = both.index
month = pd.Series(idx.to_period("M"), index=idx)
is_last = month != month.shift(-1)
is_first = month != month.shift(1)

w = np.full(len(idx), np.nan)
cur = np.nan
for i in range(len(idx)):
    if i > 0 and not np.isnan(cur):
        a = cur * (1 + r["eq"].iloc[i])
        cur = a / (a + (1 - cur) * (1 + r["bd"].iloc[i]))
    w[i] = cur
    if is_last.iloc[i]:
        cur = 0.60  # rebalanced to target at this close
cal = pd.Series(w - 0.60, index=idx)

n_in_month = month.groupby(month).transform("size")
rank = month.groupby(month).cumcount() + 1
from_end = n_in_month - rank + 1  # 1 = the month's last trading day
cal4 = cal[from_end == 4]
cal4_by_month = pd.Series(cal4.to_numpy(), index=month[from_end == 4].to_numpy())

pos = pd.Series(0.0, index=idx)
week4 = from_end <= 5
pos[week4] = -np.sign(cal[week4])
for d in idx[is_first]:
    prev = d.to_period("M") - 1
    if prev in cal4_by_month.index:
        pos[d] = np.sign(cal4_by_month[prev])
pos = pos.fillna(0.0)
print(f"signal days: week4 {int(week4.sum())}, first-day {int(is_first.sum())}; "
      f"short share {float((pos < 0).sum() / (pos != 0).sum()):.2f}")

for k, sym in enumerate(["S&P.fs", "NAS100.fs"]):
    c = closes_for(data, sym)
    naive = c.index.tz_localize(None) if c.index.tz is not None else c.index
    p = pd.Series(pos.reindex(naive).fillna(0.0).to_numpy(), index=c.index)
    res = eval_positions("47 month-end rebalancing", sym, c, p, 541 + k)
    pct = res["pct"]
    live = pct[(p.reindex(pct.index) != 0).to_numpy()]
    for label, part in (("pre-2023-03-18 (in paper)", live[live.index < "2023-03-18"]),
                        ("post-paper", live[live.index >= "2023-03-18"])):
        print(f"   {label}: days {len(part)} mean/day {part.mean():+.4f}% "
              f"t {part.mean() / part.std(ddof=1) * len(part) ** 0.5:+.2f}")
