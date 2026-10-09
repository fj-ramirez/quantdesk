"""Backlog 33: relative-momentum rotation (Antonacci 2014). At each month-end close hold the index CFD
with the best 63-session return; compare with an equal-weight hold. Ledger test 406."""
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from _common import SPLIT, bar

spec = importlib.util.spec_from_file_location("pairs", Path(__file__).with_name("2026-10-09-32-index-pairs.py"))
data = sys.argv[1]
src = Path(__file__).with_name("2026-10-09-32-index-pairs.py").read_text(encoding="utf-8")
ns: dict = {"__name__": "pairs_helpers"}
sys.argv = [sys.argv[0], data]
exec(src.split("n = 402")[0], ns)  # reuse leg_returns() only
leg_returns = ns["leg_returns"]

syms = ["S&P.fs", "NAS100.fs", "DJ30.fs"]
legs = {s: leg_returns(s) for s in syms}
idx = legs["S&P.fs"][0].index
for s in syms[1:]:
    idx = idx.intersection(legs[s][0].index)
px = pd.DataFrame({s: legs[s][0].reindex(idx) for s in syms})
ret = pd.DataFrame({s: legs[s][1].reindex(idx) for s in syms})
spr = pd.DataFrame({s: legs[s][2].reindex(idx) for s in syms})
mom = px.pct_change(63)
month_end = pd.Series(idx.to_period("M"), index=idx)
is_me = month_end != month_end.shift(-1)
choice = pd.Series(np.nan, index=idx, dtype=object)
ok = is_me & mom.notna().all(axis=1)
choice[ok] = mom[ok].idxmax(axis=1)
choice = choice.ffill()
pnl = []
prev = None
for d in idx[:-1]:
    c = choice[d]
    if not isinstance(c, str):
        pnl.append(0.0)
        continue
    cost = 0.0 if c == prev else spr.loc[d, c] + (spr.loc[d, prev] if prev else 0.0)
    pnl.append((ret.loc[d, c] - cost) * 100)
    prev = c
pnl = pd.Series(pnl, index=idx[:-1])
live = pnl[choice.reindex(pnl.index).map(lambda v: isinstance(v, str))]
ew = (ret.mean(axis=1) * 100).reindex(live.index)
switches = int((choice != choice.shift()).sum()) - 1
t = live.mean() / live.std() * math.sqrt(len(live))
sh = lambda v: v.mean() / v.std() * math.sqrt(252)
oos = live.index >= SPLIT
yrs = live.groupby(live.index.year).sum()
print(f"33 rotation: days={len(live)} switches={switches} mean/day={live.mean():+.4f}% t={t:+.2f} bar={bar(406):.2f} "
      f"| IS {live[~oos].mean():+.4f}% OOS {live[oos].mean():+.4f}% | Sharpe {sh(live):.2f} vs equal-weight {sh(ew):.2f} "
      f"| total {live.sum():+.0f}% vs EW {ew.sum():+.0f}% | years+ {(yrs > 0).sum()}/{len(yrs)}")
print("   years:", " ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()))
print("   held:", choice.value_counts().to_dict())
