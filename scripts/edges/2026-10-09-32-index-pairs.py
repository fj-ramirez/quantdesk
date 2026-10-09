"""Backlog 32: index pairs mean reversion (Gatev, Goetzmann, Rouwenhorst 2006). Ledger 403-405.

z = (log(A/B) - mean60) / sd60 at the 16:00 close. |z| > 2 -> long the cheap leg, short the rich
leg (equal notional); exit when z crosses 0 or after 20 sessions. Daily P&L in % of notional per
leg; each position change pays each leg's spread (a round trip = doubled spread).
"""
import math
import sys

import numpy as np
import pandas as pd

from _common import NY, SPLIT, bar, closes_for, rolls

data = sys.argv[1]


def leg_returns(sym):
    c = closes_for(data, sym)
    px = c["close"]
    move = px.shift(-1) - px
    for _, row in rolls(data)[lambda r: r.cfd_symbol == sym].iterrows():
        ts = row.rolled_at.tz_convert(NY)
        t0 = px.index + pd.Timedelta(hours=16)
        t1 = pd.DatetimeIndex(np.r_[t0[1:], t0[-1:]])
        hit = (t0 <= ts) & (t1 > ts)
        move[hit] -= row.step * px[hit]
    return px, move / px, c["spread"] / px


n = 402
for a, b in [("NAS100.fs", "S&P.fs"), ("NAS100.fs", "DJ30.fs"), ("S&P.fs", "DJ30.fs")]:
    n += 1
    pa, ra, sa = leg_returns(a)
    pb, rb, sb = leg_returns(b)
    idx = pa.index.intersection(pb.index)
    pa, ra, sa, pb, rb, sb = (x.reindex(idx) for x in (pa, ra, sa, pb, rb, sb))
    spread = np.log(pa / pb)
    z = ((spread - spread.rolling(60).mean()) / spread.rolling(60).std()).to_numpy()
    pos, held, age = np.zeros(len(idx)), 0.0, 0
    for k in range(len(idx)):
        if held:
            age += 1
            if (held > 0 and z[k] >= 0) or (held < 0 and z[k] <= 0) or age >= 20:
                held, age = 0.0, 0
        if not held and not math.isnan(z[k]):
            if z[k] < -2:
                held, age = 1.0, 0   # A cheap: long A, short B
            elif z[k] > 2:
                held, age = -1.0, 0
        pos[k] = held
    chg = np.abs(np.diff(np.r_[0.0, pos]))
    pnl = pd.Series(pos * (ra - rb).to_numpy() - chg * (sa + sb).to_numpy(), index=idx).iloc[:-1] * 100
    active = (pos[:-1] != 0) | (chg[:-1] != 0)
    x = pnl[active]
    entries = int(((pos != 0) & (np.r_[0.0, pos[:-1]] != pos)).sum())
    t = x.mean() / x.std() * math.sqrt(len(x))
    oos = x.index >= SPLIT
    yrs = pnl.groupby(pnl.index.year).sum()
    print(f"32 pair {a}/{b}: days={len(x)} entries={entries} mean/day={x.mean():+.4f}% t={t:+.2f} "
          f"bar={bar(n):.2f} | IS {x[~oos].mean():+.4f}% OOS {x[oos].mean():+.4f}% | total {pnl.sum():+.1f}% "
          f"| years+ {(yrs > 0).sum()}/{len(yrs)}")
    print("   years:", " ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()))
