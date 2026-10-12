"""Exploration on the development set (2019-2022) only; the 2023-26 holdout is never read here.

Where inside the D1-downtrend pullback-sell setup is the forward drift large enough to beat costs?
Events: M15 closes where the setup holds (D1 EMA20<50<200, price < D1 EMA50, live H1 %B >= 0.5,
live M15 %B >= 0.5). Outcome: bid-to-bid move in the sell's favour after 4 h and 8 h (no costs).
Each feature is cut into quintiles; a bucket is reported with its mean, SE and per-year means.

    uv run --directory backend python ../scripts/edges/2026-10-12-pullback-dose.py data/broker-export
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PIP = 1e-4
NY = "America/New_York"
m1 = pd.read_parquet(Path(sys.argv[1]) / "eurusd.parquet").sort_values("ts").set_index("ts")
m1.index = m1.index.tz_convert(None)
m1 = m1[~m1.index.duplicated()].loc[:"2022-12-31 23:59"]  # development + warm-up; holdout never loaded


def bars(rule):
    if rule == "1D":
        ny = m1.index.tz_localize("UTC").tz_convert(NY)
        key = (ny - pd.Timedelta(hours=17)).normalize().tz_localize(None)
        b = m1.groupby(key).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"), last=("close", lambda s: s.index[-1]))
        b.index = b["last"] + pd.Timedelta(minutes=1)
        return b.drop(columns="last")
    b = m1.resample(rule, label="left", closed="left").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    b.index = b.index + pd.Timedelta(rule)
    return b


def atr(b, n=14):
    pc = b["close"].shift(1)
    return pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1).ewm(alpha=1 / n).mean()


m15, h1, d1, m5 = bars("15min"), bars("1h"), bars("1D"), bars("5min")
ev = m15.index  # evaluate at M15 closes
px = m1["close"].asof(ev - pd.Timedelta(minutes=1)).to_numpy()  # last M1 close = the M15 close


def asof(s):
    return s.reindex(ev, method="ffill").to_numpy() if s.index.equals(ev) else s.asof(ev).to_numpy()


e20, e50, e200 = (asof(d1["close"].ewm(span=n).mean()) for n in (20, 50, 200))
d1_atr = asof(atr(d1))
f = pd.DataFrame(index=ev)
f["bear"] = (e20 < e50) & (e50 < e200) & (px < e50)
for nm, b in (("h1", h1), ("m15", m15), ("m5", m5)):
    mid, sd = asof(b["close"].rolling(20).mean()), asof(b["close"].rolling(20).std())
    f[f"{nm}_pctb"] = (px - (mid - 2 * sd)) / (4 * sd)
f["d1_dist_atr"] = (e50 - px) / d1_atr  # how far below the D1 EMA50 (trend strength)
f["d1_stack_gap_atr"] = (e50 - e20) / d1_atr
f["h1_atr_pips"] = asof(atr(h1)) / PIP
c = m1["close"]
for n in (60, 240, 1440):
    f[f"ret{n}"] = (px - c.asof(ev - pd.Timedelta(minutes=n + 1)).to_numpy()) / PIP
f["hour_ny"] = ev.tz_localize("UTC").tz_convert(NY).hour
f["spread"] = m1["spread"].asof(ev - pd.Timedelta(minutes=1)).to_numpy()
for h in (4, 8):
    fut = c.asof(ev + pd.Timedelta(hours=h)).to_numpy()
    ok = (ev + pd.Timedelta(hours=h)) <= m1.index[-1]
    f[f"fwd{h}"] = np.where(ok, (px - fut) / PIP, np.nan)  # sell-signed
f["year"] = ev.year
S = f[f["bear"] & (f["h1_pctb"] >= 0.5) & (f["m15_pctb"] >= 0.5) & (f.index >= "2019-01-01")].dropna(subset=["fwd4", "fwd8"])
# one event per 4 h at most, to keep overlapping outcomes from multiplying the count
keep, last = [], pd.Timestamp("1900-01-01")
for t in S.index:
    if t - last >= pd.Timedelta(hours=4):
        keep.append(t)
        last = t
S = S.loc[keep]
print(f"setup events (>= 4 h apart), development 2019-22: n={len(S)}; fwd4 {S.fwd4.mean():+.2f} fwd8 {S.fwd8.mean():+.2f}")


def show(col, q=5, labels=None):
    try:
        cut = pd.qcut(S[col], q, duplicates="drop") if labels is None else S[col].map(labels)
    except ValueError:
        return
    print(f"\n-- {col}")
    for k, g in S.groupby(cut, observed=True):
        yrs = g.groupby("year")["fwd8"].mean()
        print(f"   {str(k):28s} n={len(g):4d}  fwd4 {g.fwd4.mean():+5.2f}±{g.fwd4.std() / math.sqrt(len(g)):.2f}  "
              f"fwd8 {g.fwd8.mean():+5.2f}±{g.fwd8.std() / math.sqrt(len(g)):.2f}  years(fwd8): "
              + " ".join(f"{y % 100}:{v:+.1f}" for y, v in yrs.items()))


for col in ("h1_pctb", "m15_pctb", "m5_pctb", "d1_dist_atr", "d1_stack_gap_atr", "h1_atr_pips", "ret60", "ret240", "ret1440", "spread"):
    show(col)
sess = {h: ("Asia 18-03" if (h >= 18 or h < 3) else "London 03-08" if h < 8 else "NY am 08-12" if h < 12 else "NY pm 12-18") for h in range(24)}
show("hour_ny", labels=sess)
