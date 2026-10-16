"""Backlog 52: the nas-mean-reversion composite with entries only when dealers are long gamma.

#44's rule unchanged (close > SMA200 and RSI(2) < 10 or IBS < 0.2 or a 7-day low; exit at the
first close > SMA5), but an entry at close t is taken only when SqueezeMetrics `gex` for the
previous session > 0. A held position exits by the rule regardless of gamma. Yahoo daily ^NDX
2011-05-03 -> 2026-10-15, cost the NAS100.fs median spread per position change. Declared in
hypothesis-backlog.md pass 13 (commit 1cde5c8) before this ran. Ledger 547.

Adopted only if the filtered rule clears the bar, its mean per day in the market beats the
unfiltered rule's, and the excluded entries (gex <= 0) do worse than the filtered ones.

Usage: python 2026-10-16-52-mr-positive-gamma.py <m1 export dir> <DIX.csv>
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf
from _common import closes_for, eval_positions

data, dix = sys.argv[1], sys.argv[2]
N = 547


def signal(c, h, l, allow):
    up = c.diff().clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-c.diff().clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn)
    ibs = (c - l) / (h - l)
    entry = (c > c.rolling(200).mean()) & ((rsi < 10) | (ibs < 0.2) | (c <= c.rolling(7).min())) & allow
    exit_ = c > c.rolling(5).mean()
    pos, held = np.zeros(len(c)), 0.0
    for i, (e, x) in enumerate(zip(entry.to_numpy(), exit_.to_numpy())):
        if held and x:
            held = 0.0
        elif not held and e:
            held = 1.0
        pos[i] = held
    return pd.Series(pos, index=c.index)


cc = closes_for(data, "NAS100.fs")
spread_pct = float((cc["spread"] / cc["close"]).median())
raw = yf.download("^NDX", start="2010-01-01", end="2026-10-16", auto_adjust=False, progress=False)
c, h, l = (raw[f].squeeze().dropna() for f in ("Close", "High", "Low"))
c.index = h.index = l.index = pd.DatetimeIndex(c.index).normalize()

g = pd.read_csv(dix, parse_dates=["date"])[["date", "gex"]].sort_values("date")
s = pd.DataFrame({"date": c.index.astype("datetime64[ns]")})
g["date"] = g["date"].astype("datetime64[ns]")
gex = pd.Series(pd.merge_asof(s, g, on="date", allow_exact_matches=False)["gex"].to_numpy(), index=c.index)

idx = pd.DatetimeIndex(c.index).tz_localize("America/New_York")
closes = pd.DataFrame({"close": c.to_numpy(), "spread": c.to_numpy() * spread_pct}, index=idx)
span = slice("2011-05-03", "2026-10-15")
print(f"^NDX: NAS100.fs spread {spread_pct * 100:.4f}% per change; gex(t-1) <= 0 on "
      f"{int((gex.loc[span] <= 0).sum())} of {int(gex.loc[span].notna().sum())} sessions")
for name, allow, n in (("52 MR, entry gex(t-1) > 0", gex > 0, N),
                       ("   unfiltered (#44 rule)", pd.Series(True, index=c.index), N),
                       ("   excluded: entry gex(t-1) <= 0", gex <= 0, N)):
    pos = pd.Series(signal(c, h, l, allow).to_numpy(), index=idx)
    eval_positions(name, "^NDX", closes.loc[span], pos.loc[span], n)
